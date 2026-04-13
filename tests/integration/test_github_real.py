"""Integration test: real GitHub API search.

Requires GITHUB_TOKEN env var. Run with: pytest -m integration
"""

from __future__ import annotations

import os

import pytest

from tooldb.discovery.github import GitHubSource

pytestmark = pytest.mark.integration


@pytest.mark.skipif(
    not os.environ.get("GITHUB_TOKEN"),
    reason="GITHUB_TOKEN not set",
)
class TestGitHubRealSearch:
    @pytest.mark.asyncio
    async def test_search_returns_results(self) -> None:
        source = GitHubSource()
        try:
            results = await source.search("markdown pdf converter", limit=3)
            assert isinstance(results, list)
            if results:
                assert "name" in results[0]
                assert "url" in results[0]
                assert results[0]["type"] == "repo"
        finally:
            await source.close()

    @pytest.mark.asyncio
    async def test_result_shape(self) -> None:
        source = GitHubSource()
        try:
            results = await source.search("json parser", limit=2)
            for r in results:
                assert isinstance(r["name"], str)
                assert r["url"].startswith("https://")
                assert isinstance(r["task_tags"], list)
        finally:
            await source.close()
