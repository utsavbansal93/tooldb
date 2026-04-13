"""Invariant assertions called on every cache write.

Catches data corruption at the boundary, not three operations later
when you can't trace the cause.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tooldb.models import Recipe, Tool


class InvariantViolation(Exception):
    """Raised when a data invariant is violated on cache write."""


def assert_tool_invariants(tool: Tool) -> None:
    """Validate a Tool before writing to cache. Raises InvariantViolation."""
    _check(
        tool.url.startswith(("http://", "https://", "file://", "git+")),
        f"url must start with http://, https://, file://, or git+ — got {tool.url!r}",
    )

    _check(
        isinstance(tool.task_tags, list),
        f"task_tags must be a list — got {type(tool.task_tags).__name__}",
    )

    for tag in tool.task_tags:
        _check(
            isinstance(tag, str) and len(tag) > 0,
            f"task_tags must contain non-empty strings — got {tag!r}",
        )

    _check(
        tool.type in ("repo", "api", "service", "cli"),
        f"type must be repo|api|service|cli — got {tool.type!r}",
    )

    _check(
        tool.cost_tier in ("free", "freemium", "paid", "unknown"),
        f"cost_tier must be free|freemium|paid|unknown — got {tool.cost_tier!r}",
    )

    _check(
        tool.my_status in ("untried", "works", "degraded", "broken", "avoid"),
        f"my_status must be untried|works|degraded|broken|avoid — got {tool.my_status!r}",
    )

    _check(
        tool.source in ("cache", "github", "public_apis", "web", "manual"),
        f"source must be cache|github|public_apis|web|manual — got {tool.source!r}",
    )

    # If both rate limits are set, per_sec * 3600 should be >= per_hour
    if tool.rate_limit_per_sec is not None and tool.rate_limit_per_hour is not None:
        _check(
            tool.rate_limit_per_sec * 3600 >= tool.rate_limit_per_hour,
            f"rate_limit_per_sec ({tool.rate_limit_per_sec}) * 3600 "
            f"< rate_limit_per_hour ({tool.rate_limit_per_hour}) — inconsistent",
        )

    # Wrapper path, if set, should exist on disk unless tool is broken/avoid
    if tool.wrapper_path and tool.my_status not in ("broken", "avoid"):
        path = Path(tool.wrapper_path)
        _check(
            path.exists(),
            f"wrapper_path {tool.wrapper_path!r} does not exist "
            f"and status is {tool.my_status!r} (not broken/avoid)",
        )

    _check(
        tool.schema_version >= 1,
        f"schema_version must be >= 1 — got {tool.schema_version}",
    )


def assert_recipe_invariants(recipe: Recipe) -> None:
    """Validate a Recipe before writing to cache. Raises InvariantViolation."""
    _check(
        len(recipe.steps) > 0,
        "recipe must have at least one step",
    )

    _check(
        recipe.step_count == len(recipe.steps),
        f"step_count ({recipe.step_count}) != len(steps) ({len(recipe.steps)})",
    )

    _check(
        recipe.my_status in ("untried", "works", "degraded", "broken", "avoid"),
        f"my_status must be untried|works|degraded|broken|avoid — got {recipe.my_status!r}",
    )

    # Check for circular references: output_to_next_input should only feed forward
    # (step N's output feeds step N+1, not backwards)
    # This is structurally enforced by the list ordering — steps execute in order.
    # The only violation would be if output_to_next_input somehow references an earlier step,
    # but since steps is a flat list executed sequentially, this is already safe.
    # We just verify tool_ids are positive integers.
    for i, step in enumerate(recipe.steps):
        _check(
            isinstance(step.tool_id, int) and step.tool_id > 0,
            f"step {i}: tool_id must be a positive integer — got {step.tool_id!r}",
        )


def _check(condition: bool, message: str) -> None:
    if not condition:
        raise InvariantViolation(message)
