"""Tests for the v1 → v2 schema migration."""

from __future__ import annotations

import sqlite3

from tooldb.db.migrations import CURRENT_SCHEMA_VERSION, get_schema_version, init_db, migrate


class TestMigrationV2:
    def test_fresh_db_gets_v2(self) -> None:
        """A brand new database should get schema v2 with production_assessments."""
        conn = init_db(":memory:")
        version = get_schema_version(conn)
        assert version == 2

        # Verify table exists
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='production_assessments'"
        ).fetchone()
        assert row is not None, "production_assessments table should exist"
        conn.close()

    def test_v1_db_migrates_to_v2(self) -> None:
        """An existing v1 database should migrate cleanly to v2."""
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")

        # Apply v1 schema manually (without production_assessments)
        conn.executescript("""
            CREATE TABLE tools (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                url TEXT NOT NULL UNIQUE,
                type TEXT NOT NULL CHECK(type IN ('repo','api','service','cli')),
                task_tags TEXT NOT NULL DEFAULT '[]',
                source TEXT NOT NULL CHECK(
                    source IN ('cache','github','public_apis','web','manual')
                ),
                my_status TEXT NOT NULL DEFAULT 'untried',
                schema_version INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO schema_meta (key, value) VALUES ('version', '1');
        """)

        # Insert a v1 tool
        conn.execute(
            "INSERT INTO tools (name, url, type, source) VALUES (?, ?, ?, ?)",
            ("test-tool", "https://github.com/foo/bar", "repo", "github"),
        )
        conn.commit()

        # Now migrate
        migrate(conn)

        # Check version is 2
        assert get_schema_version(conn) == 2

        # Check existing tool data is preserved
        tool = conn.execute("SELECT * FROM tools WHERE name = 'test-tool'").fetchone()
        assert tool is not None
        assert tool["url"] == "https://github.com/foo/bar"

        # Check production_assessments table exists
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='production_assessments'"
        ).fetchone()
        assert row is not None

        conn.close()

    def test_v2_db_no_double_migrate(self) -> None:
        """A v2 database should not re-run migrations."""
        conn = init_db(":memory:")
        assert get_schema_version(conn) == 2

        # Running migrate again should be a no-op
        migrate(conn)
        assert get_schema_version(conn) == 2
        conn.close()

    def test_current_schema_version_is_2(self) -> None:
        assert CURRENT_SCHEMA_VERSION == 2
