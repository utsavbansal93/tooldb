"""Tests for GitHub repo health signal fetching (mocked HTTP)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx

from tooldb.assessment.github_signals import fetch_repo_health


@pytest.fixture
def github_api() -> respx.MockRouter:
    """Create a respx mock router for GitHub API."""
    with respx.mock(base_url="https://api.github.com", assert_all_called=False) as router:
        yield router


class TestFetchRepoHealth:
    @pytest.mark.asyncio
    async def test_happy_path(self, github_api: respx.MockRouter) -> None:
        now = datetime.now(UTC)
        recent = (now - timedelta(days=10)).strftime("%Y-%m-%dT%H:%M:%SZ")

        github_api.get("/repos/jgm/pandoc").respond(
            json={
                "pushed_at": recent,
                "open_issues_count": 150,
                "archived": False,
                "fork": False,
                "license": {"spdx_id": "GPL-2.0"},
            }
        )
        github_api.get("/repos/jgm/pandoc/releases").respond(
            json=[{"created_at": recent}]
        )
        github_api.get("/repos/jgm/pandoc/community/profile").respond(
            json={"files": {"security": {"url": "..."}}}
        )
        github_api.get("/repos/jgm/pandoc/contents/.github/workflows").respond(
            json=[{"name": "ci.yml"}]
        )
        github_api.get("/repos/jgm/pandoc/contents/tests").respond(json=[])

        client = httpx.AsyncClient(
            base_url="https://api.github.com", timeout=5.0
        )
        async with client:
            health = await fetch_repo_health("jgm", "pandoc", client=client)

        assert health.last_commit_date is not None
        assert health.open_issue_count == 150
        assert health.is_archived is False
        assert health.license_spdx == "GPL-2.0"
        assert health.has_recent_release is True
        assert health.release_count_1y == 1
        assert health.has_security_md is True
        assert health.has_ci is True
        assert not health.errors

    @pytest.mark.asyncio
    async def test_rate_limited(self, github_api: respx.MockRouter) -> None:
        github_api.get("/repos/foo/bar").respond(status_code=403)
        github_api.get("/repos/foo/bar/releases").respond(status_code=403)
        github_api.get("/repos/foo/bar/community/profile").respond(status_code=403)
        # Fallback file checks also rate-limited
        github_api.get("/repos/foo/bar/contents/.github/workflows").respond(status_code=403)
        github_api.get("/repos/foo/bar/contents/tests").respond(status_code=403)
        github_api.get("/repos/foo/bar/contents/test").respond(status_code=403)
        github_api.get("/repos/foo/bar/contents/spec").respond(status_code=403)
        github_api.get("/repos/foo/bar/contents/__tests__").respond(status_code=403)
        github_api.get("/repos/foo/bar/contents/SECURITY.md").respond(status_code=403)

        client = httpx.AsyncClient(
            base_url="https://api.github.com", timeout=5.0
        )
        async with client:
            health = await fetch_repo_health("foo", "bar", client=client)

        assert health.last_commit_date is None
        assert len(health.errors) > 0
        assert any("rate limit" in e.lower() for e in health.errors)

    @pytest.mark.asyncio
    async def test_repo_not_found(self, github_api: respx.MockRouter) -> None:
        github_api.get("/repos/foo/nonexistent").respond(status_code=404)
        github_api.get("/repos/foo/nonexistent/releases").respond(status_code=404)
        github_api.get("/repos/foo/nonexistent/community/profile").respond(status_code=404)
        github_api.get("/repos/foo/nonexistent/contents/.github/workflows").respond(status_code=404)
        github_api.get("/repos/foo/nonexistent/contents/tests").respond(status_code=404)
        github_api.get("/repos/foo/nonexistent/contents/test").respond(status_code=404)
        github_api.get("/repos/foo/nonexistent/contents/spec").respond(status_code=404)
        github_api.get("/repos/foo/nonexistent/contents/__tests__").respond(status_code=404)
        github_api.get("/repos/foo/nonexistent/contents/SECURITY.md").respond(status_code=404)

        client = httpx.AsyncClient(
            base_url="https://api.github.com", timeout=5.0
        )
        async with client:
            health = await fetch_repo_health("foo", "nonexistent", client=client)

        assert any("not found" in e.lower() or "404" in e for e in health.errors)

    @pytest.mark.asyncio
    async def test_malformed_json(self, github_api: respx.MockRouter) -> None:
        github_api.get("/repos/foo/bar").respond(
            content=b"not json", headers={"content-type": "application/json"}
        )
        github_api.get("/repos/foo/bar/releases").respond(json=[])
        github_api.get("/repos/foo/bar/community/profile").respond(json={"files": {}})
        github_api.get("/repos/foo/bar/contents/.github/workflows").respond(status_code=404)
        github_api.get("/repos/foo/bar/contents/tests").respond(status_code=404)
        github_api.get("/repos/foo/bar/contents/test").respond(status_code=404)
        github_api.get("/repos/foo/bar/contents/spec").respond(status_code=404)
        github_api.get("/repos/foo/bar/contents/__tests__").respond(status_code=404)

        client = httpx.AsyncClient(
            base_url="https://api.github.com", timeout=5.0
        )
        async with client:
            health = await fetch_repo_health("foo", "bar", client=client)

        assert any("malformed" in e.lower() for e in health.errors)

    @pytest.mark.asyncio
    async def test_archived_and_fork(self, github_api: respx.MockRouter) -> None:
        github_api.get("/repos/foo/bar").respond(
            json={
                "pushed_at": "2024-01-01T00:00:00Z",
                "open_issues_count": 0,
                "archived": True,
                "fork": True,
                "license": None,
            }
        )
        github_api.get("/repos/foo/bar/releases").respond(json=[])
        github_api.get("/repos/foo/bar/community/profile").respond(json={"files": {}})
        github_api.get("/repos/foo/bar/contents/.github/workflows").respond(status_code=404)
        github_api.get("/repos/foo/bar/contents/tests").respond(status_code=404)
        github_api.get("/repos/foo/bar/contents/test").respond(status_code=404)
        github_api.get("/repos/foo/bar/contents/spec").respond(status_code=404)
        github_api.get("/repos/foo/bar/contents/__tests__").respond(status_code=404)

        client = httpx.AsyncClient(
            base_url="https://api.github.com", timeout=5.0
        )
        async with client:
            health = await fetch_repo_health("foo", "bar", client=client)

        assert health.is_archived is True
        assert health.is_fork is True

    @pytest.mark.asyncio
    async def test_no_releases(self, github_api: respx.MockRouter) -> None:
        github_api.get("/repos/foo/bar").respond(
            json={"pushed_at": "2024-01-01T00:00:00Z", "open_issues_count": 0}
        )
        github_api.get("/repos/foo/bar/releases").respond(json=[])
        github_api.get("/repos/foo/bar/community/profile").respond(json={"files": {}})
        github_api.get("/repos/foo/bar/contents/.github/workflows").respond(status_code=404)
        github_api.get("/repos/foo/bar/contents/tests").respond(status_code=404)
        github_api.get("/repos/foo/bar/contents/test").respond(status_code=404)
        github_api.get("/repos/foo/bar/contents/spec").respond(status_code=404)
        github_api.get("/repos/foo/bar/contents/__tests__").respond(status_code=404)

        client = httpx.AsyncClient(
            base_url="https://api.github.com", timeout=5.0
        )
        async with client:
            health = await fetch_repo_health("foo", "bar", client=client)

        assert health.has_recent_release is False
        assert health.release_count_1y == 0
