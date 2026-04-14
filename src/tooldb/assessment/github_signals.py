"""GitHub API integration for repository health signals.

Fetches commit recency, release cadence, issue health, contributor count,
CI/tests/SECURITY.md presence, and license from the GitHub REST API.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import httpx

from tooldb.logging import logger

_GITHUB_URL_RE = re.compile(
    r"github\.com/([^/]+)/([^/?.#]+)", re.IGNORECASE
)


@dataclass
class GitHubRepoHealth:
    """Health signals extracted from GitHub API."""

    last_commit_date: datetime | None = None
    has_recent_release: bool | None = None
    release_count_1y: int | None = None
    open_issue_count: int | None = None
    avg_issue_age_days: float | None = None
    contributor_count: int | None = None
    has_ci: bool | None = None
    has_tests: bool | None = None
    has_security_md: bool | None = None
    license_spdx: str | None = None
    is_archived: bool = False
    is_fork: bool = False
    errors: list[str] = field(default_factory=list)


def parse_github_url(url: str) -> tuple[str, str] | None:
    """Extract (owner, repo) from a GitHub URL. Returns None if not a GitHub URL."""
    m = _GITHUB_URL_RE.search(url)
    if not m:
        return None
    owner = m.group(1)
    repo = m.group(2).removesuffix(".git")
    return owner, repo


async def fetch_repo_health(
    owner: str,
    repo: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> GitHubRepoHealth:
    """Fetch health signals for a GitHub repository.

    Makes 3 concurrent API calls: repo metadata, releases, community profile.
    Degrades gracefully on rate limits (403) and errors.
    """
    owns_client = client is None
    if client is None:
        token = os.environ.get("GITHUB_TOKEN", "")
        headers: dict[str, str] = {"Accept": "application/vnd.github+json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        else:
            logger.warning("GITHUB_TOKEN not set — assessment will use unauthenticated rate limits")
        client = httpx.AsyncClient(
            base_url="https://api.github.com",
            headers=headers,
            timeout=15.0,
        )

    health = GitHubRepoHealth()

    try:
        results = await asyncio.gather(
            _fetch_repo_metadata(client, owner, repo, health),
            _fetch_releases(client, owner, repo, health),
            _fetch_community_profile(client, owner, repo, health),
            return_exceptions=True,
        )
        for r in results:
            if isinstance(r, Exception):
                health.errors.append(str(r))
    finally:
        if owns_client:
            await client.aclose()

    return health


async def _fetch_repo_metadata(
    client: httpx.AsyncClient,
    owner: str,
    repo: str,
    health: GitHubRepoHealth,
) -> None:
    """GET /repos/{owner}/{repo} — core metadata."""
    try:
        resp = await client.get(f"/repos/{owner}/{repo}")
    except httpx.HTTPError as e:
        health.errors.append(f"repo metadata: {e}")
        return

    if resp.status_code == 404:
        health.errors.append("Repository not found or is private")
        return
    if resp.status_code == 403:
        health.errors.append("GitHub rate limit exceeded for repo metadata")
        return
    if resp.status_code >= 400:
        health.errors.append(f"repo metadata: HTTP {resp.status_code}")
        return

    try:
        data = resp.json()
    except Exception:
        health.errors.append("repo metadata: malformed JSON")
        return

    # Last commit proxy
    pushed_at = data.get("pushed_at")
    if pushed_at:
        with contextlib.suppress(ValueError):
            health.last_commit_date = datetime.fromisoformat(pushed_at.replace("Z", "+00:00"))

    health.open_issue_count = data.get("open_issues_count")
    health.is_archived = bool(data.get("archived", False))
    health.is_fork = bool(data.get("fork", False))

    # License
    lic = data.get("license")
    if isinstance(lic, dict):
        health.license_spdx = lic.get("spdx_id")


async def _fetch_releases(
    client: httpx.AsyncClient,
    owner: str,
    repo: str,
    health: GitHubRepoHealth,
) -> None:
    """GET /repos/{owner}/{repo}/releases — release cadence."""
    try:
        resp = await client.get(
            f"/repos/{owner}/{repo}/releases",
            params={"per_page": 30},
        )
    except httpx.HTTPError as e:
        health.errors.append(f"releases: {e}")
        return

    if resp.status_code == 403:
        health.errors.append("GitHub rate limit exceeded for releases")
        return
    if resp.status_code >= 400:
        health.errors.append(f"releases: HTTP {resp.status_code}")
        return

    try:
        releases = resp.json()
    except Exception:
        health.errors.append("releases: malformed JSON")
        return

    if not isinstance(releases, list):
        health.errors.append("releases: unexpected response shape")
        return

    one_year_ago = datetime.now(UTC) - timedelta(days=365)
    recent_count = 0

    for rel in releases:
        created = rel.get("created_at", "")
        if created:
            try:
                dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                if dt >= one_year_ago:
                    recent_count += 1
            except ValueError:
                continue

    health.release_count_1y = recent_count
    health.has_recent_release = recent_count > 0


async def _fetch_community_profile(
    client: httpx.AsyncClient,
    owner: str,
    repo: str,
    health: GitHubRepoHealth,
) -> None:
    """GET /repos/{owner}/{repo}/community/profile — CI, tests, security signals."""
    try:
        resp = await client.get(f"/repos/{owner}/{repo}/community/profile")
    except httpx.HTTPError as e:
        health.errors.append(f"community profile: {e}")
        return

    if resp.status_code == 403:
        health.errors.append("GitHub rate limit exceeded for community profile")
        return
    if resp.status_code >= 400:
        # Fallback: try to check files directly
        await _fallback_file_checks(client, owner, repo, health)
        return

    try:
        data = resp.json()
    except Exception:
        health.errors.append("community profile: malformed JSON")
        return

    files = data.get("files", {})
    if not isinstance(files, dict):
        return

    # SECURITY.md
    health.has_security_md = files.get("security") is not None

    # Code of conduct (not directly useful, but we check contributing)
    # CI detection: not directly in community profile, use fallback
    await _check_ci(client, owner, repo, health)
    await _check_tests(client, owner, repo, health)


async def _fallback_file_checks(
    client: httpx.AsyncClient,
    owner: str,
    repo: str,
    health: GitHubRepoHealth,
) -> None:
    """Check for CI, tests, SECURITY.md via contents API when community profile unavailable."""
    await asyncio.gather(
        _check_ci(client, owner, repo, health),
        _check_tests(client, owner, repo, health),
        _check_security_md(client, owner, repo, health),
    )


async def _check_ci(
    client: httpx.AsyncClient, owner: str, repo: str, health: GitHubRepoHealth
) -> None:
    """Check for GitHub Actions workflows."""
    try:
        resp = await client.get(f"/repos/{owner}/{repo}/contents/.github/workflows")
        health.has_ci = resp.status_code == 200
    except httpx.HTTPError:
        pass  # leave as None


async def _check_tests(
    client: httpx.AsyncClient, owner: str, repo: str, health: GitHubRepoHealth
) -> None:
    """Check for test directories."""
    for test_dir in ("tests", "test", "spec", "__tests__"):
        try:
            resp = await client.get(f"/repos/{owner}/{repo}/contents/{test_dir}")
            if resp.status_code == 200:
                health.has_tests = True
                return
        except httpx.HTTPError:
            continue
    health.has_tests = False


async def _check_security_md(
    client: httpx.AsyncClient, owner: str, repo: str, health: GitHubRepoHealth
) -> None:
    """Check for SECURITY.md."""
    try:
        resp = await client.get(f"/repos/{owner}/{repo}/contents/SECURITY.md")
        health.has_security_md = resp.status_code == 200
    except httpx.HTTPError:
        pass
