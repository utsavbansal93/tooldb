# Changelog

All notable changes to this project will be documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- Production readiness assessment module (`src/tooldb/assessment/`) — checks GitHub repo health, license risk, and known CVEs via OSV.dev
- `assess_production_readiness` MCP tool (#12) — returns structured report with overall score (0.0–1.0) and human-readable flags
- `tooldb assess <id>` CLI command — run production readiness assessment on a tool
- `--production` flag on `tooldb find` — auto-runs assessment on results; also auto-triggers for queries containing production/enterprise/regulated keywords
- Safety checks on invocation — blocks tools with status "avoid", warns on dangerous shell patterns in templates and untrusted URLs
- Schema v2 migration — adds `production_assessments` table (existing data preserved)
- MCP E2E smoke test — starts server subprocess, handshakes, verifies all 12 tools are registered
- Skill update — new section 2b with production assessment decision tree and honest framing disclaimer

### Fixed
- MCP server entry point (`uv run tooldb-mcp`) now works — moved `mcp_server/` into `src/` so it's on sys.path
- Built wheel now includes the `mcp_server` package — previously only `tooldb/` shipped, so `uvx`/external installs crashed with `ModuleNotFoundError: No module named 'mcp_server'`. Declared both modules via `[tool.uv.build-backend] module-name`.

### Changed
- Server honors `TOOLDB_DB_PATH` env var to override the SQLite location; default remains `~/.tooldb/tooldb.sqlite`. Enables running outside the project checkout (e.g. `uvx tooldb-mcp` in Cowork).
- `skills/SKILL.md` gained a "Prerequisites & self-setup" section (Track 2): if the tooldb MCP isn't registered in the current session, the skill now instructs Claude to register it via `claude mcp add` or `.claude/settings.json`, then ask the user to reload. Turns the previous dead-end (skill advertised but tools missing) into an on-demand self-install with one reload.
- `skills/SKILL.md` gained a Track 3 section: last-resort graceful fallback when self-setup can't complete (no CLI, no write permission, no network). Prints a clear user-facing message AND auto-files a GitHub issue with environment diagnostics so the install path can be tightened. Every Track 3 hit is treated as a defect to eliminate, not an acceptable steady state.

### Added
- Companion [`tooldb-cowork-plugin`](https://github.com/utsavbansal93/tooldb-cowork-plugin) repo: a Cowork plugin that co-ships `skills/tool-search/SKILL.md` with a `.mcp.json` registering `tooldb` via `uvx --from git+...`. Installing the plugin guarantees the skill's MCP backend is always present.

### Added (previous)
- `.claude/settings.json` auto-registers the MCP server for Claude Code — no manual `claude mcp add` needed
- Server loads `.env` from project root at startup for API keys (`GITHUB_TOKEN`, `BRAVE_API_KEY`)
- `CLAUDE.md` with development rules for keeping docs in sync
- `CHANGELOG.md`
- `decisions/` directory for architectural decision records
