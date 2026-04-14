"""Click-based CLI for ToolDB.

All commands operate on ~/.tooldb/tooldb.sqlite by default.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import Any

import click

from tooldb.adapters.wrapper_generator import generate_wrapper
from tooldb.cascade import Cascade
from tooldb.db.cache import ToolCache
from tooldb.invoker import ToolInvoker
from tooldb.logging import setup_logging
from tooldb.models import Recipe, RecipeStep

DEFAULT_DB_DIR = Path.home() / ".tooldb"
DEFAULT_DB_PATH = DEFAULT_DB_DIR / "tooldb.sqlite"

VALID_STATUSES = ("untried", "works", "degraded", "broken", "avoid")


def _get_cache(db_path: str | None = None) -> ToolCache:
    """Get or create the ToolCache."""
    path = Path(db_path) if db_path else DEFAULT_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    return ToolCache(path)


def _run_async(coro: Any) -> Any:
    """Run an async coroutine from sync context."""
    return asyncio.run(coro)


def _output_json(data: Any) -> None:
    """Print JSON to stdout."""
    click.echo(json.dumps(data, indent=2, default=str))


def _tool_to_dict(tool: Any) -> dict[str, Any]:
    """Convert a Tool to a JSON-serializable dict."""
    return {
        "id": tool.id,
        "name": tool.name,
        "url": tool.url,
        "type": tool.type,
        "task_tags": tool.task_tags,
        "source": tool.source,
        "my_status": tool.my_status,
        "my_notes": tool.my_notes,
        "cost_tier": tool.cost_tier,
        "auth_required": tool.auth_required,
        "invocation_count": tool.invocation_count,
        "created_at": str(tool.created_at),
        "updated_at": str(tool.updated_at),
    }


def _recipe_to_dict(recipe: Any) -> dict[str, Any]:
    """Convert a Recipe to a JSON-serializable dict."""
    return {
        "id": recipe.id,
        "name": recipe.name,
        "description": recipe.description,
        "step_count": recipe.step_count,
        "my_status": recipe.my_status,
        "my_notes": recipe.my_notes,
    }


@click.group()
@click.option("--db", envvar="TOOLDB_PATH", default=None, help="Database path")
@click.pass_context
def main(ctx: click.Context, db: str | None) -> None:
    """ToolDB: Personal tool discovery and experience cache."""
    setup_logging()
    ctx.ensure_object(dict)
    ctx.obj["db"] = db


# ──────────────────── find ────────────────────


@main.command()
@click.argument("task")
@click.option("--max-layer", default=4, type=int, help="Max cascade layer (1-4)")
@click.option("--force", is_flag=True, help="Bypass negative cache")
@click.option("--dry-run", is_flag=True, help="Show plan without calling APIs")
@click.option("--production", is_flag=True, help="Run production readiness assessment on results")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
def find(
    ctx: click.Context,
    task: str,
    max_layer: int,
    force: bool,
    dry_run: bool,
    production: bool,
    as_json: bool,
) -> None:
    """Search for tools matching a task description."""
    cache = _get_cache(ctx.obj["db"])
    cascade = Cascade(cache)

    result = _run_async(
        cascade.find(
            task,
            max_layer=max_layer,
            bypass_negative_cache=force,
            dry_run=dry_run,
        )
    )

    # Run production assessment if requested or query implies it
    assessments: dict[int, Any] = {}
    if production or _is_production_query_cli(task):
        from tooldb.assessment.production_readiness import assess as run_assess
        from tooldb.assessment.production_readiness import report_to_dict

        for t in result.tools:
            if t.id is not None:
                report = _run_async(run_assess(t, skip_cve=dry_run))
                assessments[t.id] = report

    if as_json:
        data: dict[str, Any] = {
            "tools": [_tool_to_dict(t) for t in result.tools],
            "recipes": [_recipe_to_dict(r) for r in result.recipes],
            "layer_reached": result.layer_reached,
            "negative_cached": result.negative_cached,
            "source_timings": result.source_timings,
        }
        if assessments:
            from tooldb.assessment.production_readiness import report_to_dict

            data["production_assessments"] = {
                str(tid): report_to_dict(r) for tid, r in assessments.items()
            }
        _output_json(data)
        return

    if result.negative_cached:
        click.echo("Task is in negative cache (previously no results found).")
        click.echo("Use --force to bypass.")
        return

    if not result.tools and not result.recipes:
        click.echo("No tools found.")
        return

    click.echo(f"Found {len(result.tools)} tool(s), layer reached: {result.layer_reached}")
    for t in result.tools:
        status_mark = {"works": "+", "broken": "x", "avoid": "!", "degraded": "~"}.get(
            t.my_status, "?"
        )
        click.echo(f"  [{status_mark}] {t.id}: {t.name} ({t.url})")

        # Show assessment summary if available
        if t.id in assessments:
            report = assessments[t.id]
            _print_assessment_summary(report)

    if result.recipes:
        click.echo(f"\nFound {len(result.recipes)} recipe(s):")
        for r in result.recipes:
            click.echo(f"  {r.id}: {r.name} ({r.step_count} steps)")


# ──────────────────── assess ────────────────────


@main.command()
@click.argument("tool_id", type=int)
@click.option("--skip-cve", is_flag=True, help="Skip CVE check")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
def assess(ctx: click.Context, tool_id: int, skip_cve: bool, as_json: bool) -> None:
    """Run production readiness assessment on a tool."""
    from tooldb.assessment.production_readiness import assess as run_assess
    from tooldb.assessment.production_readiness import report_to_dict

    cache = _get_cache(ctx.obj["db"])
    tool = cache.get(tool_id)
    if tool is None:
        click.echo(f"Error: tool {tool_id} not found.", err=True)
        sys.exit(1)

    report = _run_async(run_assess(tool, skip_cve=skip_cve))

    # Save to cache
    cache.save_assessment(report)

    if as_json:
        _output_json(report_to_dict(report))
        return

    _print_assessment_report(report)


def _print_assessment_report(report: Any) -> None:
    """Print a full assessment report."""
    click.echo(f"\nProduction Readiness Assessment: {report.tool_name}")
    click.echo(f"URL: {report.tool_url}")
    click.echo(f"Type: {report.assessment_type}")
    click.echo(f"Score: {report.overall_score:.2f}/1.00")
    click.echo(f"Assessed: {report.assessed_at.strftime('%Y-%m-%d %H:%M UTC')}")

    if report.flags:
        click.echo(f"\nFlags ({len(report.flags)}):")
        for flag in report.flags:
            click.echo(f"  \u26a0 {flag}")
    else:
        click.echo("\nNo flags raised.")

    if report.assessment_type == "repo":
        click.echo("\nSignals:")
        if report.last_commit_date:
            click.echo(f"  Last commit: {report.last_commit_date.strftime('%Y-%m-%d')}")
        if report.has_recent_release is not None:
            click.echo(f"  Recent release: {'yes' if report.has_recent_release else 'no'}"
                       f" ({report.release_count_1y or 0} in last year)")
        if report.open_issue_count is not None:
            click.echo(f"  Open issues: {report.open_issue_count}")
        if report.contributor_count_1y is not None:
            click.echo(f"  Contributors: {report.contributor_count_1y}")
        if report.has_ci is not None:
            click.echo(f"  CI: {'yes' if report.has_ci else 'no'}")
        if report.has_tests is not None:
            click.echo(f"  Tests: {'yes' if report.has_tests else 'no'}")
        if report.has_security_md is not None:
            click.echo(f"  SECURITY.md: {'yes' if report.has_security_md else 'no'}")
        if report.license_spdx:
            click.echo(f"  License: {report.license_spdx} (risk: {report.license_risk})")
        if report.cve_count > 0:
            click.echo(f"  CVEs: {report.cve_count}")
            for cve in report.cve_details[:5]:
                click.echo(f"    {cve['id']}: {cve['summary'][:80]}")

    click.echo(
        "\nNote: This assessment checks publicly available signals. "
        "It does NOT constitute a security audit or compliance certification."
    )


def _print_assessment_summary(report: Any) -> None:
    """Print a compact assessment summary (for use in find output)."""
    score_label = (
        "strong" if report.overall_score > 0.7
        else "caution" if report.overall_score > 0.4
        else "weak"
    )
    score_str = f"{report.overall_score:.2f} ({score_label})"
    click.echo(f"      \u2514\u2500 Production readiness: {score_str}")
    for flag in report.flags[:3]:
        click.echo(f"         \u26a0 {flag}")
    if len(report.flags) > 3:
        click.echo(f"         ... and {len(report.flags) - 3} more flag(s)")


def _is_production_query_cli(task: str) -> bool:
    """Check if query implies production use (CLI helper)."""
    from tooldb.assessment.production_readiness import is_production_query

    return is_production_query(task)


# ──────────────────── record ────────────────────


@main.command()
@click.argument("tool_id", type=int)
@click.argument("status", type=click.Choice(VALID_STATUSES))
@click.option("--notes", default=None, help="Optional notes")
@click.pass_context
def record(ctx: click.Context, tool_id: int, status: str, notes: str | None) -> None:
    """Record experience with a tool (works/broken/degraded/avoid)."""
    cache = _get_cache(ctx.obj["db"])
    tool = cache.get(tool_id)
    if tool is None:
        click.echo(f"Error: tool {tool_id} not found.", err=True)
        sys.exit(1)
    cache.update_status(tool_id, status, notes)
    click.echo(f"Updated {tool.name} to '{status}'.")


# ──────────────────── list ────────────────────


@main.command("list")
@click.option("--status", default=None, type=click.Choice(VALID_STATUSES))
@click.option("--json", "as_json", is_flag=True)
@click.pass_context
def list_tools(ctx: click.Context, status: str | None, as_json: bool) -> None:
    """List cached tools."""
    cache = _get_cache(ctx.obj["db"])
    tools = cache.list_tools(status)

    if as_json:
        _output_json([_tool_to_dict(t) for t in tools])
        return

    if not tools:
        click.echo("No tools found.")
        return

    for t in tools:
        click.echo(f"  {t.id}: {t.name} [{t.my_status}] ({t.url})")


# ──────────────────── delete ────────────────────


@main.command()
@click.argument("tool_id", type=int)
@click.pass_context
def delete(ctx: click.Context, tool_id: int) -> None:
    """Delete a tool from the cache."""
    cache = _get_cache(ctx.obj["db"])
    if cache.delete(tool_id):
        click.echo(f"Deleted tool {tool_id}.")
    else:
        click.echo(f"Error: tool {tool_id} not found.", err=True)
        sys.exit(1)


# ──────────────────── merge ────────────────────


@main.command()
@click.argument("keep_id", type=int)
@click.argument("drop_id", type=int)
@click.pass_context
def merge(ctx: click.Context, keep_id: int, drop_id: int) -> None:
    """Merge two tools (keep first, drop second)."""
    cache = _get_cache(ctx.obj["db"])
    try:
        result = cache.merge(keep_id, drop_id)
        click.echo(f"Merged into {result.name} (id={result.id}).")
    except ValueError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


# ──────────────────── benchmark ────────────────────


@main.command()
@click.argument("tool_id", type=int)
@click.argument("task_type")
@click.option(
    "--target-type",
    default="tool",
    type=click.Choice(["tool", "recipe"]),
)
@click.pass_context
def benchmark(
    ctx: click.Context, tool_id: int, task_type: str, target_type: str
) -> None:
    """Run a benchmark against a tool or recipe."""
    from tooldb.benchmark.runner import BenchmarkRunner
    from tooldb.models import BenchmarkSpec

    cache = _get_cache(ctx.obj["db"])

    # Create a simple deterministic benchmark spec
    spec = BenchmarkSpec(
        task_type=task_type,
        target_type=target_type,  # type: ignore[arg-type]
        target_id=tool_id,
        fixture_path="/dev/null",
        criteria_type="eyeball",
        criteria_spec={},
        budget={},
    )

    runner = BenchmarkRunner()

    if target_type == "tool":
        tool = cache.get(tool_id)
        if tool is None:
            click.echo(f"Error: tool {tool_id} not found.", err=True)
            sys.exit(1)
        result = _run_async(runner.run_tool(spec, tool))
    else:
        recipe = cache.get_recipe(tool_id)
        if recipe is None:
            click.echo(f"Error: recipe {tool_id} not found.", err=True)
            sys.exit(1)
        result = _run_async(runner.run_recipe(spec, recipe, cache.get))

    click.echo(f"Benchmark: {result.task_type} score={result.score:.2f}")


# ──────────────────── invoke ────────────────────


@main.command()
@click.argument("tool_id", type=int)
@click.option("--input", "inputs", multiple=True, help="key=value input pairs")
@click.option("--dry-run", is_flag=True)
@click.pass_context
def invoke(
    ctx: click.Context, tool_id: int, inputs: tuple[str, ...], dry_run: bool
) -> None:
    """Invoke a tool with given inputs."""
    cache = _get_cache(ctx.obj["db"])
    invoker = ToolInvoker(cache)

    input_dict: dict[str, str] = {}
    for pair in inputs:
        if "=" not in pair:
            click.echo(f"Error: invalid input format '{pair}' (use key=value)", err=True)
            sys.exit(1)
        key, val = pair.split("=", 1)
        input_dict[key] = val

    result = _run_async(invoker.invoke(tool_id, input_dict, dry_run=dry_run))
    click.echo(f"Status: {result['status']}")
    click.echo(f"Duration: {result['duration_ms']}ms")
    if result.get("output"):
        click.echo(f"Output: {result['output']}")


# ──────────────────── generate-wrapper ────────────────────


@main.command("generate-wrapper")
@click.argument("tool_id", type=int)
@click.pass_context
def gen_wrapper(ctx: click.Context, tool_id: int) -> None:
    """Generate a Python wrapper for a tool."""
    cache = _get_cache(ctx.obj["db"])
    tool = cache.get(tool_id)
    if tool is None:
        click.echo(f"Error: tool {tool_id} not found.", err=True)
        sys.exit(1)

    path = generate_wrapper(tool)
    click.echo(f"Generated wrapper: {path}")


# ──────────────────── recipe subcommands ────────────────────


@main.group()
def recipe() -> None:
    """Manage multi-tool recipes."""


@recipe.command("create")
@click.argument("name")
@click.option("--description", required=True)
@click.option(
    "--step",
    multiple=True,
    help="Step spec: tool_id=N,params='{}'[,output_to_next_input=key]",
)
@click.pass_context
def recipe_create(
    ctx: click.Context, name: str, description: str, step: tuple[str, ...]
) -> None:
    """Create a new recipe with steps."""
    if not step:
        click.echo("Error: at least one --step required.", err=True)
        sys.exit(1)

    cache = _get_cache(ctx.obj.get("db"))
    steps: list[RecipeStep] = []

    for s in step:
        parts: dict[str, str] = {}
        for segment in s.split(","):
            if "=" in segment:
                k, v = segment.split("=", 1)
                parts[k.strip()] = v.strip()

        try:
            tool_id = int(parts["tool_id"])
        except (KeyError, ValueError):
            click.echo(f"Error: invalid step spec '{s}' (need tool_id=N)", err=True)
            sys.exit(1)

        params = json.loads(parts.get("params", "{}"))
        output_key = parts.get("output_to_next_input")
        steps.append(RecipeStep(tool_id=tool_id, params=params, output_to_next_input=output_key))

    r = Recipe(
        name=name,
        description=description,
        steps=steps,
        step_count=len(steps),
    )

    try:
        saved = cache.create_recipe(r)
        click.echo(f"Created recipe '{saved.name}' (id={saved.id}, {saved.step_count} steps).")
    except (ValueError, Exception) as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


@recipe.command("list")
@click.option("--status", default=None, type=click.Choice(VALID_STATUSES))
@click.option("--json", "as_json", is_flag=True)
@click.pass_context
def recipe_list(ctx: click.Context, status: str | None, as_json: bool) -> None:
    """List recipes."""
    cache = _get_cache(ctx.obj.get("db"))
    recipes = cache.list_recipes(status)

    if as_json:
        _output_json([_recipe_to_dict(r) for r in recipes])
        return

    if not recipes:
        click.echo("No recipes found.")
        return

    for r in recipes:
        click.echo(f"  {r.id}: {r.name} [{r.my_status}] ({r.step_count} steps)")


@recipe.command("benchmark")
@click.argument("recipe_id", type=int)
@click.argument("task_type")
@click.pass_context
def recipe_benchmark(ctx: click.Context, recipe_id: int, task_type: str) -> None:
    """Run a benchmark against a recipe."""
    from tooldb.benchmark.runner import BenchmarkRunner
    from tooldb.models import BenchmarkSpec

    cache = _get_cache(ctx.obj.get("db"))
    r = cache.get_recipe(recipe_id)
    if r is None:
        click.echo(f"Error: recipe {recipe_id} not found.", err=True)
        sys.exit(1)

    spec = BenchmarkSpec(
        task_type=task_type,
        target_type="recipe",
        target_id=recipe_id,
        fixture_path="/dev/null",
        criteria_type="eyeball",
        criteria_spec={},
        budget={},
    )
    runner = BenchmarkRunner()
    result = _run_async(runner.run_recipe(spec, r, cache.get))
    click.echo(f"Recipe benchmark: {result.task_type} score={result.score:.2f}")


# ──────────────────── stats ────────────────────


@main.command()
@click.pass_context
def stats(ctx: click.Context) -> None:
    """Show cache statistics."""
    cache = _get_cache(ctx.obj["db"])
    s = cache.get_stats()
    click.echo("Cache Statistics:")
    click.echo(f"  Total tools:    {s['total_tools']}")
    click.echo(f"  Total recipes:  {s['total_recipes']}")
    click.echo(f"  Negative cache: {s['negative_cache_size']}")
    click.echo(f"  Stale (>90d):   {s['stale_count']}")
    click.echo("  By status:")
    for status, count in s.get("counts_by_status", {}).items():
        click.echo(f"    {status}: {count}")
    if s.get("most_used"):
        click.echo("  Most used:")
        for t in s["most_used"]:
            click.echo(f"    {t['name']} ({t['invocation_count']} invocations)")


# ──────────────────── export / import ────────────────────


@main.command("export")
@click.argument("path")
@click.pass_context
def export_db(ctx: click.Context, path: str) -> None:
    """Export the database to a tar.gz archive."""
    db_path = Path(ctx.obj["db"]) if ctx.obj.get("db") else DEFAULT_DB_PATH
    if not db_path.exists():
        click.echo("Error: no database to export.", err=True)
        sys.exit(1)

    with tarfile.open(path, "w:gz") as tar:
        tar.add(str(db_path), arcname="tooldb.sqlite")
    click.echo(f"Exported to {path}")


@main.command("import")
@click.argument("path")
@click.option("--force", is_flag=True, help="Overwrite existing database")
@click.pass_context
def import_db(ctx: click.Context, path: str, force: bool) -> None:
    """Import a database from a tar.gz archive."""
    db_path = Path(ctx.obj["db"]) if ctx.obj.get("db") else DEFAULT_DB_PATH

    if db_path.exists() and not force:
        click.echo("Error: database exists. Use --force to overwrite.", err=True)
        sys.exit(1)

    db_path.parent.mkdir(parents=True, exist_ok=True)

    with tarfile.open(path, "r:gz") as tar, tempfile.TemporaryDirectory() as tmpdir:
        tar.extractall(tmpdir, filter="data")
        extracted = Path(tmpdir) / "tooldb.sqlite"
        if not extracted.exists():
            click.echo("Error: archive doesn't contain tooldb.sqlite.", err=True)
            sys.exit(1)
        shutil.copy2(str(extracted), str(db_path))

    click.echo(f"Imported from {path}")
