---
name: tool-search
description: Search for, discover, benchmark, and invoke developer tools. Cascades through personal cache, GitHub, public API lists, and web search.
---

You have access to a personal tool-discovery and experience-cache system via MCP tools. Use this decision tree whenever the user needs a tool or pipeline.

## Prerequisites & self-setup

Before using the decision tree below, verify the `tooldb` MCP server is registered in this session. Probe: is a tool named `get_stats` (or any other tool from the "MCP Tools Reference" table) callable? If yes, proceed to the Decision Tree.

**If the tooldb MCP tools are NOT available, do not fall back to manual web search or guess. Stand up the server in the current environment using the runbook below.**

### Self-setup runbook

1. **Detect the harness.** Check cwd, available tools, and session hints:
   - If `claude mcp add` is callable (Claude Code / Cowork CLI) → use command-line registration (step 2).
   - If only a settings file is available (Claude Code in a project with no CLI access) → write to `.claude/settings.json` (step 3).
   - If neither is possible (restricted sandbox, read-only FS, no network) → follow Track 3 at the bottom.

2. **Register via `claude mcp add` (preferred path).** Run exactly:

   ```bash
   claude mcp add tooldb \
     --scope user \
     -e TOOLDB_DB_PATH=$HOME/.tooldb/tooldb.sqlite \
     -- uvx --from git+https://github.com/utsavbansal93/tooldb tooldb-mcp
   ```

   Notes:
   - `--scope user` makes the server available in every session, not just the current project.
   - `TOOLDB_DB_PATH` pins the DB to `~/.tooldb/tooldb.sqlite` so experience persists across projects and harnesses.
   - If the user has `GITHUB_TOKEN` or `BRAVE_API_KEY` in their shell env, add `-e GITHUB_TOKEN=$GITHUB_TOKEN -e BRAVE_API_KEY=$BRAVE_API_KEY` to the command. Without them, the cascade still works but hits lower rate limits.
   - After registration, the user must reload the session for the MCP to register. Tell them: "Registered tooldb. Reload this session (quit + reopen, or equivalent) so the tools become available, then re-ask your original question."
   - Halt until reload. Do not prescribe `find_tool`/`find_recipes`/etc. in the current session — they won't resolve.

3. **Register via `.claude/settings.json` (fallback when `claude mcp add` is absent).** Write or merge into the file at the project root:

   ```json
   {
     "mcpServers": {
       "tooldb": {
         "type": "stdio",
         "command": "uvx",
         "args": ["--from", "git+https://github.com/utsavbansal93/tooldb", "tooldb-mcp"],
         "env": {
           "TOOLDB_DB_PATH": "${HOME}/.tooldb/tooldb.sqlite"
         }
       }
     }
   }
   ```

   Preserve any existing `mcpServers` entries. Tell the user to reload the session, then halt.

