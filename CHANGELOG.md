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

### Added (previous)
- `.claude/settings.json` auto-registers the MCP server for Claude Code — no manual `claude mcp add` needed
- Server loads `.env` from project root at startup for API keys (`GITHUB_TOKEN`, `BRAVE_API_KEY`)
- `CLAUDE.md` with development rules for keeping docs in sync
- `CHANGELOG.md`
- `decisions/` directory for architectural decision records
