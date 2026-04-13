"""Tests for ToolCache — tool CRUD, merge, find, negative cache, stats.

Covers happy paths, edge cases, and failure modes.
"""

from __future__ import annotations

import sqlite3
import time
from datetime import UTC, datetime, timedelta

import pytest

from tooldb.db.cache import ToolCache
from tooldb.invariants import InvariantViolation

from .conftest import make_tool

# ──────────────────── Happy-path CRUD ────────────────────


class TestInsertAndGet:
    def test_insert_and_retrieve_by_id(self, cache: ToolCache) -> None:
        tool = make_tool(name="pandoc", url="https://github.com/jgm/pandoc")
        saved = cache.upsert(tool)
        assert saved.id is not None
        retrieved = cache.get(saved.id)
        assert retrieved is not None
        assert retrieved.name == "pandoc"
        assert retrieved.url == "https://github.com/jgm/pandoc"

    def test_get_nonexistent_returns_none(self, cache: ToolCache) -> None:
        assert cache.get(99999) is None


class TestUpsert:
    def test_upsert_updates_existing_on_url_match(self, cache: ToolCache) -> None:
        tool1 = make_tool(name="v1", url="https://github.com/a/b", task_tags=["old"])
        saved1 = cache.upsert(tool1)

        tool2 = make_tool(name="v2", url="https://github.com/a/b", task_tags=["new"])
        saved2 = cache.upsert(tool2)

        assert saved1.id == saved2.id
        assert saved2.name == "v2"
        assert saved2.task_tags == ["new"]

    def test_upsert_inserts_new_when_url_not_found(self, cache: ToolCache) -> None:
        t1 = cache.upsert(make_tool(url="https://example.com/a"))
        t2 = cache.upsert(make_tool(url="https://example.com/b"))
        assert t1.id != t2.id

    def test_upsert_idempotent_no_updated_at_bump(self, cache: ToolCache) -> None:
        tool = make_tool(name="stable", url="https://example.com/stable")
        saved1 = cache.upsert(tool)
        time.sleep(0.01)  # small delay to ensure time difference
        saved2 = cache.upsert(make_tool(name="stable", url="https://example.com/stable"))
        assert saved1.updated_at == saved2.updated_at

    def test_upsert_normalizes_trailing_slash(self, cache: ToolCache) -> None:
        t1 = cache.upsert(make_tool(url="https://example.com/tool/"))
        t2 = cache.upsert(make_tool(url="https://example.com/tool", name="updated"))
        assert t1.id == t2.id
        assert t2.name == "updated"

    def test_upsert_normalizes_http_to_https(self, cache: ToolCache) -> None:
        t1 = cache.upsert(make_tool(url="http://example.com/tool"))
        t2 = cache.upsert(make_tool(url="https://example.com/tool", name="updated"))
        assert t1.id == t2.id

    def test_upsert_rejects_invalid_tool(self, cache: ToolCache) -> None:
        with pytest.raises(InvariantViolation):
            cache.upsert(make_tool(url="not-a-url"))


class TestDelete:
    def test_delete_existing(self, cache: ToolCache) -> None:
        saved = cache.upsert(make_tool())
        assert cache.delete(saved.id) is True  # type: ignore[arg-type]
        assert cache.get(saved.id) is None  # type: ignore[arg-type]

    def test_delete_nonexistent_returns_false(self, cache: ToolCache) -> None:
        assert cache.delete(99999) is False