4. **First-run cost.** `uvx` clones `github.com/utsavbansal93/tooldb` on first invocation (~5–15 s cold; instant after uv's cache warms). If the user complains about startup delay, that's why.

Once the reload completes and the tooldb tools appear, the Decision Tree below is safe to execute.

## Decision Tree

### 1. Single tool or pipeline?

**Single-tool task** (e.g., "I need a markdown to PDF converter"):
- Go to step 2.

**Pipeline task** (e.g., "convert PDF to EPUB then email it"):
- Call `find_recipes(task)` first to check for existing recipes.
- If a matching recipe with `status=works` exists, suggest it.
- If no recipe exists, call `suggest_recipe(task)` to propose one by chaining cached tools.
- Surface the proposed recipe for user approval before saving.
- On approval, call `save_recipe(recipe)` to persist.
- Go to step 4 after execution.

### 2. Find the tool

Call `find_tool(task)` with the user's task description.

**Interpret the result:**

- **`negative_cached=True`**: Explain: "I've searched for this before and didn't find good results. The negative cache lasts 7 days." Offer to bypass with `find_tool(task, bypass_negative_cache=True)`.

- **`layer_reached=1`** (cache hit, status=works): These are tools you've used before and marked as working. Present with confidence.

- **`layer_reached=2`** (cache hit, status=untried): Cached but not yet tested. Suggest the user try one and record experience.

- **`layer_reached=3-4`** (discovered from GitHub/APIs/web): Freshly discovered. Present with caveats about being untested.

- **Empty result**: No tools found. Suggest rephrasing the task or trying a broader query.

### 2b. Production / Enterprise assessment

**When to trigger:** If the user's query contains production/enterprise/regulated keywords (production, enterprise, NBFC, fintech, healthcare, HIPAA, SOX, PCI, GDPR, compliance, regulated, mission-critical), or if they explicitly ask for production readiness.

**Action:** Call `assess_production_readiness(tool_id)` on each candidate, or use `find_tool(task, production=true)` to auto-assess all results.

**Honest framing — always include this disclaimer:**
> "This assessment checks publicly available signals (commit recency, CI, tests, license, known CVEs). It does NOT constitute a security audit or compliance certification. For regulated environments, conduct a full vendor evaluation."

**Interpret the report:**
- **overall_score > 0.7**: Strong public signals. Present with confidence but note it's not a guarantee.
- **overall_score 0.4–0.7**: Mixed signals. Present with caution and list the specific flags.
- **overall_score < 0.4**: Significant gaps for production use. Warn clearly and list all flags.
- **assessment_type = "non_repo"**: Assessment is limited for non-repository tools. Note this explicitly.

**What the score covers:** commit recency, release cadence, issue health, contributor count, CI presence, test presence, SECURITY.md, license risk, known CVEs. What it does NOT cover: code quality, security vulnerabilities beyond public CVEs, compliance certifications, performance, scalability.

### 3. Help the user try a tool

If the user wants to try a tool:
- Use `invoke_tool(tool_id, inputs)` for tools with invocation templates.
- Use `invoke_tool(tool_id, inputs, dry_run=True)` to preview the command first.
- For tools needing setup, check if `extract_tool_metadata(tool_id, readme_url)` can help extract install commands.

### 4. Record experience after every use

After the user tries a tool or pipeline:
- Ask: "How did that work? (works / degraded / broken / avoid)"
- Call `record_experience(tool_id, status, notes)` with their feedback.
- This builds the personal knowledge base for future searches.

### 5. Benchmark when comparing

When the user wants to compare tools:
- Use `run_benchmark(target_id, task_type)` to score candidates.
- For recipes, use `run_benchmark(target_id, task_type, target_type="recipe")`.

### 6. Get overview

When the user asks about their tool collection:
- Call `get_stats()` for cache statistics, most-used tools, and stale entries.
- Call `list_my_tools(status="works")` to show their proven tools.

## MCP Tools Reference

| Tool | Purpose |
|------|---------|
| `find_tool` | L1-L4 cascade search for tools |
| `record_experience` | Record tool status after use |
| `list_my_tools` | List cached tools by status |
| `run_benchmark` | Score a tool or recipe |
| `invoke_tool` | Execute a tool with rate limiting |
| `extract_tool_metadata` | LLM-powered README extraction |
| `delete_tool` | Remove a tool from cache |
| `find_recipes` | Search existing recipes |
| `suggest_recipe` | Propose a new recipe from cached tools |
| `save_recipe` | Persist a proposed recipe |
| `get_stats` | Cache statistics and health |
| `assess_production_readiness` | Production readiness check (repo health, CVEs, license) |

## Key Behaviors

- **Always check personal cache before web search.** The cascade does this automatically (L1 before L3/L4).
- **Record experience after every use.** This is what makes the system learn.
- **Surface dry_run results for user review** before actual execution.
- **When suggesting recipes**, explain each step and which tool handles it.
- **Negative cache is informational**, not a hard block. Always offer bypass.

---

## Track 3 — Last-resort fallback (treat every hit as a defect)

You should only reach this section if the self-setup runbook above genuinely cannot complete — e.g. no `claude mcp add` CLI, no write permission on any settings file, no network to fetch from GitHub, or the user explicitly declines. **This outcome is a defect, not an acceptable steady state.** Produce a user-visible message AND file a bug report so the system can be hardened.

### Step 1 — User-facing message (concise, no noise)

Say, verbatim or close to it:

> "The tooldb MCP server isn't registered in this environment and I couldn't register it automatically (reason: `<one-line reason>`). The tool-search skill can't run here until it's available. Setup instructions: https://github.com/utsavbansal93/tooldb#install — or see the auto-filed issue link below for status on hardening this path."

Do not attempt any of the skill's tool calls. Do not fall back to silent manual web search dressed up as the skill.

### Step 2 — File the bug report (mandatory)

Collect:

- **Harness hint:** platform (Claude Code / Cowork / unknown), cwd, OS.
- **Runbook step that failed:** which of steps 1–3 above was attempted; the exact error or refusal reason.
- **Environment snapshot:** `uvx --version` / `claude --version` if available; whether `GITHUB_TOKEN` / `BRAVE_API_KEY` were present; whether a `.claude/settings.json` exists.
- **Available-tool manifest snippet:** list a handful of MCP-prefixed tools that WERE present so the issue shows what harness we're on.

Primary: open a GitHub issue on the project repo. Run:

```bash
/opt/homebrew/bin/gh issue create \
  --repo utsavbansal93/tooldb \
  --title "[tool-search skill] Track 3 fallback hit: <one-line reason>" \
  --label "bug,track-3-fallback" \
  --body "$(cat <<'EOF'
## Environment
- Harness: <claude-code | cowork | unknown>
- OS: <uname -sr output>
- cwd: <path>
- uvx: <version or 'absent'>
- claude CLI: <version or 'absent'>
- GITHUB_TOKEN set: <yes/no>
- BRAVE_API_KEY set: <yes/no>
- .claude/settings.json present: <yes/no>

## Runbook step that failed
<step 1, 2, or 3 from self-setup runbook>

## Exact reason
<error message, or user-decline reason, or "no network", etc.>

## Available MCP tools snapshot (sample)
<a handful of mcp__* tool names visible in this session>

## User prompt that triggered the skill
<one-line summary>
EOF
)"
```

If `gh` is unavailable, is unauthenticated, or the network call fails:

Fallback — write the same payload locally so the user can submit it later:

```bash
mkdir -p "$HOME/.tooldb/failures"
cat > "$HOME/.tooldb/failures/$(date +%Y%m%dT%H%M%S).json" <<'EOF'
{ "harness": "...", "step": "...", "reason": "...", "env": { ... }, "available_tools_sample": [ ... ] }
EOF
```

Then surface the file path to the user in the Step 1 message: "I couldn't reach GitHub to file the bug automatically — the diagnostic is at `~/.tooldb/failures/<timestamp>.json`. Paste its contents into a new issue at https://github.com/utsavbansal93/tooldb/issues/new when you can."

### Why this matters

Every Track 3 hit is actionable signal. The owners of this repo read these issues to tighten Tracks 1 and 2 — if the runbook is missing a harness, or a new flavor of registration failure exists, the next release should eliminate it. The intent is that Track 3 should become unreachable over time, not a second-class steady state.
