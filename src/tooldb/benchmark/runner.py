"""Benchmark runner for tools and recipes.

Three modes:
- deterministic: shell command or Python predicate returning pass/fail + score
- llm_judge: rubric string, Claude scores output (MCP-only, needs injected callable)
- eyeball: outputs surfaced for user review, score=0
"""

from __future__ import annotations

import asyncio
import subprocess
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from tooldb.benchmark.specs import fixture_content_hash, validate_spec
from tooldb.logging import logger
from tooldb.models import BenchmarkResult, BenchmarkSpec, Recipe, RecipeStep, Tool


class BenchmarkError(Exception):
    """Raised when a benchmark cannot be executed."""


class BenchmarkRunner:
    """Execute benchmarks against tools or recipes."""

    def __init__(
        self,
        llm_call: Callable[[str], Awaitable[str]] | None = None,
    ) -> None:
        """Initialize runner.

        Args:
            llm_call: Async callable for LLM judge mode. When None, LLM judge
                      benchmarks will raise BenchmarkError.
        """
        self._llm_call = llm_call

    async def run_tool(
        self,
        spec: BenchmarkSpec,
        tool: Tool,
    ) -> BenchmarkResult:
        """Run a benchmark against a single tool."""
        errors = validate_spec(spec)
        if errors:
            raise BenchmarkError(f"Invalid spec: {'; '.join(errors)}")

        fixture_hash = fixture_content_hash(spec.fixture_path)
        timeout_s = float(spec.budget.get("timeout_s", 30))  # type: ignore[arg-type]

        if spec.criteria_type == "deterministic":
            score = await self._run_deterministic(spec, timeout_s)
        elif spec.criteria_type == "llm_judge":
            score = await self._run_llm_judge(spec, tool)
        elif spec.criteria_type == "eyeball":
            score = 0.0  # eyeball mode doesn't auto-score
            logger.info(
                "Eyeball benchmark for tool %s — review output manually",
                tool.name,
            )
        else:
            raise BenchmarkError(f"Unknown criteria_type: {spec.criteria_type}")

        return BenchmarkResult(
            task_type=spec.task_type,
            score=score,
            ran_at=datetime.now(UTC).replace(tzinfo=None),
            fixture_hash=fixture_hash,
        )

    async def run_recipe(
        self,
        spec: BenchmarkSpec,
        recipe: Recipe,
        get_tool: Callable[[int], Tool | None],
    ) -> BenchmarkResult:
        """Run a benchmark against a recipe pipeline.

        Walks steps in order, piping output to next step's input.
        Final output is evaluated against the spec criteria.

        Args:
            get_tool: Callable to look up tools by ID (e.g. cache.get).
        """
        errors = validate_spec(spec)
        if errors:
            raise BenchmarkError(f"Invalid spec: {'; '.join(errors)}")

        fixture_hash = fixture_content_hash(spec.fixture_path)
        per_step_timeout = float(spec.budget.get("per_step_timeout_s", 30))  # type: ignore[arg-type]

        current_output: Any = None

        for i, step in enumerate(recipe.steps):
            tool = get_tool(step.tool_id)
            if tool is None:
                raise BenchmarkError(
                    f"Recipe step {i}: tool_id={step.tool_id} not found"
                )

            try:
                step_result = await asyncio.wait_for(
                    self._execute_step(step, tool, current_output),
                    timeout=per_step_timeout,
                )
                current_output = step_result
            except TimeoutError as e:
                raise BenchmarkError(
                    f"Recipe step {i} ({tool.name}) timed out after {per_step_timeout}s"
                ) from e
            except Exception as e:
                raise BenchmarkError(
                    f"Recipe step {i} ({tool.name}) failed: {e}"
                ) from e

        # Evaluate final output
        score = 1.0  # pipeline completed successfully
        return BenchmarkResult(
            task_type=spec.task_type,
            score=score,
            ran_at=datetime.now(UTC).replace(tzinfo=None),
            fixture_hash=fixture_hash,
        )

    async def _run_deterministic(
        self, spec: BenchmarkSpec, timeout_s: float
    ) -> float:
        """Run a deterministic benchmark (shell command or predicate)."""
        command = spec.criteria_spec.get("command")
        if not command:
            raise BenchmarkError("deterministic spec missing 'command'")

        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(
                    subprocess.run,
                    str(command),
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=timeout_s,
                ),
                timeout=timeout_s + 5,
            )
            # Coerce return code to score: 0 = pass (1.0), non-zero = fail (0.0)
            passed = result.returncode == 0
            if not isinstance(passed, bool):
                logger.warning("Deterministic predicate returned non-bool, coercing")
            return 1.0 if passed else 0.0
        except (TimeoutError, subprocess.TimeoutExpired) as e:
            raise BenchmarkError(
                f"Deterministic benchmark timed out after {timeout_s}s"
            ) from e

    async def _run_llm_judge(self, spec: BenchmarkSpec, tool: Tool) -> float:
        """Run an LLM judge benchmark. Requires injected llm_call."""
        if self._llm_call is None:
            raise BenchmarkError(
                "LLM judge mode requires an llm_call callable "
                "(only available via MCP, not CLI)"
            )

        rubric = spec.criteria_spec.get("rubric", "")
        prompt = (
            f"Score the tool '{tool.name}' ({tool.url}) for the task "
            f"'{spec.task_type}' on a scale of 0.0 to 1.0.\n\n"
            f"Rubric: {rubric}\n\n"
            f"Respond with ONLY a number between 0.0 and 1.0."
        )

        response = await self._llm_call(prompt)

        try:
            score = float(response.strip())
        except ValueError:
            logger.warning("LLM judge returned non-numeric response: %s", response)
            score = 0.0

        # Clamp to valid range
        if score < 0.0 or score > 1.0:
            logger.warning("LLM judge score %.2f outside [0,1], clamping", score)
            score = max(0.0, min(1.0, score))

        return score

    async def _execute_step(
        self,
        step: RecipeStep,
        tool: Tool,
        previous_output: Any,
    ) -> Any:
        """Execute a single recipe step. Placeholder for actual tool invocation."""
        # In v1, recipe benchmark just validates the pipeline structure
        # and that all tools are reachable. Actual invocation comes later
        # via the invoker module.
        return {
            "tool": tool.name,
            "status": "simulated",
            "input": previous_output,
            "params": step.params,
        }
