# ToolDB

Personal tool-discovery, experience-cache, and invocation system. Searches your local SQLite cache first, then discovers fresh candidates from GitHub, public API lists, and the web. Results are benchmarked, cached, and made invocable through generated wrappers with rate limiting. The system learns from every use.

## Install

```bash
git clone https://github.com/utsavbansal93/tooldb.git
cd tooldb
uv sync
cp .env.example .env   # then fill in your API keys
```

### Environment variables

Copy `.env.example` to `.env` and fill in:

| Variable | Required for | Purpose |
|----------|-------------|---------|
| `GITHUB_TOKEN` | L3 discovery | GitHub API search (higher rate limits). [Create token](https://github.com/settings/tokens) — no special scopes needed. |
| `BRAVE_API_KEY` | L4 discovery | Brave web search fallback. [Get key](https://brave.com/search/api/). |

Without these, cache layers (L1/L2) still work. Discovery just won't reach external sources.

## Quickstart

```bash
# 1. Search for a tool (hits GitHub + APIs since cache is empty)
uv run tooldb find "markdown to PDF converter"

# 2. Record your experience after trying tool #1
uv run tooldb record 1 works --notes "fast, handles CJK"

# 3. List your proven tools
uv run tooldb list --status works

# 4. Check cache health
uv run tooldb stats

# 5. Search again — this time it's an instant L1 cache hit
uv run tooldb find "markdown to PDF converter"
```

## CLI reference

```bash
tooldb find "<task>" [--max-layer N] [--force] [--dry-run] [--json]
tooldb record <id> works|broken|degraded|avoid [--notes "..."]
tooldb list [--status works] [--json]
tooldb delete <id>
tooldb merge <keep_id> <drop_id>
tooldb benchmark <id> <task_type> [--target-type tool|recipe]
tooldb invoke <id> [--input key=value ...] [--dry-run]
tooldb generate-wrapper <id>
tooldb recipe create <name> --description "..." --step "tool_id=1,params={}" ...
tooldb recipe list [--status works] [--json]
tooldb recipe benchmark <recipe_id> <task_type>
tooldb stats
tooldb export <path>
tooldb import <path> [--force]
```

## MCP server (Claude Desktop)

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "tooldb": {
      "command": "uv",
      "args": ["--directory", "/Users/utsavbansal/code/tooldb", "run", "tooldb-mcp"]
    }
  }
}
```

This exposes 11 tools: `find_tool`, `record_experience`, `list_my_tools`, `run_benchmark`, `invoke_tool`, `extract_tool_metadata`, `delete_tool`, `find_recipes`, `suggest_recipe`, `save_recipe`, `get_stats`.

## Skill installation

The file `skills/tool-search.skill.md` teaches Claude the decision tree for using the MCP tools (when to search cache vs. web, how to record experience, when to suggest recipes).

**Claude Code**: Copy to your project or global skills directory:

```bash
# Per-project
mkdir -p .claude/skills && cp skills/tool-search.skill.md .claude/skills/

# Global (all projects)
mkdir -p ~/.claude/skills && cp skills/tool-search.skill.md ~/.claude/skills/
```

**Claude Desktop**: Claude Desktop does not currently support loading skill files from the filesystem. Workaround: copy the contents of `skills/tool-search.skill.md` into a Claude Project's custom instructions. The MCP tools will still work without the skill — Claude just won't have the decision tree for optimal usage.

## Architecture

```
L1: Cache (status=works)      — instant, your proven tools
L2: Cache (status=untried)    — previously discovered, not yet tested
L3: GitHub + public-api-lists — ~740 curated APIs + GitHub search
L4: Brave web search          — fallback for obscure tools
```

Each layer runs only if the previous found nothing. Results persist to `~/.tooldb/tooldb.sqlite` with URL deduplication. Negative cache prevents re-searching failed queries for 7 days (bypassable with `--force`).

## Development

```bash
uv sync --group dev
uv run ruff check src/ tests/ mcp_server/
uv run mypy src/
uv run pytest                        # 167 unit + property tests
uv run pytest -m integration         # real API tests (needs tokens)
```

## License

MIT
