"""Public API Lists discovery source.

Fetches from https://public-api-lists.github.io/public-api-lists/api/all.json
(~740 entries). Cached in-memory per session. Token-matches against
name, description, and category.
"""

from __future__ import annotations

import httpx

from tooldb.discovery.base import ToolCandidate
from tooldb.logging import log_discovery
from tooldb.models import tokenize_task

ALL_JSON_URL = "https://public-api-lists.github.io/public-api-lists/api/all.json"


class PublicApisSource:
    """Discover tools from the public-api-lists curated collection."""

    source_name: str = "public_apis"

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client
        self._owns_client = client is None
        self._cache: list[dict[str, object]] | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=15.0)
        return self._client

    async def _fetch_all(self) -> list[dict[str, object]]:
        """Fetch and cache the full API list. Returns cached copy on subsequent calls."""
        if self._cache is not None:
            return self._cache

        client = await self._get_client()
        try:
            resp = await client.get(ALL_JSON_URL)
        except httpx.HTTPError as e:
            log_discovery("public_apis", "http_error", error=str(e))
            return []

        if resp.status_code != 200:
            log_discovery("public_apis", "fetch_error", status=resp.status_code)
            return []

        try:
            data = resp.json()
        except Exception:
            log_discovery("public_apis", "invalid_json")
            return []

        if not isinstance(data, list):
            log_discovery("public_apis", "unexpected_shape", detail="root is not a list")
            return []

        self._cache = data
        log_discovery("public_apis", "fetched", count=len(data))
        return data

    async def search(self, task: str, limit: int = 5) -> list[ToolCandidate]:
        """Search cached API entries by token-matching on name, description, category."""
        entries = await self._fetch_all()
        if not entries:
            return []

        tokens = tokenize_task(task)
        if not tokens:
            return []

        scored: list[tuple[int, dict[str, object]]] = []
        for entry in entries:
            try:
                name = str(entry.get("name", "")).lower()
                desc = str(entry.get("description", "")).lower()
                category = str(entry.get("category", "")).lower()
            except (AttributeError, TypeError):
                continue

            score = 0
            searchable = f"{name} {desc} {category}"
            for token in tokens:
                if token in searchable:
                    score += 1

            if score > 0:
                scored.append((score, entry))

        scored.sort(key=lambda x: x[0], reverse=True)

        candidates: list[ToolCandidate] = []
        for _, entry in scored[:limit]:
            try:
                auth_val = str(entry.get("auth", "No"))
                candidates.append(
                    ToolCandidate(
                        name=str(entry.get("name", "")),
                        url=str(entry.get("url", "")),
                        type="api",
                        description=str(entry.get("description", "")),
                        task_tags=[str(entry.get("category", ""))],
                        license=None,
                        stars=None,
                        auth_required=auth_val != "No" and auth_val != "",
                        cost_tier="free",
                    )
                )
            except (KeyError, TypeError):
                continue

        log_discovery("public_apis", "search_complete", results=len(candidates), task=task)
        return candidates

    async def close(self) -> None:
        if self._client and self._owns_client:
            await self._client.aclose()
