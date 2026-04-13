"""Property-based tests using Hypothesis.

Tests invariants that should hold for ALL inputs, not just hand-picked examples.
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from tooldb.db.cache import ToolCache
from tooldb.models import Tool, task_signature, tokenize_task

# ──────────────────── Strategies ────────────────────


def tool_strategy() -> st.SearchStrategy[Tool]:
    """Generate valid Tool instances for property testing."""
    return st.builds(
        Tool,
        name=st.text(min_size=1, max_size=100),
        url=st.sampled_from(
            [
                "https://github.com/example/tool-1",
                "https://github.com/example/tool-2",
                "https://github.com/example/tool-3",
                "https://api.example.com/v1",
                "https://example.com/cli-tool",
            ]
        ),
        type=st.sampled_from(["repo", "api", "service", "cli"]),
        task_tags=st.lists(st.text(min_size=1, max_size=30), max_size=5),
        source=st.sampled_from(["manual", "github", "public_apis", "web"]),
        cost_tier=st.sampled_from(["free", "freemium", "paid", "unknown"]),
        my_status=st.sampled_from(["untried", "works", "degraded"]),
    )


# ──────────────────── Token split ────────────────────


@given(st.text())
@settings(max_examples=200)
def test_find_by_task_never_crashes(task: str) -> None:
    """find_by_task must never raise, regardless of input."""
    cache = ToolCache(":memory:")
    result = cache.find_by_task(task)
    assert isinstance(result, list)
    cache.close()


@given(st.lists(st.text(min_size=1, max_size=50), min_size=1, max_size=20))
@settings(max_examples=100)
def test_token_split_is_deterministic(tokens: list[str]) -> None:
    """Same input should always produce same tokens."""
    task = " ".join(tokens)
    assert tokenize_task(task) == tokenize_task(task)


@given(st.text(max_size=200))
@settings(max_examples=100)
def test_tokenize_returns_list_of_strings(task: str) -> None:
    """Tokenizer always returns a list of strings."""
    result = tokenize_task(task)
    assert isinstance(result, list)
    for token in result:
        assert isinstance(token, str)
        assert len(token) >= 2  # min token length enforced


# ──────────────────── Task signature ────────────────────


@given(st.text())
@settings(max_examples=200)
def test_task_signature_is_stable(task: str) -> None:
    """Same input always produces same hash."""
    assert task_signature(task) == task_signature(task)


@given(st.text(min_size=1))
@settings(max_examples=100)
def test_task_signature_case_insensitive(task: str) -> None:
    """Hash should be the same regardless of case."""
    assert task_signature(task) == task_signature(task.upper())
    assert task_signature(task) == task_signature(task.lower())


@given(st.text(), st.text())
@settings(max_examples=100)
def test_task_signature_different_inputs(a: str, b: str) -> None:
    """Different normalized inputs should (almost certainly) produce different hashes."""
    if a.strip().casefold() != b.strip().casefold():
        assert task_signature(a) != task_signature(b)


# ──────────────────── Upsert roundtrip ────────────────────


@given(tool=tool_strategy())
@settings(max_examples=50)
def test_upsert_roundtrip(tool: Tool) -> None:
    """Upsert then get should return equivalent data."""
    cache = ToolCache(":memory:")
    saved = cache.upsert(tool)
    retrieved = cache.get(saved.id)  # type: ignore[arg-type]

    assert retrieved is not None
    assert retrieved.name == saved.name
    assert retrieved.url == saved.url
    assert retrieved.type == saved.type
    assert retrieved.task_tags == saved.task_tags
    assert retrieved.source == saved.source
    assert retrieved.cost_tier == saved.cost_tier
    assert retrieved.my_status == saved.my_status
    cache.close()
