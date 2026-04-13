-- ToolDB schema v1

CREATE TABLE IF NOT EXISTS tools (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    name                TEXT NOT NULL,
    url                 TEXT NOT NULL UNIQUE,
    type                TEXT NOT NULL CHECK(type IN ('repo','api','service','cli')),
    task_tags           TEXT NOT NULL DEFAULT '[]',
    license             TEXT,
    auth_required       INTEGER NOT NULL DEFAULT 0,
    cost_tier           TEXT NOT NULL DEFAULT 'unknown' CHECK(cost_tier IN ('free','freemium','paid','unknown')),
    dockerized          INTEGER NOT NULL DEFAULT 0,
    source              TEXT NOT NULL CHECK(source IN ('cache','github','public_apis','web','manual')),
    my_status           TEXT NOT NULL DEFAULT 'untried' CHECK(my_status IN ('untried','works','degraded','broken','avoid')),
    my_notes            TEXT,
    benchmark_results   TEXT NOT NULL DEFAULT '[]',
    last_used_at        TEXT,
    last_used_for       TEXT,
    last_failure_reason TEXT,
    -- invocation metadata
    install_cmd         TEXT,
    invocation_template TEXT,
    rate_limit_per_hour INTEGER,
    rate_limit_per_sec  REAL,
    auth_method         TEXT,
    auth_env_var        TEXT,
    wrapper_path        TEXT,
    last_invocation_at  TEXT,
    -- operational metadata
    readme_extracted_at TEXT,
    metadata_version    INTEGER NOT NULL DEFAULT 0,
    invocation_count    INTEGER NOT NULL DEFAULT 0,
    schema_version      INTEGER NOT NULL DEFAULT 1,
    --
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at          TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS recipes (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    name                TEXT NOT NULL,
    description         TEXT NOT NULL,
    steps               TEXT NOT NULL,
    step_count          INTEGER NOT NULL,
    my_status           TEXT NOT NULL DEFAULT 'untried' CHECK(my_status IN ('untried','works','degraded','broken','avoid')),
    my_notes            TEXT,
    benchmark_results   TEXT NOT NULL DEFAULT '[]',
    last_validated_at   TEXT,
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at          TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS benchmarks (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    task_type           TEXT NOT NULL,
    target_type         TEXT NOT NULL CHECK(target_type IN ('tool','recipe')),
    target_id           INTEGER NOT NULL,
    fixture_path        TEXT NOT NULL,
    criteria_type       TEXT NOT NULL CHECK(criteria_type IN ('deterministic','llm_judge','eyeball')),
    criteria_spec       TEXT NOT NULL,
    budget              TEXT NOT NULL,
    created_at          TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS negative_cache (
    task_signature      TEXT PRIMARY KEY,
    tried_at            TEXT NOT NULL DEFAULT (datetime('now')),
    reason              TEXT NOT NULL
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_tools_my_status ON tools(my_status);
CREATE INDEX IF NOT EXISTS idx_tools_source ON tools(source);
CREATE INDEX IF NOT EXISTS idx_tools_url ON tools(url);
CREATE INDEX IF NOT EXISTS idx_recipes_my_status ON recipes(my_status);
CREATE INDEX IF NOT EXISTS idx_benchmarks_target ON benchmarks(target_type, target_id);

-- Schema version tracking
CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

INSERT OR IGNORE INTO schema_meta (key, value) VALUES ('version', '1');
