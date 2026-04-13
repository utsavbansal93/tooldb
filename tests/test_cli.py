"""Tests for the Click CLI."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
from click.testing import CliRunner

from tooldb.cli import main
from tooldb.db.cache import ToolCache
from tooldb.models import Tool


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def db_path(tmp_path: Path) -> str:
    return str(tmp_path / "test.sqlite")


@pytest.fixture
def populated_db(db_path: str) -> str:
    """Create a DB with a couple of tools."""
    cache = ToolCache(db_path)
    cache.upsert(
        Tool(
            name="pdf-tool",
            url="https://github.com/example/pdf-tool",
            type="repo",
            task_tags=["pdf", "convert"],
            source="github",
            my_status="works",
        )
    )
    cache.upsert(
        Tool(
            name="markdown-tool",
            url="https://github.com/example/md-tool",
            type="repo",
            task_tags=["markdown"],
            source="github",
            my_status="untried",
            invocation_template="echo {query}",
        )
    )
    cache.close()
    return db_path


# ──────────────────── find ────────────────────


class TestFindCommand:
    def test_find_with_results(self, runner: CliRunner, populated_db: str) -> None:
        result = runner.invoke(main, ["--db", populated_db, "find", "pdf", "--max-layer", "2"])
        assert result.exit_code == 0
        assert "pdf-tool" in result.output

    def test_find_no_results(self, runner: CliRunner, db_path: str) -> None:
        # Init empty DB
        ToolCache(db_path).close()
        result = runner.invoke(main, ["--db", db_path, "find", "nonexistent", "--max-layer", "2"])
        assert result.exit_code == 0
        assert "No tools found" in result.output or "negative cache" in result.output.lower()

    def test_find_json_output(self, runner: CliRunner, populated_db: str) -> None:
        result = runner.invoke(
            main, ["--db", populated_db, "find", "pdf", "--max-layer", "2", "--json"]
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "tools" in data

    def test_find_dry_run(self, runner: CliRunner, populated_db: str) -> None:
        result = runner.invoke(
            main, ["--db", populated_db, "find", "new thing", "--dry-run", "--max-layer", "4"]
        )
        assert result.exit_code == 0

    def test_find_force_bypasses_negative_cache(
        self, runner: CliRunner, populated_db: str
    ) -> None:
        result = runner.invoke(
            main,
            ["--db", populated_db, "find", "something", "--force", "--max-layer", "2"],
        )
        assert result.exit_code == 0


# ──────────────────── record ────────────────────


class TestRecordCommand:
    def test_record_status(self, runner: CliRunner, populated_db: str) -> None:
        result = runner.invoke(main, ["--db", populated_db, "record", "1", "works"])
        assert result.exit_code == 0
        assert "Updated" in result.output

    def test_record_nonexistent(self, runner: CliRunner, populated_db: str) -> None:
        result = runner.invoke(main, ["--db", populated_db, "record", "999", "works"])
        assert result.exit_code != 0

    def test_record_invalid_status(self, runner: CliRunner, populated_db: str) -> None:
        result = runner.invoke(main, ["--db", populated_db, "record", "1", "amazing"])
        assert result.exit_code != 0


# ──────────────────── list ────────────────────


class TestListCommand:
    def test_list_all(self, runner: CliRunner, populated_db: str) -> None:
        result = runner.invoke(main, ["--db", populated_db, "list"])
        assert result.exit_code == 0
        assert "pdf-tool" in result.output

    def test_list_by_status(self, runner: CliRunner, populated_db: str) -> None:
        result = runner.invoke(main, ["--db", populated_db, "list", "--status", "works"])
        assert result.exit_code == 0
        assert "pdf-tool" in result.output
        assert "markdown-tool" not in result.output

    def test_list_json(self, runner: CliRunner, populated_db: str) -> None:
        result = runner.invoke(main, ["--db", populated_db, "list", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)


# ──────────────────── delete ────────────────────


class TestDeleteCommand:
    def test_delete_existing(self, runner: CliRunner, populated_db: str) -> None:
        result = runner.invoke(main, ["--db", populated_db, "delete", "1"])
        assert result.exit_code == 0
        assert "Deleted" in result.output

    def test_delete_nonexistent(self, runner: CliRunner, populated_db: str) -> None:
        result = runner.invoke(main, ["--db", populated_db, "delete", "999"])
        assert result.exit_code != 0


# ──────────────────── merge ────────────────────


class TestMergeCommand:
    def test_merge_success(self, runner: CliRunner, populated_db: str) -> None:
        result = runner.invoke(main, ["--db", populated_db, "merge", "1", "2"])
        assert result.exit_code == 0
        assert "Merged" in result.output

    def test_merge_same_id(self, runner: CliRunner, populated_db: str) -> None:
        result = runner.invoke(main, ["--db", populated_db, "merge", "1", "1"])
        assert result.exit_code != 0


# ──────────────────── invoke ────────────────────


class TestInvokeCommand:
    def test_invoke_success(self, runner: CliRunner, populated_db: str) -> None:
        result = runner.invoke(
            main,
            ["--db", populated_db, "invoke", "2", "--input", "query=hello"],
        )
        assert result.exit_code == 0
        assert "Status:" in result.output

    def test_invoke_dry_run(self, runner: CliRunner, populated_db: str) -> None:
        result = runner.invoke(
            main,
            ["--db", populated_db, "invoke", "2", "--input", "query=test", "--dry-run"],
        )
        assert result.exit_code == 0
        assert "dry_run" in result.output


# ──────────────────── stats ────────────────────


class TestStatsCommand:
    def test_stats(self, runner: CliRunner, populated_db: str) -> None:
        result = runner.invoke(main, ["--db", populated_db, "stats"])
        assert result.exit_code == 0
        assert "Total tools:" in result.output


# ──────────────────── export / import ────────────────────


class TestExportImport:
    def test_export_import_roundtrip(self, runner: CliRunner, populated_db: str) -> None:
        with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as f:
            export_path = f.name

        # Export
        result = runner.invoke(main, ["--db", populated_db, "export", export_path])
        assert result.exit_code == 0

        # Import to new location
        with tempfile.TemporaryDirectory() as tmpdir:
            new_db = str(Path(tmpdir) / "imported.sqlite")
            result = runner.invoke(main, ["--db", new_db, "import", export_path])
            assert result.exit_code == 0

            # Verify
            cache = ToolCache(new_db)
            tools = cache.list_tools()
            assert len(tools) == 2

    def test_import_refuses_overwrite(self, runner: CliRunner, populated_db: str) -> None:
        with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as f:
            export_path = f.name
        runner.invoke(main, ["--db", populated_db, "export", export_path])

        # Import without --force to existing DB
        result = runner.invoke(main, ["--db", populated_db, "import", export_path])
        assert result.exit_code != 0
        assert "exists" in result.output.lower() or "force" in result.output.lower()


# ──────────────────── help ────────────────────


class TestHelp:
    def test_main_help(self, runner: CliRunner) -> None:
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "ToolDB" in result.output

    def test_find_help(self, runner: CliRunner) -> None:
        result = runner.invoke(main, ["find", "--help"])
        assert result.exit_code == 0

    def test_recipe_help(self, runner: CliRunner) -> None:
        result = runner.invoke(main, ["recipe", "--help"])
        assert result.exit_code == 0


# ──────────────────── recipe subcommands ────────────────────


class TestRecipeCommands:
    def test_recipe_create(self, runner: CliRunner, populated_db: str) -> None:
        result = runner.invoke(
            main,
            [
                "--db", populated_db,
                "recipe", "create", "test-recipe",
                "--description", "A test recipe",
                "--step", "tool_id=1,params={}",
            ],
        )
        assert result.exit_code == 0
        assert "Created" in result.output

    def test_recipe_list(self, runner: CliRunner, populated_db: str) -> None:
        # Create a recipe first
        runner.invoke(
            main,
            [
                "--db", populated_db,
                "recipe", "create", "r1",
                "--description", "desc",
                "--step", "tool_id=1,params={}",
            ],
        )
        result = runner.invoke(main, ["--db", populated_db, "recipe", "list"])
        assert result.exit_code == 0
        assert "r1" in result.output

    def test_recipe_list_json(self, runner: CliRunner, populated_db: str) -> None:
        runner.invoke(
            main,
            [
                "--db", populated_db,
                "recipe", "create", "r2",
                "--description", "d",
                "--step", "tool_id=1,params={}",
            ],
        )
        result = runner.invoke(main, ["--db", populated_db, "recipe", "list", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)
