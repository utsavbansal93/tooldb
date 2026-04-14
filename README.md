# ToolDB

Personal tool-discovery, experience-cache, and invocation system that learns from every use.

![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)
![License MIT](https://img.shields.io/badge/license-MIT-green)
![Tests 228 passed](https://img.shields.io/badge/tests-228%20passed-brightgreen)

## Why?

Every developer searches for the same tools over and over — a PDF converter, a JSON formatter, an image resizer. You find one, try it, forget the name, and search again next week.

ToolDB fixes this. It searches GitHub, 740+ public APIs, and the web, then caches what you find in a local SQLite database. When you record that a tool *works*, it becomes an instant cache hit next time. When something *breaks*, you'll never waste time on it again.

The core loop:

```
discover → try → record experience → next time it's instant
```

ToolDB runs as a **CLI** for terminal workflows, an **MCP server** for AI agents (Claude Desktop, Claude Code), or both simultaneously — sharing the same cache.

## Architecture

```
                    ┌─────────────────────────────────────────┐
   find "pdf to     │            Cascade Engine               │
    markdown"       │                                         │
        │           │  L1  Cache (status = works)      ~0 ms  │
        ▼           │   │  Your proven, working tools         │
   ┌─────────┐      │   ▼                                     │
   │ cascade │─────▶│  L2  Cache (status = untried)    ~0 ms  │
   └─────────┘      │   │  Previously discovered, not tested  │
        │           │   ▼                                     │
        │           │  L3  GitHub + Public API Lists  ~500 ms │
        │           │   │  ~740 curated APIs + GitHub search  │
        │           │   ▼                                     │
        │           │  L4  Brave Web Search          ~800 ms  │
        │           │      Fallback for obscure tools         │
        ▼           └─────────────────────────────────────────┘
   ┌─────────┐
   │ persist │──▶ ~/.tooldb/tooldb.sqlite
   └─────────┘    URL-deduplicated, schema-versioned
```

Each layer runs **only if the previous found nothing**. Results persist to SQLite with URL deduplication. A **negative cache** prevents re-searching failed queries for 7 days (bypassable with `--force`).

## Quickstart

```bash
git clone https://github.com/utsavbansal93/tooldb.git
cd tooldb
uv sync
cp .env.example .env   # fill in API keys (optional — cache layers work without them)
```

```bash
# 1. Search for a tool (hits GitHub + APIs since cache is empty)
uv run tooldb find "markdown to PDF converter"

# 2. Record your experience after trying it
uv run tooldb record 1 works --notes "fast, handles CJK"

# 3. Search again — instant L1 cache hit
uv run tooldb find "markdown to PDF converter"

# 4. List your proven tools
uv run tooldb list --status works

# 5. Check cache health
uv run tooldb stats
```

## Environment Variables

| Variable | Required for | Purpose |
|----------|-------------|---------|
| `GITHUB_TOKEN` | L3 discovery | GitHub API search (higher rate limits). [Create token](https://github.com/settings/tokens) — no special scopes needed. |
| `BRAVE_API_KEY` | L4 discovery | Brave web search fallback. [Get key](https://brave.com/search/api/). |

Without these, cache layers (L1/L2) still work. Discovery just won't reach external sources.

## CLI Reference

| Command | Arguments | Key Options | Purpose |
|---------|-----------|-------------|---------|
| `find` | `TASK` | `--max-layer N`, `--force`, `--dry-run`, `--production`, `--json` | Search for tools via L1-L4 cascade |
| `assess` | `ID` | `--skip-cve`, `--json` | Production readiness assessment |
| `record` | `ID STATUS` | `--notes TEXT` | Record experience (works/degraded/broken/avoid) |
| `list` | — | `--status`, `--json` | List cached tools |
| `delete` | `ID` | — | Remove a tool from cache |
| `merge` | `KEEP_ID DROP_ID` | — | Merge two tools (keep first, drop second) |
| `benchmark` | `ID TASK_TYPE` | `--target-type tool\|recipe` | Score a tool or recipe |
| `invoke` | `ID` | `--input key=value`, `--dry-run` | Execute a tool with rate limiting |
| `generate-wrapper` | `ID` | — | Generate a Python wrapper script |
| `recipe create` | `NAME` | `--description`, `--step "spec"` | Create a multi-tool pipeline |
| `recipe list` | — | `--status`, `--json` | List saved recipes |
| `recipe benchmark` | `ID TASK_TYPE` | — | Benchmark a recipe pipeline |
| `stats` | — | — | Cache statistics and health |
| `export` | `PATH` | — | Export database to tar.gz |
| `import` | `PATH` | `--force` | Import database from tar.gz |

## MCP Server

ToolDB exposes 12 tools via [Model Context Protocol](https://modelcontextprotocol.io/) for use by AI agents.

### Setup: Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "tooldb": {
      "command": "uv",
      "args": ["--directory", "<path-to-tooldb>", "run", "tooldb-mcp"]
    }
  }
}
```

### Setup: Claude Code

**Automatic (recommended):** The repo includes `.claude/settings.json` with the MCP server config. Just open the project in Claude Code — the server registers itself. The server also loads `.env` automatically for API keys.

**Manual (if needed):**

```bash
claude mcp add tooldb -- uv --directory <path-to-tooldb> run tooldb-mcp
```

### MCP Tool Reference

#### `find_tool` — Search for tools via L1-L4 cascade

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `task` | string | yes | — | Natural language task description |
| `max_layer` | int | no | 4 | Max cascade depth (1=cache-works, 2=cache-untried, 3=github+apis, 4=web) |
| `bypass_negative_cache` | bool | no | false | Ignore previous "no results" entries |
| `dry_run` | bool | no | false | Preview without calling external APIs |

**Returns**: `{ tools, recipes, layer_reached, negative_cached, source_timings }`

#### `record_experience` — Record tool status after use

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `tool_id` | int | yes | Tool ID |
| `status` | string | yes | `works` \| `degraded` \| `broken` \| `avoid` |
| `notes` | string | no | Freetext notes about the experience |

**Returns**: `{ status, tool, new_status }`

#### `list_my_tools` — List cached tools by status

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `status` | string | no | Filter: `works` \| `untried` \| `degraded` \| `broken` \| `avoid` |

**Returns**: Array of tool objects with id, name, url, status, tags, invocation_count

#### `run_benchmark` — Score a tool or recipe

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `target_id` | int | yes | — | Tool or recipe ID |
| `task_type` | string | yes | — | Task being benchmarked (e.g., "pdf_conversion") |
| `target_type` | string | no | "tool" | `tool` \| `recipe` |

**Returns**: `{ task_type, score, ran_at, fixture_hash }`

#### `invoke_tool` — Execute a tool with rate limiting

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `tool_id` | int | yes | — | Tool ID |
| `inputs` | object | no | null | Key-value pairs for template substitution |
| `dry_run` | bool | no | false | Preview the command without executing |

**Returns**: `{ output, status, duration_ms }`

#### `extract_tool_metadata` — LLM-powered README extraction

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `tool_id` | int | yes | Tool ID to enrich |
| `readme_url` | string | yes | URL to the tool's README |

**Returns**: `{ status, tool, extracted_fields }` — extracts install_cmd, auth_method, rate_limits, cost_tier, task_tags

#### `delete_tool` — Remove a tool from cache

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `tool_id` | int | yes | Tool ID to delete |

**Returns**: `{ status, deleted }`

#### `find_recipes` — Search existing multi-tool pipelines

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `task` | string | yes | Natural language task description |

**Returns**: Array of recipe objects with id, name, description, steps, status

#### `suggest_recipe` — Propose a new pipeline from cached tools

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `task` | string | yes | Pipeline task (e.g., "convert PDF to EPUB then email it") |

**Returns**: `{ name, description, steps }` — does NOT save, call `save_recipe` to persist

#### `save_recipe` — Persist a proposed recipe

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | string | yes | Recipe name |
| `description` | string | yes | What the recipe does |
| `steps` | array | yes | List of `{ tool_id, params, output_to_next_input }` |

**Returns**: `{ status, id, name, steps }`

#### `get_stats` — Cache statistics and health

No parameters.

**Returns**: `{ counts_by_status, total_tools, total_recipes, negative_cache_size, most_used, stale_count }`

#### `assess_production_readiness` — Production readiness check

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `tool_id` | int | yes | — | Tool ID to assess |
| `skip_cve` | bool | no | false | Skip CVE check (faster) |

**Returns**: `{ tool_id, tool_name, overall_score, flags, assessment_type, ... }` — repo health signals, license risk, CVE count, and human-readable flags

## Skill File

The file `skills/SKILL.md` teaches Claude a decision tree for optimal tool discovery:

1. **Single tool vs. pipeline?** — routes to `find_tool` or `find_recipes`/`suggest_recipe`
2. **Interpret cascade results** — explains layer_reached, negative_cached
3. **Try tools safely** — `invoke_tool` with `dry_run` first
4. **Record experience after every use** — the feedback loop that makes the system learn
5. **Benchmark when comparing** — `run_benchmark` for scoring
6. **Get overview** — `get_stats` + `list_my_tools`

**Install for Claude Code:**

```bash
# Per-project
mkdir -p .claude/skills && cp skills/SKILL.md .claude/skills/

# Global (all projects)
mkdir -p ~/.claude/skills && cp skills/SKILL.md ~/.claude/skills/
```

**Claude Desktop**: Copy the contents of `skills/SKILL.md` into a Claude Project's custom instructions. The MCP tools work without the skill — Claude just won't have the decision tree.

## Project Structure

```
tooldb/
├── pyproject.toml                 # Config, dependencies, entry points
├── .env.example                   # API key template
├── .claude/
│   └── settings.json              # Auto-registers MCP server for Claude Code
├── skills/
│   └── SKILL.md       # Decision tree for Claude
├── src/
│   ├── mcp_server/
│   │   └── server.py              # 12 FastMCP tools (auto-loads .env)
│   └── tooldb/
│   ├── models.py                  # Tool, Recipe, CascadeResult dataclasses
│   ├── cascade.py                 # L1-L4 discovery orchestrator
│   ├── cli.py                     # Click CLI (15 commands)
│   ├── invoker.py                 # Rate-limited tool execution
│   ├── invariants.py              # Data validation on write
│   ├── logging.py                 # Structured JSON logging
│   ├── assessment/
│   │   ├── production_readiness.py # Assess repo health, license, CVEs
│   │   ├── github_signals.py      # GitHub API repo health signals
│   │   ├── osv_client.py          # OSV.dev CVE lookup
│   │   ├── license_classifier.py  # SPDX → risk level
│   │   └── safety.py              # Pre-invocation safety checks
│   ├── db/
│   │   ├── cache.py               # ToolCache — CRUD, merge, find, stats
│   │   ├── schema.sql             # SQLite schema v2
│   │   └── migrations.py          # Schema init and versioning
│   ├── discovery/
│   │   ├── base.py                # ToolCandidate protocol
│   │   ├── github.py              # GitHub Search API
│   │   ├── public_apis.py         # 740+ curated public APIs
│   │   └── web.py                 # Brave Web Search
│   ├── adapters/
│   │   ├── registry.py            # LLM-powered metadata extraction
│   │   └── wrapper_generator.py   # Python wrapper generation
│   └── benchmark/
│       ├── runner.py              # Deterministic / LLM-judge / eyeball
│       └── specs.py               # Spec validation, fixture hashing
├── wrappers/                      # Generated wrapper scripts
└── tests/                         # 169 unit + property-based tests
```

## Development

```bash
uv sync --group dev
uv run ruff check src/ tests/ mcp_server/
uv run mypy src/
uv run pytest                        # 169 unit + property tests
uv run pytest -m integration         # real API tests (needs tokens)
```

## License

MIT
