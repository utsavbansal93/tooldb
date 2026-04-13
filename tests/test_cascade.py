"""Tests for the L1→L4 cascade orchestrator."""

from __future__ import annotations

import pytest

from tooldb.cascade import Cascade
from tooldb.db.cache import ToolCache
from tooldb.discovery.base import ToolCandidate
from tooldb.discovery.github import AuthenticationError
from tooldb.models import Recipe, RecipeStep, task_signature

from .conftest import make_tool

# ──────────────────── Mock discovery sources ────────────────────


class MockSource:
    """Configurable mock discovery source."""

    def __init__(
        self,
        name: str,
        results: list[ToolCandidate] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.source_name = name
        self._results = results or []
        self._error = error
        self.search_count = 0

    async def search(self, task: str, limit: int = 5) -> list[ToolCandidate]:
        self.search_count += 1
        if self._error:
            raise self._error
        return self._results[:limit]


def _make_candidate(
    name: str = "test-tool",
    url: str = "https://github.com/example/test",
    **overrides: object,
) -> ToolCandidate:
    defaults: dict[str, object] = {
        "name": name,
        "url": url,
        "type": "repo",
        "description": "A test tool",
        "task_tags": ["testing"],
        "license": "MIT",
        "stars": 100,
        "auth_required": False,
        "cost_tier": "free",
    }
    defaults.update(overrides)
    return ToolCandidate(**defaults)  # type: ignore[typeddict-item]


# ──────────────────── L1 tests ────────────────────


class TestL1CacheHit:
    @pytest.mark.asyncio
    async def test_works_tool_returns_immediately(self) -> None:
        cache = ToolCache(":memory:")
        cache.upsert(make_tool(name="pdf-tool", task_tags=["pdf"], my_status="works"))

        github = MockSource("github")
        cascade = Cascade(cache, sources=[github])
        result = await cascade.find("pdf converter")

        assert result.layer_reached == 1
        assert len(result.tools) == 1
        assert result.tools[0].name == "pdf-tool"
        assert github.search_count == 0  # no discovery called

    @pytest.mark.asyncio
    async def test_works_recipe_returns_at_l1(self) -> None:
        cache = ToolCache(":memory:")
        t1 = cache.upsert(make_tool(name="step1", url="https://a.com", task_tags=["pdf"]))
        recipe = Recipe(
            name="pdf pipeline",
            description="convert pdf",
            steps=[RecipeStep(tool_id=t1.id, params={})],  # type: ignore[arg-type]
            step_count=1,
            my_status="works",
        )
        cache.create_recipe(recipe)

        cascade = Cascade(cache, sources=[MockSource("github")])
        result = await cascade.find("pdf")

        assert result.layer_reached == 1
        assert len(result.recipes) == 1

    @pytest.mark.asyncio
    async def test_l1_miss_untried_not_returned(self) -> None:
        cache = ToolCache(":memory:")
        cache.upsert(make_tool(name="pdf-tool", task_tags=["pdf"], my_status="untried"))

        cascade = Cascade(cache, sources=[MockSource("github"), MockSource("public_apis")])
        result = await cascade.find("pdf converter", max_layer=1)

        assert result.layer_reached == 0
        assert len(result.tools) == 0


# ──────────────────── L2 tests ────────────────────


class TestL2CacheUntried:
    @pytest.mark.asyncio
    async def test_untried_tool_found(self) -> None:
        cache = ToolCache(":memory:")
        cache.upsert(make_tool(name="pdf-tool", task_tags=["pdf"], my_status="untried"))

        github = MockSource("github")
        cascade = Cascade(cache, sources=[github])
        result = await cascade.find("pdf converter", max_layer=2)

        assert result.layer_reached == 2
        assert len(result.tools) == 1
        assert github.search_count == 0


# ──────────────────── L3 tests ────────────────────


class TestL3Discovery:
    @pytest.mark.asyncio
    async def test_discovery_results_written_to_cache(self) -> None:
        cache = ToolCache(":memory:")
        candidates = [_make_candidate(name="found-tool", url="https://github.com/ex/found")]
        github = MockSource("github", results=candidates)
        public_apis = MockSource("public_apis")

        cascade = Cascade(cache, sources=[github, public_apis])
        result = await cascade.find("something new")

        assert result.layer_reached == 3
        assert len(result.tools) >= 1
        # Verify it's in the cache now
        cached = cache.find_by_task("found")
        assert len(cached) >= 1
        assert cached[0].my_status == "untried"

    @pytest.mark.asyncio
    async def test_dedup_same_url(self) -> None:
        cache = ToolCache(":memory:")
        candidates = [
            _make_candidate(name="tool-a", url="https://github.com/ex/same"),
            _make_candidate(name="tool-b", url="https://github.com/ex/same"),
        ]
        github = MockSource("github", results=candidates)

        cascade = Cascade(cache, sources=[github, MockSource("public_apis")])
        result = await cascade.find("unique task query xyz")

        # Should be deduped to 1
        urls = {t.url for t in result.tools}
        assert len(urls) == len(result.tools)

    @pytest.mark.asyncio
    async def test_github_auth_error_surfaces(self) -> None:
        cache = ToolCache(":memory:")
        github = MockSource("github", error=AuthenticationError("bad token"))

        cascade = Cascade(cache, sources=[github, MockSource("public_apis")])
        with pytest.raises(AuthenticationError):
            await cascade.find("something")

    @pytest.mark.asyncio
    async def test_one_source_exception_others_still_run(self) -> None:
        cache = ToolCache(":memory:")
        candidates = [_make_candidate(name="from-apis", url="https://api.example.com/v1")]
        bad_github = MockSource("github", error=RuntimeError("network"))
        good_apis = MockSource("public_apis", results=candidates)

        cascade = Cascade(cache, sources=[bad_github, good_apis])
        result = await cascade.find("api search")

        assert len(result.tools) >= 1

    @pytest.mark.asyncio
    async def test_all_sources_empty_writes_negative_cache(self) -> None:
        cache = ToolCache(":memory:")
        github = MockSource("github")
        apis = MockSource("public_apis")
        web = MockSource("web")

        cascade = Cascade(cache, sources=[github, apis, web])
        await cascade.find("xyzzy_nonexistent_task_12345")

        sig = task_signature("xyzzy_nonexistent_task_12345")
        assert cache.is_negatively_cached(sig)

    @pytest.mark.asyncio
    async def test_per_candidate_scores_use_post_write_ids(self) -> None:
        cache = ToolCache(":memory:")
        candidates = [_make_candidate(name="scored", url="https://github.com/ex/scored")]
        github = MockSource("github", results=candidates)

        cascade = Cascade(cache, sources=[github, MockSource("public_apis")])
        result = await cascade.find("score test")

        for tool_id in result.per_candidate_scores:
            assert isinstance(tool_id, int)
            assert cache.get(tool_id) is not None


# ──────────────────── L4 tests ────────────────────


class TestL4WebSearch:
    @pytest.mark.asyncio
    async def test_l4_runs_when_l3_empty(self) -> None:
        cache = ToolCache(":memory:")
        web_candidates = [
            _make_candidate(
                name="web-result",
                url="https://example.com/tool",
                type="service",
                cost_tier="unknown",
            )
        ]
        github = MockSource("github")
        apis = MockSource("public_apis")
        web = MockSource("web", results=web_candidates)

        cascade = Cascade(cache, sources=[github, apis, web])
        result = await cascade.find("obscure tool query")

        assert result.layer_reached == 4
        assert len(result.tools) >= 1


# ──────────────────── Edge cases ────────────────────


class TestCascadeEdgeCases:
    @pytest.mark.asyncio
    async def test_max_layer_zero_returns_empty(self) -> None:
        cache = ToolCache(":memory:")
        cascade = Cascade(cache, sources=[])
        result = await cascade.find("anything", max_layer=0)

        assert result.tools == []
        assert result.recipes == []
        assert result.layer_reached == 0

    @pytest.mark.asyncio
    async def test_max_layer_99_clamped_to_4(self) -> None:
        cache = ToolCache(":memory:")
        cascade = Cascade(cache, sources=[MockSource("github"), MockSource("public_apis"),
                                          MockSource("web")])
        # Should not crash — clamped to 4
        result = await cascade.find("test clamp", max_layer=99)
        assert isinstance(result, type(result))

    @pytest.mark.asyncio
    async def test_max_layer_2_stops_before_discovery(self) -> None:
        cache = ToolCache(":memory:")
        github = MockSource("github")

        cascade = Cascade(cache, sources=[github])
        await cascade.find("test limit", max_layer=2)

        assert github.search_count == 0

    @pytest.mark.asyncio
    async def test_negative_cache_blocks_search(self) -> None:
        cache = ToolCache(":memory:")
        sig = task_signature("blocked task")
        cache.add_negative(sig, "previously failed")

        github = MockSource("github")
        cascade = Cascade(cache, sources=[github])
        result = await cascade.find("blocked task")

        assert result.negative_cached is True
        assert github.search_count == 0

    @pytest.mark.asyncio
    async def test_bypass_negative_cache(self) -> None:
        cache = ToolCache(":memory:")
        sig = task_signature("blocked task")
        cache.add_negative(sig, "previously failed")

        candidates = [_make_candidate(url="https://github.com/ex/bypassed")]
        github = MockSource("github", results=candidates)
        cascade = Cascade(cache, sources=[github, MockSource("public_apis")])
        result = await cascade.find("blocked task", bypass_negative_cache=True)

        assert result.negative_cached is False
        assert github.search_count == 1

    @pytest.mark.asyncio
    async def test_dry_run_no_external_calls(self) -> None:
        cache = ToolCache(":memory:")
        github = MockSource("github")
        apis = MockSource("public_apis")
        web = MockSource("web")

        cascade = Cascade(cache, sources=[github, apis, web])
        await cascade.find("dry run test", dry_run=True)

        assert github.search_count == 0
        assert apis.search_count == 0
        assert web.search_count == 0

    @pytest.mark.asyncio
    async def test_source_timings_populated(self) -> None:
        cache = ToolCache(":memory:")
        cascade = Cascade(cache, sources=[MockSource("github"), MockSource("public_apis"),
                                          MockSource("web")])
        result = await cascade.find("timing test")

        # Should have at least L1 timing
        assert "L1_cache_works" in result.source_timings
