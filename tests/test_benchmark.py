"""Tests for benchmark specs and runner.

Covers deterministic, eyeball, LLM judge modes + recipe benchmarks.
"""

from __future__ import annotations

import tempfile

import pytest

from tooldb.benchmark.runner import BenchmarkError, BenchmarkRunner
from tooldb.benchmark.specs import fixture_content_hash, validate_spec
from tooldb.db.cache import ToolCache
from tooldb.models import BenchmarkSpec, Recipe, RecipeStep

from .conftest import make_tool

# ──────────────────── Spec validation ────────────────────


class TestSpecValidation:
    def test_valid_deterministic_spec(self) -> None:
        spec = BenchmarkSpec(
            task_type="pdf_convert",
            target_type="tool",
            target_id=1,
            fixture_path="/tmp/test.txt",
            criteria_type="deterministic",
            criteria_spec={"command": "echo ok"},
            budget={"timeout_s": 10},
        )
        assert validate_spec(spec) == []

    def test_deterministic_missing_command(self) -> None:
        spec = BenchmarkSpec(
            task_type="test",
            target_type="tool",
            target_id=1,
            fixture_path="/tmp/test.txt",
            criteria_type="deterministic",
            criteria_spec={},
            budget={},
        )
        errors = validate_spec(spec)
        assert any("command" in e for e in errors)

    def test_llm_judge_missing_rubric(self) -> None:
        spec = BenchmarkSpec(
            task_type="test",
            target_type="tool",
            target_id=1,
            fixture_path="/tmp/test.txt",
            criteria_type="llm_judge",
            criteria_spec={},
            budget={},
        )
        errors = validate_spec(spec)
        assert any("rubric" in e for e in errors)


# ──────────────────── Fixture hash ────────────────────


