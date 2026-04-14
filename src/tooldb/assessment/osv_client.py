"""OSV.dev client for checking known CVEs.

OSV.dev is free and requires no authentication.
Queries by package name across common ecosystems as best-effort.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx

from tooldb.logging import logger

OSV_API_URL = "https://api.osv.dev/v1/query"

# Ecosystems to try when we only have a repo name (best-effort heuristic)
DEFAULT_ECOSYSTEMS = ["PyPI", "npm", "Go", "crates.io", "RubyGems", "NuGet", "Packagist"]


@dataclass
class CVEResult:
    """Result of a CVE check against OSV.dev."""

    cve_count: int = 0
    details: list[dict[str, str]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


async def check_cves(
    repo_name: str,
    *,
    ecosystems: list[str] | None = None,
    client: httpx.AsyncClient | None = None,
) -> CVEResult:
    """Check for known CVEs via OSV.dev.

    Tries the repo name as a package name across common ecosystems.
    This is a best-effort heuristic — not all repos map to packages.

    Args:
        repo_name: Repository name to use as package name guess.
        ecosystems: Ecosystems to query. Defaults to common ones.
        client: Optional httpx client for testing.

    Returns:
        CVEResult with count, details, and any errors.
    """
    result = CVEResult()
    ecosystems = ecosystems or DEFAULT_ECOSYSTEMS

    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=10.0)

    seen_vuln_ids: set[str] = set()

    try:
        for ecosystem in ecosystems:
            vulns = await _query_osv(client, repo_name, ecosystem, result)
            for vuln in vulns:
                vuln_id = vuln.get("id", "")
                if vuln_id and vuln_id not in seen_vuln_ids:
                    seen_vuln_ids.add(vuln_id)
                    result.details.append(_extract_vuln_detail(vuln))

            if result.details:
                # Found CVEs in at least one ecosystem, stop searching
                break
    finally:
        if owns_client:
            await client.aclose()

    result.cve_count = len(result.details)
    return result


async def _query_osv(
    client: httpx.AsyncClient,
    package_name: str,
    ecosystem: str,
    result: CVEResult,
) -> list[dict]:
    """Query OSV.dev for a single package+ecosystem pair."""
    payload = {
        "package": {
            "name": package_name,
            "ecosystem": ecosystem,
        }
    }

    try:
        resp = await client.post(OSV_API_URL, json=payload)
    except httpx.TimeoutException:
        result.errors.append(f"OSV.dev timeout for {ecosystem}/{package_name}")
        return []
    except httpx.HTTPError as e:
        result.errors.append(f"OSV.dev error for {ecosystem}/{package_name}: {e}")
        return []

    if resp.status_code >= 400:
        # Don't treat 400 as fatal — package may not exist in this ecosystem
        logger.debug("OSV.dev returned %d for %s/%s", resp.status_code, ecosystem, package_name)
        return []

    try:
        data = resp.json()
    except Exception:
        result.errors.append(f"OSV.dev malformed JSON for {ecosystem}/{package_name}")
        return []

    vulns = data.get("vulns", [])
    if not isinstance(vulns, list):
        return []

    return vulns


def _extract_vuln_detail(vuln: dict) -> dict[str, str]:
    """Extract a summary dict from an OSV vulnerability entry."""
    vuln_id = vuln.get("id", "unknown")
    summary = vuln.get("summary", vuln.get("details", "No description")[:200])

    severity = "unknown"
    severity_list = vuln.get("severity", [])
    if isinstance(severity_list, list):
        for s in severity_list:
            if isinstance(s, dict) and "score" in s:
                severity = str(s["score"])
                break

    published = vuln.get("published", "")

    return {
        "id": vuln_id,
        "summary": str(summary)[:200],
        "severity": severity,
        "published": str(published),
    }