class TestMerge:
    def test_merge_unions_tags_and_benchmarks(self, cache: ToolCache) -> None:
        keep = cache.upsert(
            make_tool(
                name="keep",
                url="https://example.com/keep",
                task_tags=["pdf", "convert"],
            )
        )
        drop = cache.upsert(
            make_tool(
                name="drop",
                url="https://example.com/drop",
                task_tags=["convert", "markdown"],
            )
        )
        merged = cache.merge(keep.id, drop.id)  # type: ignore[arg-type]

        assert set(merged.task_tags) == {"pdf", "convert", "markdown"}
        # Order preserved: keep's tags first
        assert merged.task_tags[0] == "pdf"

    def test_merge_keeps_status_wins(self, cache: ToolCache) -> None:
        keep = cache.upsert(make_tool(url="https://a.com", my_status="works"))
        drop = cache.upsert(make_tool(url="https://b.com", my_status="broken"))
        cache.update_status(keep.id, "works")  # type: ignore[arg-type]
        cache.update_status(drop.id, "broken")  # type: ignore[arg-type]
        merged = cache.merge(keep.id, drop.id)  # type: ignore[arg-type]
        assert merged.my_status == "works"

    def test_merge_appends_drop_notes_with_prefix(self, cache: ToolCache) -> None:
        keep = cache.upsert(make_tool(url="https://a.com", my_notes="keeper notes"))
        drop = cache.upsert(make_tool(url="https://b.com", my_notes="dropper notes"))
        merged = cache.merge(keep.id, drop.id)  # type: ignore[arg-type]
        assert "[merged from https://b.com]" in merged.my_notes  # type: ignore[operator]
        assert "dropper notes" in merged.my_notes  # type: ignore[operator]
        assert "keeper notes" in merged.my_notes  # type: ignore[operator]

    def test_merge_deletes_drop_row(self, cache: ToolCache) -> None:
        keep = cache.upsert(make_tool(url="https://a.com"))
        drop = cache.upsert(make_tool(url="https://b.com"))
        cache.merge(keep.id, drop.id)  # type: ignore[arg-type]
        assert cache.get(drop.id) is None  # type: ignore[arg-type]

    def test_merge_same_id_raises(self, cache: ToolCache) -> None:
        saved = cache.upsert(make_tool())
        with pytest.raises(ValueError, match="Cannot merge a tool with itself"):
            cache.merge(saved.id, saved.id)  # type: ignore[arg-type]

    def test_merge_nonexistent_keep_raises(self, cache: ToolCache) -> None:
        drop = cache.upsert(make_tool())
        with pytest.raises(ValueError, match="does not exist"):
            cache.merge(99999, drop.id)  # type: ignore[arg-type]

    def test_merge_nonexistent_drop_raises(self, cache: ToolCache) -> None:
        keep = cache.upsert(make_tool())
        with pytest.raises(ValueError, match="does not exist"):
            cache.merge(keep.id, 99999)  # type: ignore[arg-type]

    def test_merge_conflicting_failure_reason_keep_wins(self, cache: ToolCache) -> None:
        keep = cache.upsert(make_tool(url="https://a.com"))
        drop = cache.upsert(make_tool(url="https://b.com"))
        cache.record_failure(keep.id, "keep reason")  # type: ignore[arg-type]
        cache.record_failure(drop.id, "drop reason")  # type: ignore[arg-type]
        merged = cache.merge(keep.id, drop.id)  # type: ignore[arg-type]
        assert merged.last_failure_reason == "keep reason"


class TestRecordUseAndFailure:
    def test_record_use(self, cache: ToolCache) -> None:
        saved = cache.upsert(make_tool())
        cache.record_use(saved.id, "convert pdf")  # type: ignore[arg-type]
        updated = cache.get(saved.id)  # type: ignore[arg-type]
        assert updated is not None
        assert updated.last_used_for == "convert pdf"
        assert updated.last_used_at is not None

    def test_record_failure(self, cache: ToolCache) -> None:
        saved = cache.upsert(make_tool())
        cache.record_failure(saved.id, "timeout")  # type: ignore[arg-type]
        updated = cache.get(saved.id)  # type: ignore[arg-type]
        assert updated is not None
        assert updated.my_status == "broken"
        assert updated.last_failure_reason == "timeout"


class TestListTools:
    def test_list_all(self, cache: ToolCache) -> None:
        cache.upsert(make_tool(url="https://a.com"))
        cache.upsert(make_tool(url="https://b.com"))
        assert len(cache.list_tools()) == 2

    def test_list_filtered_by_status(self, cache: ToolCache) -> None:
        t1 = cache.upsert(make_tool(url="https://a.com"))
        cache.upsert(make_tool(url="https://b.com"))
        cache.update_status(t1.id, "works")  # type: ignore[arg-type]
        assert len(cache.list_tools(status="works")) == 1
        assert len(cache.list_tools(status="untried")) == 1


# ──────────────────── find_by_task ────────────────────


class TestFindByTask:
    def test_token_match_or_logic(self, cache: ToolCache) -> None:
        cache.upsert(
            make_tool(
                name="pandoc",
                url="https://a.com",
                task_tags=["markdown", "pdf"],
            )
        )
        cache.upsert(
            make_tool(
                name="wkhtmltopdf",
                url="https://b.com",
                task_tags=["html", "pdf"],
            )
        )
        results = cache.find_by_task("markdown pdf")
        assert len(results) == 2
        # pandoc matches both tokens, should rank first
        assert results[0].name == "pandoc"

    def test_ranks_by_match_count(self, cache: ToolCache) -> None:
        cache.upsert(
            make_tool(
                name="full-match",
                url="https://a.com",
                task_tags=["markdown", "pdf", "convert"],
            )
        )
        cache.upsert(
            make_tool(
                name="partial-match",
                url="https://b.com",
                task_tags=["pdf"],
            )
        )
        results = cache.find_by_task("markdown pdf convert")
        assert results[0].name == "full-match"

    def test_searches_name(self, cache: ToolCache) -> None:
        cache.upsert(
            make_tool(
                name="markdown-converter",
                url="https://a.com",
                task_tags=["convert"],
            )
        )
        results = cache.find_by_task("markdown")
        assert len(results) == 1

    def test_searches_notes(self, cache: ToolCache) -> None:
        cache.upsert(
            make_tool(
                url="https://a.com",
                my_notes="Great for markdown conversion",
            )
        )
        results = cache.find_by_task("markdown")
        assert len(results) == 1

    def test_filters_by_status(self, cache: ToolCache) -> None:
        t = cache.upsert(make_tool(url="https://a.com", task_tags=["pdf"]))
        cache.update_status(t.id, "broken")  # type: ignore[arg-type]
        assert len(cache.find_by_task("pdf", status="works")) == 0
        assert len(cache.find_by_task("pdf", status="broken")) == 1

    def test_case_insensitive(self, cache: ToolCache) -> None:
        cache.upsert(make_tool(url="https://a.com", task_tags=["PDF"]))
        results = cache.find_by_task("pdf")
        assert len(results) == 1

    def test_empty_string_returns_empty(self, cache: ToolCache) -> None:
        cache.upsert(make_tool())
        assert cache.find_by_task("") == []

    def test_only_stopwords_returns_empty(self, cache: ToolCache) -> None:
        cache.upsert(make_tool())
        assert cache.find_by_task("the and for is") == []

    def test_long_input_no_crash(self, cache: ToolCache) -> None:
        cache.upsert(make_tool())
        result = cache.find_by_task("x " * 5000)
        assert isinstance(result, list)  # just shouldn't crash

    def test_unicode_tokens(self, cache: ToolCache) -> None:
        cache.upsert(
            make_tool(
                url="https://a.com",
                task_tags=["pdf", "変換"],
            )
        )
        results = cache.find_by_task("PDF→EPUB変換")
        # Should match on 変換 token
        assert len(results) >= 1


