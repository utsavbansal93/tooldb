---
name: tool-search
description: Search for, discover, benchmark, and invoke developer tools. Cascades through personal cache, GitHub, public API lists, and web search.
---

You have access to a personal tool-discovery and experience-cache system via MCP tools. Use this decision tree whenever the user needs a tool or pipeline.

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

## Key Behaviors

- **Always check personal cache before web search.** The cascade does this automatically (L1 before L3/L4).
- **Record experience after every use.** This is what makes the system learn.
- **Surface dry_run results for user review** before actual execution.
- **When suggesting recipes**, explain each step and which tool handles it.
- **Negative cache is informational**, not a hard block. Always offer bypass.
