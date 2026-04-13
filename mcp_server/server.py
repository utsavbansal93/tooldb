"""FastMCP server for ToolDB.

11 tools derived from the Skill decision tree in skills/tool_search.md.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from tooldb.adapters.registry import extract_metadata_from_readme
from tooldb.benchmark.runner import BenchmarkRunner
from tooldb.cascade import Cascade
from tooldb.db.cache import ToolCache
from tooldb.invoker import ToolInvoker
from tooldb.logging import setup_logging
from tooldb.models import BenchmarkSpec, Recipe, RecipeStep

setup_logging()

DEFAULT_DB_PATH = Path.home() / ".tooldb" / "tooldb.sqlite"

mcp = FastMCP("ToolDB", instructions="Personal tool discovery, caching, and invocation system.")

# Shared state — initialized lazily
_cache: ToolCache | None = None
_llm_call: Any = None


def _get_cache() -> ToolCache:
    global _cache
    if _cache is None:
        DEFAULT_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _cache = ToolCache(DEFAULT_DB_PATH)
    return _cache


# ──────────────────── 1. find_tool ────────────────────


@mcp.tool()
async def find_tool(
    task: str,
    max_layer: int = 4,
    bypass_negative_cache: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Search for tools matching a task. Cascades through cache → GitHub → APIs → web.

    Args:
        task: Natural language task description (e.g., "markdown to PDF converter").
        max_layer: Max cascade layer (1=cache-works, 2=cache-untried, 3=github+apis, 4=web).
        bypass_negative_cache: Ignore previous "no results" cache entries.
        dry_run: Show what would happen without calling external APIs.

    Returns:
        Dict with tools, recipes, layer_reached, negative_cached, and source_timings.
    """
    cache = _get_cache()
    cascade = Cascade(cache)
    result = await cascade.find(
        task,
        max_layer=max_layer,
        bypass_negative_cache=bypass_negative_cache,
        dry_run=dry_run,
    )
    return {
        "tools": [
            {
                "id": t.id,
                "name": t.name,
                "url": t.url,
                "type": t.type,
                "status": t.my_status,
                "tags": t.task_tags,
                "source": t.source,
            }
            for t in result.tools
        ],
        "recipes": [
            {
                "id": r.id,
                "name": r.name,
                "description": r.description,
                "steps": r.step_count,
                "status": r.my_status,
            }
            for r in result.recipes
        ],
        "layer_reached": result.layer_reached,
        "negative_cached": result.negative_cached,
        "source_timings": result.source_timings,
    }


# ──────────────────── 2. record_experience ────────────────────


@mcp.tool()
async def record_experience(
    tool_id: int,
    status: str,
    notes: str | None = None,
) -> dict[str, str]:
    """Record your experience with a tool after using it.

    Args:
        tool_id: The tool's ID.
        status: One of: works, degraded, broken, avoid.
        notes: Optional notes about the experience.
    """
    cache = _get_cache()
    tool = cache.get(tool_id)
    if tool is None:
        return {"error": f"Tool {tool_id} not found"}

    cache.update_status(tool_id, status, notes)
    return {"status": "ok", "tool": tool.name, "new_status": status}


# ──────────────────── 3. list_my_tools ────────────────────


@mcp.tool()
async def list_my_tools(status: str | None = None) -> list[dict[str, Any]]:
    """List your cached tools, optionally filtered by status.

    Args:
        status: Filter by status (works/untried/degraded/broken/avoid). None for all.
    """
    cache = _get_cache()
    tools = cache.list_tools(status)
    return [
        {
            "id": t.id,
            "name": t.name,
            "url": t.url,
            "status": t.my_status,
            "tags": t.task_tags,
            "invocations": t.invocation_count,
        }
        for t in tools
    ]


# ──────────────────── 4. run_benchmark ────────────────────


