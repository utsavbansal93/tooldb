"""Schema migrations for ToolDB.

Applies schema on fresh databases and runs incremental migrations
on existing ones. Migration safety: checks schema_meta version
before applying.
"""

from __future__ import annotations

import logging
import sqlite3
from importlib import resources
from pathlib import Path

logger = logging.getLogger("tooldb")

CURRENT_SCHEMA_VERSION = 1


def apply_schema(conn: sqlite3.Connection) -> None:
    """Apply the full schema to a fresh database."""
    schema_sql = resources.files("tooldb.db").joinpath("schema.sql").read_text()
    conn.executescript(schema_sql)
    logger.info("Applied schema v%d", CURRENT_SCHEMA_VERSION)


def get_schema_version(conn: sqlite3.Connection) -> int:
    """Get the current schema version from the database.

    Returns 0 if schema_meta table doesn't exist (fresh/empty DB).
    """
    try:
        row = conn.execute("SELECT value FROM schema_meta WHERE key = 'version'").fetchone()
        return int(row[0]) if row else 0
    except sqlite3.OperationalError:
        return 0


def migrate(conn: sqlite3.Connection) -> None:
    """Run any needed migrations to bring the DB up to CURRENT_SCHEMA_VERSION.

    On a fresh database (no schema_meta table), applies the full schema.
    On an existing database, runs incremental migrations.
    """
    version = get_schema_version(conn)

    if version == 0:
        # Fresh database — apply full schema
        apply_schema(conn)
        return

    if version == CURRENT_SCHEMA_VERSION:
        return

    # Future migrations go here:
    # if version < 2:
    #     _migrate_v1_to_v2(conn)
    # if version < 3:
    #     _migrate_v2_to_v3(conn)

    # Update version
    conn.execute(
        "UPDATE schema_meta SET value = ? WHERE key = 'version'",
        (str(CURRENT_SCHEMA_VERSION),),
    )
    conn.commit()
    logger.info("Migrated schema from v%d to v%d", version, CURRENT_SCHEMA_VERSION)


def init_db(db_path: Path | str) -> sqlite3.Connection:
    """Open (or create) a SQLite database and ensure schema is applied.

    Args:
        db_path: Path to the database file. Use ":memory:" for testing.

    Returns:
        A sqlite3.Connection with WAL mode and foreign keys enabled.

    Raises:
        sqlite3.DatabaseError: If the file exists but is corrupt.
    """
    db_path_str = str(db_path)
    conn = sqlite3.connect(db_path_str)
    conn.row_factory = sqlite3.Row

    # Enable WAL mode for better concurrent read performance
    if db_path_str != ":memory:":
        conn.execute("PRAGMA journal_mode=WAL")

    conn.execute("PRAGMA foreign_keys=ON")

    # Verify the database isn't corrupt
    try:
        result = conn.execute("PRAGMA integrity_check").fetchone()
        if result[0] != "ok":
            raise sqlite3.DatabaseError(f"Database integrity check failed: {result[0]}")
    except sqlite3.DatabaseError:
        conn.close()
        raise

    migrate(conn)
    return conn
