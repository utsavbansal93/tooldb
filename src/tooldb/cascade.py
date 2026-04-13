"""L1→L4 cascade orchestrator for tool discovery.

L1: Cache (status=works) — tools + recipes
L2: Cache (status=untried) — optionally benchmark
L3: GitHub + public-api-lists discovery
L4: Brave web search discovery

Discovered candidates are written to cache → assigned IDs → then benchmarked.
URL-deduped via INSERT OR IGNORE on normalized URL.
Every layer decision is logged via structured logging.
"""

from __future__ import annotations

import asyncio
import time

from tooldb.benchmark.runner import BenchmarkRunner
from tooldb.db.cache import ToolCache
from tooldb.discovery.base import DiscoverySource, ToolCandidate
from tooldb.discovery.github import AuthenticationError, GitHubSource
from tooldb.discovery.public_apis import PublicApisSource
from tooldb.discovery.web import BraveWebSource
from tooldb.logging import log_cascade_decision
from tooldb.models import CascadeResult, Tool, normalize_url, task_signature


class Cascade:
    """L1→L4 tool and recipe discovery orchestrator."""

    def __init__(
        self,
        cache: ToolCache,
        *,
        sources: list[DiscoverySource] | None = None,
        benchmark_runner: BenchmarkRunner | None = None,
    ) -> None:
        self._cache = cache
        self._sources = sources
        self._benchmark_runner = benchmark_runner or BenchmarkRunner()

    def _default_sources(self) -> list[DiscoverySource]:
        return [GitHubSource(), PublicApisSource(), BraveWebSource()]

    async def find(
        self,
        task: str,
        *,
        max_layer: int = 4,
        run_benchmark: bool = False,
        ab_test: bool = False,
        bypass_negative_cache: bool = False,
        negative_ttl_days: int = 7,
        dry_run: bool = False,
    ) -> CascadeResult:
        """Run the cascade search.

        Args:
            task: Natural language task description.
            max_layer: Maximum layer to reach (1-4). Clamped.
            run_benchmark: Whether to benchmark untried candidates.
            ab_test: Run all candidates in parallel for comparison.
            bypass_negative_cache: Ignore negative cache entries.
            negative_ttl_days: TTL for negative cache entries.
            dry_run: Log what would happen without calling external APIs.

        Returns:
            CascadeResult with tools, recipes, layer info, and timings.
        """
        max_layer = max(0, min(4, max_layer))
        if max_layer == 0:
            log_cascade_decision("max_layer_zero", task=task)
            return CascadeResult()

        task_sig = task_signature(task)
        result = CascadeResult()
        result.source_timings = {}

        # Check negative cache
        if not bypass_negative_cache and self._cache.is_negatively_cached(
            task_sig, ttl_days=negative_ttl_days
        ):
            log_cascade_decision("negative_cache_hit", task=task, sig=task_sig)
            result.negative_cached = True
            return result

        # ── L1: cache, status=works ──
        if max_layer >= 1:
            t0 = time.monotonic()
            tools_works = self._cache.find_by_task(task, status="works")
            recipes_works = self._cache.find_recipes_by_task(task)
            recipes_works = [r for r in recipes_works if r.my_status == "works"]
            elapsed = time.monotonic() - t0
            result.source_timings["L1_cache_works"] = elapsed

            if tools_works or recipes_works:
                log_cascade_decision(
                    "L1_hit",
                    task=task,
                    tools=len(tools_works),
                    recipes=len(recipes_works),
                )
                result.tools = tools_works
                result.recipes = recipes_works
                result.layer_reached = 1
                return result

            log_cascade_decision("L1_miss", task=task)

        # ── L2: cache, status=untried ──
        if max_layer >= 2:
            t0 = time.monotonic()
            tools_untried = self._cache.find_by_task(task, status="untried")
            elapsed = time.monotonic() - t0
            result.source_timings["L2_cache_untried"] = elapsed

            if tools_untried:
                log_cascade_decision(
                    "L2_hit", task=task, tools=len(tools_untried)
                )
                result.tools = tools_untried
                result.layer_reached = 2

                if run_benchmark:
                    for tool in tools_untried:
                        if tool.id is not None:
                            result.per_candidate_scores[tool.id] = 0.0

                return result

            log_cascade_decision("L2_miss", task=task)

        # ── L3: GitHub + public-api-lists discovery ──
        if max_layer >= 3:
            if dry_run:
                log_cascade_decision("L3_dry_run", task=task)
            else:
                sources = self._sources if self._sources is not None else self._default_sources()
                l3_sources = [s for s in sources if s.source_name in ("github", "public_apis")]
                candidates, timings = await self._run_discovery(l3_sources, task)
                result.source_timings.update(timings)

                if candidates:
                    tools = self._persist_candidates(candidates)
                    result.tools.extend(tools)
                    result.layer_reached = 3
                    for t in tools:
                        if t.id is not None:
                            result.per_candidate_scores[t.id] = 0.0

                    log_cascade_decision(
                        "L3_hit", task=task, candidates=len(candidates), persisted=len(tools)
                    )
                else:
                    log_cascade_decision("L3_miss", task=task)

        # ── L4: Brave web search ──
        if max_layer >= 4 and not result.tools:
            if dry_run:
                log_cascade_decision("L4_dry_run", task=task)
            else:
                sources = self._sources if self._sources is not None else self._default_sources()
                l4_sources = [s for s in sources if s.source_name == "web"]
                candidates, timings = await self._run_discovery(l4_sources, task)
                result.source_timings.update(timings)

                if candidates:
                    tools = self._persist_candidates(candidates)
                    result.tools.extend(tools)
                    result.layer_reached = 4
                    for t in tools:
                        if t.id is not None:
                            result.per_candidate_scores[t.id] = 0.0

                    log_cascade_decision(
                        "L4_hit", task=task, candidates=len(candidates), persisted=len(tools)
                    )
                else:
                    log_cascade_decision("L4_miss", task=task)

        # If still nothing found, write to negative cache
        if not result.tools and not result.recipes and not dry_run:
            self._cache.add_negative(task_sig, f"No results found for: {task}")
            log_cascade_decision("negative_cache_write", task=task, sig=task_sig)

        if not result.layer_reached and result.tools:
            result.layer_reached = max_layer

        return result

    async def _run_discovery(
        self,
        sources: list[DiscoverySource],
        task: str,
    ) -> tuple[list[ToolCandidate], dict[str, float]]:
        """Run multiple discovery sources concurrently.

        If one source raises, others still run. Auth errors are re-raised.
        Returns (all_candidates, timing_dict).
        """
        all_candidates: list[ToolCandidate] = []
        timings: dict[str, float] = {}
        auth_error: AuthenticationError | None = None

        async def run_one(source: DiscoverySource) -> list[ToolCandidate]:
            nonlocal auth_error
            t0 = time.monotonic()
            try:
                results = await source.search(task)
                timings[f"discovery_{source.source_name}"] = time.monotonic() - t0
                return results
            except AuthenticationError as e:
                timings[f"discovery_{source.source_name}"] = time.monotonic() - t0
                auth_error = e
                return []
            except Exception as e:
                timings[f"discovery_{source.source_name}"] = time.monotonic() - t0
                log_cascade_decision(
                    "discovery_error",
                    source=source.source_name,
                    error=str(e),
                )
                return []

        tasks = [run_one(s) for s in sources]
        results = await asyncio.gather(*tasks)

        for batch in results:
            all_candidates.extend(batch)

        if auth_error is not None:
            raise auth_error

        return all_candidates, timings

    def _persist_candidates(self, candidates: list[ToolCandidate]) -> list[Tool]:
        """Write discovery candidates to cache. URL-deduped. Returns persisted tools."""
        persisted: list[Tool] = []
        seen_urls: set[str] = set()

        for c in candidates:
            url = normalize_url(c["url"])
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)

            tool = Tool(
                name=c["name"],
                url=url,
                type=c["type"],  # type: ignore[arg-type]
                task_tags=c.get("task_tags", []) or [],
                license=c.get("license"),
                auth_required=bool(c.get("auth_required")),
                cost_tier=c.get("cost_tier") or "unknown",  # type: ignore[arg-type]
                source=self._source_name_to_tool_source(c),  # type: ignore[arg-type]
                my_status="untried",
            )

            try:
                persisted_tool = self._cache.upsert(tool)
                persisted.append(persisted_tool)
            except Exception as e:
                log_cascade_decision(
                    "persist_error", url=url, error=str(e)
                )

        return persisted

    @staticmethod
    def _source_name_to_tool_source(candidate: ToolCandidate) -> str:
        """Infer the tool source from the candidate's origin."""
        url = candidate.get("url", "")
        if "github.com" in url:
            return "github"
        if candidate.get("cost_tier") == "free" and candidate.get("type") == "api":
            return "public_apis"
        return "web"
