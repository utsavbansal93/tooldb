"""Brave Search API discovery source.

Uses Brave's web search to find tools when GitHub and public-api-lists
don't have results. Requires BRAVE_API_KEY env var.
"""

from __future__ import annotations

import os

import httpx

from tooldb.discovery.base import ToolCandidate
from tooldb.logging import log_discovery

BRAVE_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"


class BraveWebSource:
    """Discover tools via Brave Web Search API."""

    source_name: str = "web"

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client
        self._owns_client = client is None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            api_key = os.environ.get("BRAVE_API_KEY", "")
            headers: dict[str, str] = {
                "Accept": "application/json",
                "Accept-Encoding": "gzip",
            }
            if api_key:
                headers["X-Subscription-Token"] = api_key
            self._client = httpx.AsyncClient(headers=headers, timeout=15.0)
        return self._client

    async def search(self, task: str, limit: int = 5) -> list[ToolCandidate]:
        """Search the web for tools matching a task.

        Appends "tool" and "API" to the query for better results.
        """
        client = await self._get_client()
        query = f"{task} tool API github"
        params = {"q": query, "count": min(limit, 20)}

        try:
            resp = await client.get(BRAVE_SEARCH_URL, params=params)
        except httpx.HTTPError as e:
            log_discovery("web", "http_error", error=str(e))
            return []

        if resp.status_code != 200:
            log_discovery("web", "api_error", status=resp.status_code)
            return []

        try:
            data = resp.json()
        except Exception:
            log_discovery("web", "invalid_json")
            return []

        results = data.get("web", {}).get("results", [])
        if not isinstance(results, list):
            return []

        candidates: list[ToolCandidate] = []
        for result in results[:limit]:
            try:
                url = str(result.get("url", ""))
                title = str(result.get("title", ""))
                desc = str(result.get("description", ""))

                # Determine type heuristic
                tool_type = "service"
                if "github.com" in url:
                    tool_type = "repo"
                elif "/api" in url.lower() or "api" in title.lower():
                    tool_type = "api"

                candidates.append(
                    ToolCandidate(
                        name=title,
                        url=url,
                        type=tool_type,
                        description=desc,
                        task_tags=[],
                        license=None,
                        stars=None,
                        auth_required=None,
                        cost_tier="unknown",
                    )
                )
            except (KeyError, TypeError):
                continue

        log_discovery("web", "search_complete", results=len(candidates), task=task)
        return candidates

    async def close(self) -> None:
        if self._client and self._owns_client:
            await self._client.aclose()