@mcp.tool()
async def run_benchmark(
    target_id: int,
    task_type: str,
    target_type: str = "tool",
) -> dict[str, Any]:
    """Run a benchmark (deterministic, LLM judge, or eyeball) against a tool or recipe.

    Args:
        target_id: Tool or recipe ID to benchmark.
        task_type: Type of task being benchmarked (e.g., "pdf_conversion").
        target_type: "tool" or "recipe".
    """
    cache = _get_cache()
    runner = BenchmarkRunner(llm_call=_llm_call)

    spec = BenchmarkSpec(
        task_type=task_type,
        target_type=target_type,  # type: ignore[arg-type]
        target_id=target_id,
        fixture_path="/dev/null",
        criteria_type="eyeball",
        criteria_spec={},
        budget={},
    )

    if target_type == "tool":
        tool = cache.get(target_id)
        if tool is None:
            return {"error": f"Tool {target_id} not found"}
        result = await runner.run_tool(spec, tool)
    else:
        recipe = cache.get_recipe(target_id)
        if recipe is None:
            return {"error": f"Recipe {target_id} not found"}
        result = await runner.run_recipe(spec, recipe, cache.get)

    return {
        "task_type": result.task_type,
        "score": result.score,
        "ran_at": result.ran_at.isoformat(),
        "fixture_hash": result.fixture_hash,
    }


# ──────────────────── 5. invoke_tool ────────────────────