class TestFixtureHash:
    def test_same_content_same_hash(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("test content")
            f.flush()
            h1 = fixture_content_hash(f.name)
            h2 = fixture_content_hash(f.name)
            assert h1 == h2

    def test_different_content_different_hash(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f1:
            f1.write("content A")
            f1.flush()
            with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f2:
                f2.write("content B")
                f2.flush()
                assert fixture_content_hash(f1.name) != fixture_content_hash(f2.name)

    def test_nonexistent_file_returns_path_hash(self) -> None:
        h = fixture_content_hash("/nonexistent/file.txt")
        assert isinstance(h, str) and len(h) == 64


# ──────────────────── Deterministic runner ────────────────────


class TestDeterministicBenchmark:
    @pytest.mark.asyncio
    async def test_passing_command(self) -> None:
        runner = BenchmarkRunner()
        spec = BenchmarkSpec(
            task_type="test",
            target_type="tool",
            target_id=1,
            fixture_path="/tmp/test.txt",
            criteria_type="deterministic",
            criteria_spec={"command": "true"},
            budget={"timeout_s": 5},
        )
        tool = make_tool()
        result = await runner.run_tool(spec, tool)
        assert result.score == 1.0
        assert result.task_type == "test"

    @pytest.mark.asyncio
    async def test_failing_command(self) -> None:
        runner = BenchmarkRunner()
        spec = BenchmarkSpec(
            task_type="test",
            target_type="tool",
            target_id=1,
            fixture_path="/tmp/test.txt",
            criteria_type="deterministic",
            criteria_spec={"command": "false"},
            budget={"timeout_s": 5},
        )
        result = await runner.run_tool(spec, make_tool())
        assert result.score == 0.0

    @pytest.mark.asyncio
    async def test_timeout_raises(self) -> None:
        runner = BenchmarkRunner()
        spec = BenchmarkSpec(
            task_type="test",
            target_type="tool",
            target_id=1,
            fixture_path="/tmp/test.txt",
            criteria_type="deterministic",
            criteria_spec={"command": "sleep 60"},
            budget={"timeout_s": 0.1},
        )
        with pytest.raises(BenchmarkError, match="timed out"):
            await runner.run_tool(spec, make_tool())


# ──────────────────── Eyeball mode ────────────────────


class TestEyeballBenchmark:
    @pytest.mark.asyncio
    async def test_returns_score_zero(self) -> None:
        runner = BenchmarkRunner()
        spec = BenchmarkSpec(
            task_type="visual_check",
            target_type="tool",
            target_id=1,
            fixture_path="/tmp/test.txt",
            criteria_type="eyeball",
            criteria_spec={},
            budget={},
        )
        result = await runner.run_tool(spec, make_tool())
        assert result.score == 0.0


# ──────────────────── LLM judge mode ────────────────────


class TestLLMJudge:
    @pytest.mark.asyncio
    async def test_no_llm_callable_raises(self) -> None:
        runner = BenchmarkRunner(llm_call=None)
        spec = BenchmarkSpec(
            task_type="quality",
            target_type="tool",
            target_id=1,
            fixture_path="/tmp/test.txt",
            criteria_type="llm_judge",
            criteria_spec={"rubric": "Is this tool good?"},
            budget={},
        )
        with pytest.raises(BenchmarkError, match="llm_call callable"):
            await runner.run_tool(spec, make_tool())

    @pytest.mark.asyncio
    async def test_with_mock_llm(self) -> None:
        async def mock_llm(prompt: str) -> str:
            return "0.85"

        runner = BenchmarkRunner(llm_call=mock_llm)
        spec = BenchmarkSpec(
            task_type="quality",
            target_type="tool",
            target_id=1,
            fixture_path="/tmp/test.txt",
            criteria_type="llm_judge",
            criteria_spec={"rubric": "Rate tool quality"},
            budget={},
        )
        result = await runner.run_tool(spec, make_tool())
        assert result.score == 0.85

    @pytest.mark.asyncio
    async def test_score_out_of_range_clamped(self) -> None:
        async def mock_llm(prompt: str) -> str:
            return "1.5"

        runner = BenchmarkRunner(llm_call=mock_llm)
        spec = BenchmarkSpec(
            task_type="quality",
            target_type="tool",
            target_id=1,
            fixture_path="/tmp/test.txt",
            criteria_type="llm_judge",
            criteria_spec={"rubric": "test"},
            budget={},
        )
        result = await runner.run_tool(spec, make_tool())
        assert result.score == 1.0  # clamped

    @pytest.mark.asyncio
    async def test_non_numeric_response(self) -> None:
        async def mock_llm(prompt: str) -> str:
            return "This tool is great!"

        runner = BenchmarkRunner(llm_call=mock_llm)
        spec = BenchmarkSpec(
            task_type="quality",
            target_type="tool",
            target_id=1,
            fixture_path="/tmp/test.txt",
            criteria_type="llm_judge",
            criteria_spec={"rubric": "test"},
            budget={},
        )
        result = await runner.run_tool(spec, make_tool())
        assert result.score == 0.0  # couldn't parse


# ──────────────────── Recipe benchmarks ────────────────────


class TestRecipeBenchmark:
    @pytest.mark.asyncio
    async def test_walks_steps_in_order(self) -> None:
        cache = ToolCache(":memory:")
        t1 = cache.upsert(make_tool(name="step1", url="https://a.com"))
        t2 = cache.upsert(make_tool(name="step2", url="https://b.com"))

        recipe = Recipe(
            name="pipeline",
            description="test pipeline",
            steps=[
                RecipeStep(tool_id=t1.id, params={"fmt": "pdf"}),  # type: ignore[arg-type]
                RecipeStep(tool_id=t2.id, params={"fmt": "epub"}),  # type: ignore[arg-type]
            ],
            step_count=2,
        )

        spec = BenchmarkSpec(
            task_type="pipeline_test",
            target_type="recipe",
            target_id=1,
            fixture_path="/tmp/test.txt",
            criteria_type="deterministic",
            criteria_spec={"command": "true"},
            budget={"per_step_timeout_s": 5},
        )

        runner = BenchmarkRunner()
        result = await runner.run_recipe(spec, recipe, cache.get)
        assert result.score == 1.0
        assert result.task_type == "pipeline_test"

    @pytest.mark.asyncio
    async def test_missing_tool_fails(self) -> None:
        recipe = Recipe(
            name="broken",
            description="missing tool",
            steps=[RecipeStep(tool_id=99999, params={})],
            step_count=1,
        )

        spec = BenchmarkSpec(
            task_type="test",
            target_type="recipe",
            target_id=1,
            fixture_path="/tmp/test.txt",
            criteria_type="deterministic",
            criteria_spec={"command": "true"},
            budget={},
        )

        cache = ToolCache(":memory:")
        runner = BenchmarkRunner()
        with pytest.raises(BenchmarkError, match="not found"):
            await runner.run_recipe(spec, recipe, cache.get)


# ──────────────────── Score distinguishability ────────────────────


class TestScoreDistinguishability:
    @pytest.mark.asyncio
    async def test_zero_score_vs_not_run(self) -> None:
        """Benchmark with score=0 must be distinguishable from 'not run'."""
        runner = BenchmarkRunner()
        spec = BenchmarkSpec(
            task_type="visual",
            target_type="tool",
            target_id=1,
            fixture_path="/tmp/test.txt",
            criteria_type="eyeball",
            criteria_spec={},
            budget={},
        )
        result = await runner.run_tool(spec, make_tool())
        # score=0 but ran_at is set → it ran
        assert result.score == 0.0
        assert result.ran_at is not None
        # A tool with empty benchmark_results means NOT run at all
        tool = make_tool()
        assert tool.benchmark_results == []  # not run
