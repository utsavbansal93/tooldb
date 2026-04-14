# 0001: Move mcp_server into src/

## Context

The `mcp_server/` package was at the project root while `uv_build` only adds `src/` to sys.path via `.pth`. This meant `uv run tooldb-mcp` (the installed entry point) failed with `ModuleNotFoundError`, even though `uv run python -m mcp_server.server` worked (because `-m` adds CWD to sys.path).

## Decision

Move `mcp_server/` into `src/mcp_server/` alongside `src/tooldb/`. The entry point in `pyproject.toml` stays unchanged (`mcp_server.server:main`) and now resolves correctly.

## Consequences

- `uv run tooldb-mcp` works out of the box
- All import paths stay the same (`from mcp_server.server import ...`)
- The `.pth` file covers both packages without any build config changes
- Must not move `mcp_server/` back to project root
