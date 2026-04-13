"""Tests for GitHub discovery source.

All tests mock httpx — no real API calls.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from tooldb.discovery.github import AuthenticationError, GitHubSource

SEARCH_URL = "https://api.github.com/search/repositories"

MOCK_RESPONSE = {
    "total_count": 2,
    "items": [
        {
            "full_name": "jgm/pandoc",
            "name": "pandoc",
            "html_url": "https://github.com/jgm/pandoc",
            "description": "Universal document converter",
            "topics": ["markdown", "pdf", "converter"],
            "stargazers_count": 30000,
            "private": False,
            "license": {"spdx_id": "GPL-2.0"},
        },
        {
            "full_name": "wkhtmltopdf/wkhtmltopdf",
            "name": "wkhtmltopdf",
            "html_url": "https://github.com/wkhtmltopdf/wkhtmltopdf",
            "description": "Convert HTML to PDF",
            "topics": ["html", "pdf"],
            "stargazers_count": 12000,
            "private": False,
            "license": {"spdx_id": "LGPL-3.0"},
        },
    ],
}


# ──────────────────── Happy path ────────────────────


class TestGitHubHappyPath:
    @respx.mock
    @pytest.mark.asyncio
    async def test_parses_repos_correctly(self) -> None:
        respx.get(SEARCH_URL).mock(return_value=httpx.Response(200, json=MOCK_RESPONSE))

        source = GitHubSource()
        results = await source.search("markdown pdf converter")
        await source.close()

        assert len(results) == 2
        assert results[0]["name"] == "jgm/pandoc"
        assert results[0]["url"] == "https://github.com/jgm/pandoc"
        assert results[0]["type"] == "repo"
        assert "markdown" in results[0]["task_tags"]
        assert results[0]["license"] == "GPL-2.0"
        assert results[0]["stars"] == 30000
        assert results[0]["cost_tier"] == "free"

    @respx.mock
    @pytest.mark.asyncio
    async def test_respects_limit(self) -> None:
        respx.get(SEARCH_URL).mock(return_value=httpx.Response(200, json=MOCK_RESPONSE))

        source = GitHubSource()
        results = await source.search("pdf", limit=1)
        await source.close()

        assert len(results) == 1

    @respx.mock
    @pytest.mark.asyncio
    async def test_source_name(self) -> None:
        source = GitHubSource()
        assert source.source_name == "github"
        await source.close()


# ──────────────────── Failure modes ────────────────────


class TestGitHubFailures:
    @respx.mock
    @pytest.mark.asyncio
    async def test_malformed_json(self) -> None:
        respx.get(SEARCH_URL).mock(
            return_value=httpx.Response(
                200, content=b"not json", headers={"content-type": "text/plain"}
            )
        )

        source = GitHubSource()
        results = await source.search("test")
        await source.close()
        assert results == []

    @respx.mock
    @pytest.mark.asyncio
    async def test_empty_items(self) -> None:
        respx.get(SEARCH_URL).mock(
            return_value=httpx.Response(200, json={"total_count": 0, "items": []})
        )

        source = GitHubSource()
        results = await source.search("nonexistent tool xyz123")
        await source.close()
        assert results == []

    @respx.mock
    @pytest.mark.asyncio
    async def test_rate_limited_403(self) -> None:
        respx.get(SEARCH_URL).mock(return_value=httpx.Response(403, json={"message": "rate limit"}))

        source = GitHubSource()
        results = await source.search("test")
        await source.close()
        assert results == []

    @respx.mock
    @pytest.mark.asyncio
    async def test_auth_error_401_raises(self) -> None:
        respx.get(SEARCH_URL).mock(
            return_value=httpx.Response(401, json={"message": "bad credentials"})
        )

        source = GitHubSource()
        with pytest.raises(AuthenticationError, match="GITHUB_TOKEN"):
            await source.search("test")
        await source.close()

    @respx.mock
    @pytest.mark.asyncio
    async def test_server_error_5xx(self) -> None:
        respx.get(SEARCH_URL).mock(
            return_value=httpx.Response(500, json={"message": "internal error"})
        )

        source = GitHubSource()
        results = await source.search("test")
        await source.close()
        assert results == []

    @respx.mock
    @pytest.mark.asyncio
    async def test_unexpected_shape_items_not_list(self) -> None:
        respx.get(SEARCH_URL).mock(
            return_value=httpx.Response(200, json={"total_count": 0, "items": "not a list"})
        )

        source = GitHubSource()
        results = await source.search("test")
        await source.close()
        assert results == []
