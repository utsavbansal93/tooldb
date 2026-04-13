"""SQLite-backed cache for tools, recipes, and negative results.

All writes call invariant assertions before persisting. URL normalization
is applied on every upsert for deduplication.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from tooldb.db.migrations import init_db
from tooldb.invariants import assert_recipe_invariants, assert_tool_invariants
from tooldb.models import (
    BenchmarkResult,
    Recipe,
    RecipeStep,
    Tool,
    normalize_url,
    tokenize_task,
)


class ToolCache:
    """SQLite-backed CRUD for tools and negative cache entries."""

    def __init__(self, db_path: Path | str = ":memory:") -> None:
        self._conn = init_db(db_path)

    @property
    def conn(self) -> sqlite3.Connection:
        return self._conn

    def close(self) -> None:
        self._conn.close()

    # ──────────────────────────── Tool CRUD ────────────────────────────

    def get(self, tool_id: int) -> Tool | None:
        """Retrieve a tool by its ID."""
        row = self._conn.execute("SELECT * FROM tools WHERE id = ?", (tool_id,)).fetchone()
        return _row_to_tool(row) if row else None

    def upsert(self, tool: Tool) -> Tool:
        """Insert or update a tool. Deduplicates on normalized URL.

        If a tool with the same normalized URL exists, updates it.
        If the tool data hasn't changed, does not bump updated_at.
        Returns the persisted Tool with its assigned ID.
        """
        assert_tool_invariants(tool)
        url = normalize_url(tool.url)
        tool.url = url

        existing = self._conn.execute("SELECT * FROM tools WHERE url = ?", (url,)).fetchone()

        if existing:
            # Check if anything actually changed
            ex_tool = _row_to_tool(existing)
            if _tools_equal_content(ex_tool, tool):
                return ex_tool

            now = _now_str()
            self._conn.execute(
                """UPDATE tools SET
                    name=?, type=?, task_tags=?, license=?, auth_required=?,
                    cost_tier=?, dockerized=?, source=?, my_status=?, my_notes=?,
                    benchmark_results=?, last_used_at=?, last_used_for=?,
                    last_failure_reason=?, install_cmd=?, invocation_template=?,
                    rate_limit_per_hour=?, rate_limit_per_sec=?, auth_method=?,
                    auth_env_var=?, wrapper_path=?, last_invocation_at=?,
                    readme_extracted_at=?, metadata_version=?, invocation_count=?,
                    schema_version=?, updated_at=?
                WHERE id=?""",
                (
                    tool.name,
                    tool.type,
                    json.dumps(tool.task_tags),
                    tool.license,
                    int(tool.auth_required),
                    tool.cost_tier,
                    int(tool.dockerized),
                    tool.source,
                    tool.my_status,
                    tool.my_notes,
                    json.dumps([_benchmark_to_dict(b) for b in tool.benchmark_results]),
                    _dt_to_str(tool.last_used_at),
                    tool.last_used_for,
                    tool.last_failure_reason,
                    tool.install_cmd,
                    tool.invocation_template,
                    tool.rate_limit_per_hour,
                    tool.rate_limit_per_sec,
                    tool.auth_method,
                    tool.auth_env_var,
                    tool.wrapper_path,
                    _dt_to_str(tool.last_invocation_at),
                    _dt_to_str(tool.readme_extracted_at),
                    tool.metadata_version,
                    tool.invocation_count,
                    tool.schema_version,
                    now,
                    existing["id"],
                ),
            )
            self._conn.commit()
            return self.get(existing["id"])  # type: ignore[return-value]
        else:
            now = _now_str()
            cur = self._conn.execute(
                """INSERT INTO tools (
                    name, url, type, task_tags, license, auth_required,
                    cost_tier, dockerized, source, my_status, my_notes,
                    benchmark_results, last_used_at, last_used_for,
                    last_failure_reason, install_cmd, invocation_template,
                    rate_limit_per_hour, rate_limit_per_sec, auth_method,
                    auth_env_var, wrapper_path, last_invocation_at,
                    readme_extracted_at, metadata_version, invocation_count,
                    schema_version, created_at, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    tool.name,
                    url,
                    tool.type,
                    json.dumps(tool.task_tags),
                    tool.license,
                    int(tool.auth_required),
                    tool.cost_tier,
                    int(tool.dockerized),
                    tool.source,
                    tool.my_status,
                    tool.my_notes,
                    json.dumps([_benchmark_to_dict(b) for b in tool.benchmark_results]),
                    _dt_to_str(tool.last_used_at),
                    tool.last_used_for,
                    tool.last_failure_reason,
                    tool.install_cmd,
                    tool.invocation_template,
                    tool.rate_limit_per_hour,
                    tool.rate_limit_per_sec,
                    tool.auth_method,
                    tool.auth_env_var,
                    tool.wrapper_path,
                    _dt_to_str(tool.last_invocation_at),
                    _dt_to_str(tool.readme_extracted_at),
                    tool.metadata_version,
                    tool.invocation_count,
                    tool.schema_version,
                    now,
                    now,
                ),
            )
            self._conn.commit()
            return self.get(cur.lastrowid)  # type: ignore[return-value]

    def delete(self, tool_id: int) -> bool:
        """Delete a tool by ID. Returns True if a row was deleted."""
        cur = self._conn.execute("DELETE FROM tools WHERE id = ?", (tool_id,))
        self._conn.commit()
        return cur.rowcount > 0

    def merge(self, keep_id: int, drop_id: int) -> Tool:
        """Merge drop tool into keep tool.

        - keep's status wins
        - drop's notes appended with '[merged from <drop_url>]' prefix
        - task_tags unioned (deduped)
        - benchmark_results unioned
        - last_failure_reason from keep wins
        - drop row deleted

        Raises ValueError if either ID doesn't exist or keep_id == drop_id.
        """
        if keep_id == drop_id:
            raise ValueError(f"Cannot merge a tool with itself (id={keep_id})")

        keep = self.get(keep_id)
        drop = self.get(drop_id)
        if keep is None:
            raise ValueError(f"Tool with id={keep_id} does not exist")
        if drop is None:
            raise ValueError(f"Tool with id={drop_id} does not exist")

        # Union task_tags (deduped, preserving order)
        seen: set[str] = set()
        merged_tags: list[str] = []
        for tag in keep.task_tags + drop.task_tags:
            if tag not in seen:
                seen.add(tag)
                merged_tags.append(tag)

        # Union benchmark_results
        merged_benchmarks = keep.benchmark_results + drop.benchmark_results

        # Append drop's notes
        drop_note = f"[merged from {drop.url}]"
        if drop.my_notes:
            drop_note += f" {drop.my_notes}"
        merged_notes = keep.my_notes or ""
        if merged_notes:
            merged_notes += "\n"
        merged_notes += drop_note

        # Update keep
        now = _now_str()
        self._conn.execute(
            """UPDATE tools SET
                task_tags=?, benchmark_results=?, my_notes=?, updated_at=?
            WHERE id=?""",
            (
                json.dumps(merged_tags),
                json.dumps([_benchmark_to_dict(b) for b in merged_benchmarks]),
                merged_notes,
                now,
                keep_id,
            ),
        )
        # Delete drop
        self._conn.execute("DELETE FROM tools WHERE id = ?", (drop_id,))
        self._conn.commit()
        return self.get(keep_id)  # type: ignore[return-value]

    def update_status(self, tool_id: int, status: str, notes: str | None = None) -> None:
        """Update a tool's status and optionally its notes."""
        now = _now_str()
        if notes is not None:
            self._conn.execute(
                "UPDATE tools SET my_status=?, my_notes=?, updated_at=? WHERE id=?",
                (status, notes, now, tool_id),
            )
        else:
            self._conn.execute(
                "UPDATE tools SET my_status=?, updated_at=? WHERE id=?",
                (status, now, tool_id),
            )
        self._conn.commit()

    def record_use(self, tool_id: int, task: str) -> None:
        """Record that a tool was used for a specific task."""
        now = _now_str()
        self._conn.execute(
            "UPDATE tools SET last_used_at=?, last_used_for=?, updated_at=? WHERE id=?",
            (now, task, now, tool_id),
        )
        self._conn.commit()

    def record_failure(self, tool_id: int, reason: str) -> None:
        """Record a tool failure, setting status to broken."""
        now = _now_str()
        self._conn.execute(
            """UPDATE tools SET
                my_status='broken', last_failure_reason=?, updated_at=?
            WHERE id=?""",
            (reason, now, tool_id),
        )
        self._conn.commit()

    def list_tools(self, status: str | None = None) -> list[Tool]:
        """List tools, optionally filtered by status."""
        if status:
            rows = self._conn.execute(
                "SELECT * FROM tools WHERE my_status = ? ORDER BY updated_at DESC",
                (status,),
            ).fetchall()
        else:
            rows = self._conn.execute("SELECT * FROM tools ORDER BY updated_at DESC").fetchall()
        return [_row_to_tool(r) for r in rows]

    def find_by_task(self, task: str, status: str | None = None) -> list[Tool]:
        """Find tools matching a task description.

        Token-splits the task, OR-matches each token against task_tags
        (via json_each), name, and my_notes. Ranks by match count.
        Case-insensitive. Returns empty list for empty/stopword-only input.
        """
        tokens = tokenize_task(task)
        if not tokens:
            return []

        # Limit token count to prevent overly expensive queries
        tokens = tokens[:20]

        # Build a query that counts how many tokens match per tool.
        # Match each token against the raw JSON text of task_tags (which contains
        # the tag strings as substrings), plus name and my_notes.
        match_clauses = []
        token_params: list[str] = []
        for token in tokens:
            like_param = f"%{token}%"
            match_clauses.append(
                """(
                    LOWER(t.task_tags) LIKE ?
                    OR LOWER(t.name) LIKE ?
                    OR LOWER(COALESCE(t.my_notes, '')) LIKE ?
                )"""
            )
            token_params.extend([like_param, like_param, like_param])

        # At least one token must match
        where = " OR ".join(match_clauses)
        # Count matches for ranking
        match_count_expr = " + ".join(f"({clause})" for clause in match_clauses)

        # The match_count_expr in SELECT and the where clause in WHERE both
        # contain ? placeholders from the same clauses, so we need params twice:
        # once for the SELECT scoring, once for the WHERE filtering.
        all_params: list[Any] = list(token_params) + list(token_params)

        status_filter = ""
        if status:
            status_filter = " AND t.my_status = ?"
            all_params.append(status)

        sql = f"""
            SELECT t.*, ({match_count_expr}) AS match_count
            FROM tools t
            WHERE ({where}){status_filter}
            ORDER BY match_count DESC, t.updated_at DESC
        """

        rows = self._conn.execute(sql, all_params).fetchall()
        return [_row_to_tool(r) for r in rows]

    # ─────────────────────── Negative cache ───────────────────────

    def is_negatively_cached(self, task_sig: str, ttl_days: int = 7) -> bool:
        """Check if a task signature is in the negative cache and not expired."""
        row = self._conn.execute(
            "SELECT tried_at FROM negative_cache WHERE task_signature = ?",
            (task_sig,),
        ).fetchone()
        if row is None:
            return False
        tried_at = datetime.fromisoformat(row["tried_at"]).replace(tzinfo=None)
        now = datetime.now(UTC).replace(tzinfo=None)
        return now - tried_at < timedelta(days=ttl_days)

    def add_negative(self, task_sig: str, reason: str) -> None:
        """Add or update a negative cache entry (upserts, no duplicates)."""
        now = _now_str()
        self._conn.execute(
            """INSERT INTO negative_cache (task_signature, tried_at, reason)
            VALUES (?, ?, ?)
            ON CONFLICT(task_signature) DO UPDATE SET tried_at=?, reason=?""",
            (task_sig, now, reason, now, reason),
        )
        self._conn.commit()

    # ─────────────────────── Recipe CRUD ───────────────────────

    def create_recipe(self, recipe: Recipe) -> Recipe:
        """Create a new recipe. Validates tool_ids exist and invariants hold.

        Raises ValueError if any step references a nonexistent tool.
        """
        assert_recipe_invariants(recipe)

        # Validate all referenced tool_ids exist
        for i, step in enumerate(recipe.steps):
            tool = self.get(step.tool_id)
            if tool is None:
                raise ValueError(f"Step {i}: tool_id={step.tool_id} does not exist")
            if tool.my_status == "broken":
                import logging

                logging.getLogger("tooldb").warning(
                    "Recipe step %d references broken tool %d (%s)",
                    i,
                    step.tool_id,
                    tool.name,
                )

        now = _now_str()
        cur = self._conn.execute(
            """INSERT INTO recipes (
                name, description, steps, step_count, my_status, my_notes,
                benchmark_results, last_validated_at, created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                recipe.name,
                recipe.description,
                json.dumps([_step_to_dict(s) for s in recipe.steps]),
                recipe.step_count,
                recipe.my_status,
                recipe.my_notes,
                json.dumps([_benchmark_to_dict(b) for b in recipe.benchmark_results]),
                _dt_to_str(recipe.last_validated_at),
                now,
                now,
            ),
        )
        self._conn.commit()
        return self.get_recipe(cur.lastrowid)  # type: ignore[return-value]

    def get_recipe(self, recipe_id: int) -> Recipe | None:
        """Retrieve a recipe by ID."""
        row = self._conn.execute("SELECT * FROM recipes WHERE id = ?", (recipe_id,)).fetchone()
        return _row_to_recipe(row) if row else None

    def list_recipes(self, status: str | None = None) -> list[Recipe]:
        """List recipes, optionally filtered by status."""
        if status:
            rows = self._conn.execute(
                "SELECT * FROM recipes WHERE my_status = ? ORDER BY updated_at DESC",
                (status,),
            ).fetchall()
        else:
            rows = self._conn.execute("SELECT * FROM recipes ORDER BY updated_at DESC").fetchall()
        return [_row_to_recipe(r) for r in rows]

    def update_recipe_status(self, recipe_id: int, status: str, notes: str | None = None) -> None:
        """Update a recipe's status and optionally its notes."""
        now = _now_str()
        if notes is not None:
            self._conn.execute(
                "UPDATE recipes SET my_status=?, my_notes=?, updated_at=? WHERE id=?",
                (status, notes, now, recipe_id),
            )
        else:
            self._conn.execute(
                "UPDATE recipes SET my_status=?, updated_at=? WHERE id=?",
                (status, now, recipe_id),
            )
        self._conn.commit()

    def find_recipes_by_task(self, task: str) -> list[Recipe]:
        """Find recipes matching a task description.

        Token-matches against recipe name, description, and the task_tags
        of all tools referenced in recipe steps.
        """
        tokens = tokenize_task(task)
        if not tokens:
            return []

        tokens = tokens[:20]

        # First get all recipes
        all_recipes = self.list_recipes()
        if not all_recipes:
            return []

        # Score each recipe
        scored: list[tuple[int, Recipe]] = []
        for recipe in all_recipes:
            score = 0
            # Check name + description
            name_lower = recipe.name.lower()
            desc_lower = recipe.description.lower()
            notes_lower = (recipe.my_notes or "").lower()

            # Collect all task_tags from referenced tools
            all_tags: list[str] = []
            needs_revalidation = False
            for step in recipe.steps:
                tool = self.get(step.tool_id)
                if tool is None:
                    needs_revalidation = True
                    continue
                all_tags.extend(t.lower() for t in tool.task_tags)
            tags_text = " ".join(all_tags)

            for token in tokens:
                if token in name_lower or token in desc_lower or token in notes_lower:
                    score += 1
                if token in tags_text:
                    score += 1

            if score > 0:
                # Flag recipes that need revalidation
                if needs_revalidation:
                    recipe.my_notes = (recipe.my_notes or "") + " [needs revalidation]"
                scored.append((score, recipe))

        # Sort by score descending
        scored.sort(key=lambda x: x[0], reverse=True)
        return [r for _, r in scored]

    def validate_recipe(self, recipe_id: int) -> bool:
        """Check all referenced tools still exist. Updates last_validated_at.

        Returns True if all tools exist, False otherwise.
        """
        recipe = self.get_recipe(recipe_id)
        if recipe is None:
            return False

        all_valid = True
        for step in recipe.steps:
            if self.get(step.tool_id) is None:
                all_valid = False
                break

        now = _now_str()
        self._conn.execute(
            "UPDATE recipes SET last_validated_at = ?, updated_at = ? WHERE id = ?",
            (now, now, recipe_id),
        )
        self._conn.commit()
        return all_valid

    # ─────────────────────── Stats ───────────────────────

    def get_stats(self) -> dict[str, Any]:
        """Compute cache statistics.

        Returns dict with: counts_by_status, total_tools, total_recipes,
        negative_cache_size, most_used (top 5), stale_count (>90d unused).
        """
        # Counts by status
        rows = self._conn.execute(
            "SELECT my_status, COUNT(*) as cnt FROM tools GROUP BY my_status"
        ).fetchall()
        counts_by_status = {row["my_status"]: row["cnt"] for row in rows}

        total_tools = sum(counts_by_status.values())

        # Recipe count
        recipe_count = self._conn.execute("SELECT COUNT(*) FROM recipes").fetchone()[0]

        # Negative cache
        neg_count = self._conn.execute("SELECT COUNT(*) FROM negative_cache").fetchone()[0]

        # Most used (by invocation_count)
        most_used = self._conn.execute(
            "SELECT id, name, invocation_count FROM tools ORDER BY invocation_count DESC LIMIT 5"
        ).fetchall()
        most_used_list = [
            {"id": r["id"], "name": r["name"], "invocation_count": r["invocation_count"]}
            for r in most_used
        ]

        # Stale tools (last_used_at > 90 days ago or never used)
        cutoff = (datetime.now(UTC) - timedelta(days=90)).isoformat()
        stale_count = self._conn.execute(
            "SELECT COUNT(*) FROM tools WHERE last_used_at IS NULL OR last_used_at < ?",
            (cutoff,),
        ).fetchone()[0]

        return {
            "counts_by_status": counts_by_status,
            "total_tools": total_tools,
            "total_recipes": recipe_count,
            "negative_cache_size": neg_count,
            "most_used": most_used_list,
            "stale_count": stale_count,
        }


# ──────────────────────── Helpers ────────────────────────


def _row_to_tool(row: sqlite3.Row) -> Tool:
    """Convert a sqlite3.Row to a Tool dataclass."""
    benchmark_results = [
        BenchmarkResult(
            task_type=b["task_type"],
            score=b["score"],
            ran_at=datetime.fromisoformat(b["ran_at"]),
            fixture_hash=b["fixture_hash"],
        )
        for b in json.loads(row["benchmark_results"])
    ]

    return Tool(
        id=row["id"],
        name=row["name"],
        url=row["url"],
        type=row["type"],
        task_tags=json.loads(row["task_tags"]),
        license=row["license"],
        auth_required=bool(row["auth_required"]),
        cost_tier=row["cost_tier"],
        dockerized=bool(row["dockerized"]),
        source=row["source"],
        my_status=row["my_status"],
        my_notes=row["my_notes"],
        benchmark_results=benchmark_results,
        last_used_at=_str_to_dt(row["last_used_at"]),
        last_used_for=row["last_used_for"],
        last_failure_reason=row["last_failure_reason"],
        install_cmd=row["install_cmd"],
        invocation_template=row["invocation_template"],
        rate_limit_per_hour=row["rate_limit_per_hour"],
        rate_limit_per_sec=row["rate_limit_per_sec"],
        auth_method=row["auth_method"],
        auth_env_var=row["auth_env_var"],
        wrapper_path=row["wrapper_path"],
        last_invocation_at=_str_to_dt(row["last_invocation_at"]),
        readme_extracted_at=_str_to_dt(row["readme_extracted_at"]),
        metadata_version=row["metadata_version"],
        invocation_count=row["invocation_count"],
        schema_version=row["schema_version"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def _benchmark_to_dict(b: BenchmarkResult) -> dict[str, Any]:
    return {
        "task_type": b.task_type,
        "score": b.score,
        "ran_at": b.ran_at.isoformat(),
        "fixture_hash": b.fixture_hash,
    }


def _now_str() -> str:
    """Current UTC time as naive ISO string (no tz suffix, SQLite-compatible)."""
    return datetime.now(UTC).replace(tzinfo=None).isoformat()


def _dt_to_str(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    # Store as naive UTC string
    if dt.tzinfo is not None:
        dt = dt.replace(tzinfo=None)
    return dt.isoformat()


def _str_to_dt(s: str | None) -> datetime | None:
    if s is None:
        return None
    dt = datetime.fromisoformat(s)
    # Strip timezone info for consistency (we store naive UTC)
    return dt.replace(tzinfo=None)


def _row_to_recipe(row: sqlite3.Row) -> Recipe:
    """Convert a sqlite3.Row to a Recipe dataclass."""
    steps_data = json.loads(row["steps"])
    steps = [
        RecipeStep(
            tool_id=s["tool_id"],
            params=s.get("params", {}),
            output_to_next_input=s.get("output_to_next_input"),
        )
        for s in steps_data
    ]
    benchmark_results = [
        BenchmarkResult(
            task_type=b["task_type"],
            score=b["score"],
            ran_at=datetime.fromisoformat(b["ran_at"]),
            fixture_hash=b["fixture_hash"],
        )
        for b in json.loads(row["benchmark_results"])
    ]
    return Recipe(
        id=row["id"],
        name=row["name"],
        description=row["description"],
        steps=steps,
        step_count=row["step_count"],
        my_status=row["my_status"],
        my_notes=row["my_notes"],
        benchmark_results=benchmark_results,
        last_validated_at=_str_to_dt(row["last_validated_at"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def _step_to_dict(s: RecipeStep) -> dict[str, Any]:
    return {
        "tool_id": s.tool_id,
        "params": s.params,
        "output_to_next_input": s.output_to_next_input,
    }


def _tools_equal_content(a: Tool, b: Tool) -> bool:
    """Check if two tools have the same content (ignoring id, timestamps)."""
    return (
        a.name == b.name
        and a.type == b.type
        and a.task_tags == b.task_tags
        and a.license == b.license
        and a.auth_required == b.auth_required
        and a.cost_tier == b.cost_tier
        and a.dockerized == b.dockerized
        and a.source == b.source
        and a.my_status == b.my_status
        and a.my_notes == b.my_notes
        and a.install_cmd == b.install_cmd
        and a.invocation_template == b.invocation_template
        and a.rate_limit_per_hour == b.rate_limit_per_hour
        and a.rate_limit_per_sec == b.rate_limit_per_sec
        and a.auth_method == b.auth_method
        and a.auth_env_var == b.auth_env_var
        and a.wrapper_path == b.wrapper_path
        and a.metadata_version == b.metadata_version
        and a.schema_version == b.schema_version
    )
