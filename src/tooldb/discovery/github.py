"""GitHub Search API discovery source.

Searches GitHub repositories by topic, name, and description.
Requires GITHUB_TOKEN env var for authenticated requests (higher rate limits).
"""

from __future__ import annotations

import os

import httpx

from tooldb.discovery.base import ToolCandidate
from tooldb.logging import log_discovery


class AuthenticationError(Exception):
    """Raised when GitHub returns 401 (bad/missing token)."""


class GitHubSource:
    """Discover tools via GitHub Search API."""

    source_name: str = "github"

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client
        self._owns_client = client is None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            token = os.environ.get("GITHUB_TOKEN", "")
            headers: dict[str, str] = {"Accept": "application/vnd.github+json"}
            if token:
                headers["Authorization"] = f"Bearer {token}"
            self._client = httpx.AsyncClient(
                base_url="https://api.github.com",
                headers=headers,
                timeout=15.0,
            )
        return self._client

    async def search(self, task: str, limit: int = 5) -> list[ToolCandidate]:
        """Search GitHub repos matching a task description.

        Returns up to `limit` ToolCandidate entries.
        Handles rate limits (403) and auth errors (401) gracefully.
        """
        client = await self._get_client()

        # Build search query: use the task as keywords + filter for good repos
        query = f"{task} in:name,description,topics stars:>10"
        params: dict[str, str | int] = {
            "q": query,
            "sort": "stars",
            "order": "desc",
            "per_page": min(limit, 30),
        }

        try:
            resp = await client.get("/search/repositories", params=params)
        except httpx.HTTPError as e:
            log_discovery("github", "http_error", error=str(e))
            return []

        if resp.status_code == 401:
            log_discovery("github", "auth_error", status=401)
            raise AuthenticationError("GitHub returned 401 — check GITHUB_TOKEN env var")

        if resp.status_code == 403:
            log_discovery("github", "rate_limited", status=403)
            return []

        if resp.status_code >= 400:
            log_discovery("github", "api_error", status=resp.status_code)
            return []

        try:
            data = resp.json()
        except Exception:
            log_discovery("github", "malformed_json")
            return []

        items = data.get("items", [])
        if not isinstance(items, list):
            log_discovery("github", "unexpected_shape", detail="items is not a list")
            return []

        candidates: list[ToolCandidate] = []
        for item in items[:limit]:
            try:
                candidates.append(
                    ToolCandidate(
                        name=item.get("full_name", item.get("name", "")),
                        url=item.get("html_url", ""),
                        type="repo",
                        description=item.get("description", "") or "",
                        task_tags=item.get("topics", []) or [],
                        license=_extract_license(item),
                        stars=item.get("stargazers_count"),
                        auth_required=None,
                        cost_tier="free" if not item.get("private") else "unknown",
                    )
                )
            except (KeyError, TypeError):
                continue

        log_discovery("github", "search_complete", results=len(candidates), task=task)
        return candidates

    async def close(self) -> None:
        if self._client and self._owns_client:
            await self._client.aclose()


def _extract_license(item: dict[str, object]) -> str | None:
    """Extract license SPDX ID from a GitHub repo item."""
    lic = item.get("license")
    if isinstance(lic, dict):
        return lic.get("spdx_id")  # type: ignore[return-value]
    return None
