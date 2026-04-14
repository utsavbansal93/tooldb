# ToolDB — Development Notes

## Skill ↔ MCP Coupling

The skill file (`skills/SKILL.md`) references MCP tool names by exact string (e.g. `find_tool`, `record_experience`). The MCP server (`src/mcp_server/server.py`) defines those names via `@mcp.tool()` decorators.

**If you rename, remove, or add an MCP tool in `server.py`, check whether `skills/SKILL.md` needs a matching update.** The skill only depends on tool names and their parameter signatures — not on internal implementation, file paths, or module structure. So most code changes (refactors, bug fixes, new internal helpers) do NOT require a skill update.

Changes that DO require a skill update:
- Renaming a `@mcp.tool()` function
- Removing a tool
- Adding a new tool that users should know about
- Changing a tool's parameter names or semantics

Changes that do NOT:
- Internal refactors, moving files, changing imports
- Bug fixes in tool implementations
- Adding internal helpers or changing database schema
- Modifying `.env` handling or logging

## What to Update With Every Change

After completing any code change, update the relevant docs **before** committing. Use this checklist:

### Always update:
- **CHANGELOG.md** — Add an entry under `[Unreleased]` describing what changed and why. Group by: Added, Changed, Fixed, Removed. Keep entries concise (one line each).
- **README.md** — If the change affects anything user-facing: CLI commands, MCP tools, setup steps, env vars, project structure, or quickstart examples.

### Update when applicable:
- **decisions/** — When you make a non-obvious architectural or design choice (e.g. "why we moved mcp_server into src/", "why we use manual .env parsing instead of python-dotenv"), add a decision log entry as `decisions/NNNN-short-title.md` using the format: Context, Decision, Consequences.
- **skills/SKILL.md** — Only when MCP tool names, parameters, or semantics change (see Skill ↔ MCP Coupling above).
- **.claude/settings.json** — Only if MCP server command or args change.

### Never update for:
- Internal refactors that don't change behavior
- Test-only changes
- Dev tooling changes (ruff config, mypy config, pre-commit)

### Changelog format (CHANGELOG.md):
```markdown
## [Unreleased]
### Added
- MCP tool `foo_bar` for doing X

### Changed
- `find_tool` now accepts `timeout` parameter

### Fixed
- Entry point crash when mcp_server not on sys.path

### Removed
- Deprecated `old_tool` MCP endpoint
```

## MCP Server Setup

The MCP server auto-registers via `.claude/settings.json` — no manual `claude mcp add` needed. The server loads `.env` from the project root at startup for API keys (`GITHUB_TOKEN`, `BRAVE_API_KEY`).

## Package Layout

`mcp_server/` lives inside `src/` (at `src/mcp_server/`) so the `uv_build` `.pth` file puts it on sys.path. Do not move it back to the project root — that breaks the `tooldb-mcp` entry point.