@mcp.tool()
async def invoke_tool(
    tool_id: int,
    inputs: dict[str, Any] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Invoke a tool with rate limiting and wrapper dispatch.

    Args:
        tool_id: Tool ID to invoke.
        inputs: Dict of input key=value pairs.
        dry_run: Preview command without executing.
    """
    cache = _get_cache()
    invoker = ToolInvoker(cache)
    return await invoker.invoke(tool_id, inputs or {}, dry_run=dry_run)


# ──────────────────── 6. extract_tool_metadata ────────────────────


@mcp.tool()
async def extract_tool_metadata(
    tool_id: int,
    readme_url: str,
) -> dict[str, Any]:
    """Extract structured metadata from a tool's README using LLM (MCP-only).

    Args:
        tool_id: Tool ID to update with extracted metadata.
        readme_url: URL to the tool's README file.
    """
    import httpx

    cache = _get_cache()
    tool = cache.get(tool_id)
    if tool is None:
        return {"error": f"Tool {tool_id} not found"}

    # Fetch README
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            resp = await client.get(readme_url)
            readme_content = resp.text
        except httpx.HTTPError as e:
            return {"error": f"Failed to fetch README: {e}"}

    try:
        metadata = await extract_metadata_from_readme(readme_content, llm_call=_llm_call)
    except Exception as e:
        return {"error": f"Extraction failed: {e}"}

    # Update tool with extracted metadata
    if metadata.install_cmd:
        tool.install_cmd = metadata.install_cmd
    if metadata.invocation_template:
        tool.invocation_template = metadata.invocation_template
    if metadata.auth_method:
        tool.auth_method = metadata.auth_method
    if metadata.auth_env_var:
        tool.auth_env_var = metadata.auth_env_var
    if metadata.rate_limit_per_hour is not None:
        tool.rate_limit_per_hour = metadata.rate_limit_per_hour
    if metadata.rate_limit_per_sec is not None:
        tool.rate_limit_per_sec = metadata.rate_limit_per_sec
    if metadata.cost_tier:
        tool.cost_tier = metadata.cost_tier  # type: ignore[assignment]
    if metadata.task_tags:
        # Merge tags
        existing = set(tool.task_tags)
        for tag in metadata.task_tags:
            if tag not in existing:
                tool.task_tags.append(tag)
    tool.readme_extracted_at = datetime.now(UTC)

    cache.upsert(tool)
    return {"status": "ok", "tool": tool.name, "extracted_fields": _count_extracted(metadata)}


def _count_extracted(m: Any) -> int:
    """Count how many fields were actually extracted."""
    count = 0
    for field in [
        "install_cmd", "invocation_template", "auth_method", "auth_env_var",
        "rate_limit_per_hour", "rate_limit_per_sec",
    ]:
        if getattr(m, field, None) is not None:
            count += 1
    if m.task_tags:
        count += 1
    return count


# ──────────────────── 7. delete_tool ────────────────────


@mcp.tool()
async def delete_tool(tool_id: int) -> dict[str, Any]:
    """Delete a tool from your cache.

    Args:
        tool_id: Tool ID to delete.
    """
    cache = _get_cache()
    if cache.delete(tool_id):
        return {"status": "ok", "deleted": tool_id}
    return {"error": f"Tool {tool_id} not found"}


# ──────────────────── 8. find_recipes ────────────────────


@mcp.tool()
async def find_recipes(task: str) -> list[dict[str, Any]]:
    """Search for existing recipes matching a task.

    Args:
        task: Natural language task description.
    """
    cache = _get_cache()
    recipes = cache.find_recipes_by_task(task)
    return [
        {
            "id": r.id,
            "name": r.name,
            "description": r.description,
            "steps": r.step_count,
            "status": r.my_status,
        }
        for r in recipes
    ]


# ──────────────────── 9. suggest_recipe ────────────────────


@mcp.tool()
async def suggest_recipe(task: str) -> dict[str, Any]:
    """Propose a new recipe by chaining cached tools for a pipeline task.

    Does NOT save the recipe — use save_recipe to persist after review.

    Args:
        task: Pipeline task description (e.g., "convert markdown to PDF then upload to S3").
    """
    cache = _get_cache()
    # Find tools that match parts of the task
    tools = cache.find_by_task(task)

    if not tools:
        return {
            "status": "no_tools",
            "message": "No cached tools match this task. Run find_tool first.",
        }

    # Build a suggested recipe from top matches
    steps = []
    for i, tool in enumerate(tools[:5]):  # max 5 steps
        steps.append({
            "step": i + 1,
            "tool_id": tool.id,
            "tool_name": tool.name,
            "tool_url": tool.url,
            "params": {},
            "output_to_next_input": "text" if i < len(tools[:5]) - 1 else None,
        })

    return {
        "status": "suggested",
        "name": f"recipe-for-{task[:40].replace(' ', '-')}",
        "description": f"Auto-suggested recipe for: {task}",
        "steps": steps,
        "message": "Review this recipe and call save_recipe to persist it.",
    }


# ──────────────────── 10. save_recipe ────────────────────


@mcp.tool()
async def save_recipe(
    name: str,
    description: str,
    steps: list[dict[str, Any]],
) -> dict[str, Any]:
    """Save a recipe (new or edited from suggest_recipe).

    Args:
        name: Recipe name.
        description: What this recipe does.
        steps: List of step dicts with tool_id, params, output_to_next_input.
    """
    cache = _get_cache()
    recipe_steps = [
        RecipeStep(
            tool_id=int(s["tool_id"]),
            params=s.get("params", {}),
            output_to_next_input=s.get("output_to_next_input"),
        )
        for s in steps
    ]

    recipe = Recipe(
        name=name,
        description=description,
        steps=recipe_steps,
        step_count=len(recipe_steps),
    )

    try:
        saved = cache.create_recipe(recipe)
        return {
            "status": "ok",
            "id": saved.id,
            "name": saved.name,
            "steps": saved.step_count,
        }
    except ValueError as e:
        return {"error": str(e)}


# ──────────────────── 11. get_stats ────────────────────


@mcp.tool()
async def get_stats() -> dict[str, Any]:
    """Get cache statistics: tool counts, most-used, stale entries, negative cache size."""
    cache = _get_cache()
    return cache.get_stats()


def main() -> None:
    """Entry point for tooldb-mcp script."""
    mcp.run()


if __name__ == "__main__":
    main()
