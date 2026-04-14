# 0002: Auto-register MCP server and load .env at startup

## Context

Users had to manually run `claude mcp add tooldb -- uv --directory <path> run tooldb-mcp` and pass `-e GITHUB_TOKEN=...` to wire env vars. This meant the skill's MCP tools weren't available until manual setup was done — defeating the "just works" goal.

## Decision

1. Add `.claude/settings.json` with the MCP server config so Claude Code auto-registers it when opening the project.
2. Add a `_load_dotenv()` function at the top of `server.py` that reads `.env` from the project root before any other imports. No new dependency — just a simple line parser that skips comments and doesn't override existing env vars.

## Consequences

- Clone + open in Claude Code = MCP tools available immediately
- Users put API keys in `.env` (gitignored) and they're picked up automatically
- No dependency on `python-dotenv` — fewer moving parts
- The `.env` path is relative to `server.py` location (`../../.env` from `src/mcp_server/`), so it breaks if the file moves — but decision 0001 locks the location