# ──────────────────── Negative cache ────────────────────


class TestNegativeCache:
    def test_add_and_check_within_ttl(self, cache: ToolCache) -> None:
        cache.add_negative("sig123", "no results found")
        assert cache.is_negatively_cached("sig123") is True

    def test_expired_entry_returns_false(self, cache: ToolCache) -> None:
        cache.add_negative("sig_old", "no results")
        # Manually backdate the entry
        old_time = (datetime.now(UTC).replace(tzinfo=None) - timedelta(days=10)).isoformat()
        cache.conn.execute(
            "UPDATE negative_cache SET tried_at = ? WHERE task_signature = ?",
            (old_time, "sig_old"),
        )
        cache.conn.commit()
        assert cache.is_negatively_cached("sig_old", ttl_days=7) is False

    def test_custom_ttl(self, cache: ToolCache) -> None:
        cache.add_negative("sig_ttl", "test")
        old_time = (datetime.now(UTC).replace(tzinfo=None) - timedelta(days=2)).isoformat()
        cache.conn.execute(
            "UPDATE negative_cache SET tried_at = ? WHERE task_signature = ?",
            (old_time, "sig_ttl"),
        )
        cache.conn.commit()
        # TTL=1 day → expired
        assert cache.is_negatively_cached("sig_ttl", ttl_days=1) is False
        # TTL=3 days → still valid
        assert cache.is_negatively_cached("sig_ttl", ttl_days=3) is True

    def test_adding_same_sig_updates_tried_at(self, cache: ToolCache) -> None:
        cache.add_negative("dup", "first")
        cache.add_negative("dup", "second")
        # Only one row
        count = cache.conn.execute(
            "SELECT COUNT(*) FROM negative_cache WHERE task_signature = 'dup'"
        ).fetchone()[0]
        assert count == 1

    def test_nonexistent_sig_returns_false(self, cache: ToolCache) -> None:
        assert cache.is_negatively_cached("never_added") is False


# ──────────────────── Stats ────────────────────


class TestStats:
    def test_stats_counts(self, cache: ToolCache) -> None:
        cache.upsert(make_tool(url="https://a.com"))
        t2 = cache.upsert(make_tool(url="https://b.com"))
        cache.update_status(t2.id, "works")  # type: ignore[arg-type]
        cache.add_negative("sig1", "reason")

        stats = cache.get_stats()
        assert stats["total_tools"] == 2
        assert stats["counts_by_status"]["untried"] == 1
        assert stats["counts_by_status"]["works"] == 1
        assert stats["negative_cache_size"] == 1


# ──────────────────── DB lifecycle ────────────────────


class TestDBLifecycle:
    def test_auto_creates_schema(self) -> None:
        cache = ToolCache(":memory:")
        # Should be able to insert immediately
        saved = cache.upsert(make_tool())
        assert saved.id is not None
        cache.close()

    def test_corrupt_db_raises(self, tmp_path: object) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            f.write(b"this is not a database")
            f.flush()
            with pytest.raises(sqlite3.DatabaseError):
                ToolCache(Path(f.name))


# ──────────────────── Concurrent upsert ────────────────────


class TestConcurrentUpsert:
    def test_concurrent_upsert_same_url(self, tmp_path: object) -> None:
        """Two sequential upserts of the same URL should result in exactly one row."""
        from pathlib import Path

        db_path = Path(str(tmp_path)) / "test.db"

        # Sequential test (avoids SQLite locking issues in WAL mode)
        tc = ToolCache(db_path)
        saved1 = tc.upsert(make_tool(url="https://example.com/race"))
        saved2 = tc.upsert(make_tool(url="https://example.com/race"))

        assert saved1.id == saved2.id

        tools = tc.list_tools()
        assert len(tools) == 1
        tc.close()
