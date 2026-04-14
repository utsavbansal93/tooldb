"""Production readiness assessment for tools.

Combines GitHub repo health signals, CVE checks, and license classification
into a structured report with transparent scoring and human-readable flags.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from tooldb.assessment.github_signals import GitHubRepoHealth, fetch_repo_health, parse_github_url
from tooldb.assessment.license_classifier import classify_license_risk
from tooldb.assessment.osv_client import check_cves
from tooldb.models import ProductionReadinessReport, Tool, tokenize_task

# Keywords that trigger automatic production readiness assessment
PRODUCTION_KEYWORDS = frozenset({
    "production",
    "enterprise",
    "regulated",
    "nbfc",
    "fintech",
    "healthcare",
    "hipaa",
    "sox",
    "pci",
    "gdpr",
    "compliance",
    "mission-critical",
    "high-availability",
    "sla",
})


def is_production_query(task: str) -> bool:
    """Check if a task query implies production/enterprise use."""
    tokens = set(tokenize_task(task))
    return bool(tokens & PRODUCTION_KEYWORDS)


async def assess(
    tool: Tool,
    *,
    skip_cve: bool = False,
) -> ProductionReadinessReport:
    """Assess a tool's production readiness using public signals.

    For GitHub repos: fetches repo health, checks CVEs, classifies license.
    For non-repo tools: returns a minimal report with an honest flag.

    Args:
        tool: The tool to assess.
        skip_cve: Skip CVE check for faster assessment.

    Returns:
        A ProductionReadinessReport with score, flags, and raw data.
    """
    now = datetime.now(UTC)

    # Check if it's a GitHub URL
    parsed = parse_github_url(tool.url)
    if parsed is None:
        return _non_repo_report(tool, now)

    owner, repo = parsed

    # Fetch GitHub signals
    health = await fetch_repo_health(owner, repo)

    # Check CVEs
    cve_result = None
    if not skip_cve:
        cve_result = await check_cves(repo)

    # Classify license
    license_risk = classify_license_risk(health.license_spdx)

    # Build report
    report = ProductionReadinessReport(
        tool_id=tool.id or 0,
        tool_name=tool.name,
        tool_url=tool.url,
        assessed_at=now,
        assessment_type="repo",
        last_commit_date=health.last_commit_date,
        has_recent_release=health.has_recent_release,
        release_count_1y=health.release_count_1y,
        open_issue_count=health.open_issue_count,
        avg_issue_age_days=health.avg_issue_age_days,
        contributor_count_1y=health.contributor_count,
        has_ci=health.has_ci,
        has_tests=health.has_tests,
        has_security_md=health.has_security_md,
        license_spdx=health.license_spdx,
        license_risk=license_risk,
        cve_count=cve_result.cve_count if cve_result else 0,
        cve_details=cve_result.details if cve_result else [],
        raw_data={
            "github_errors": health.errors,
            "cve_errors": cve_result.errors if cve_result else [],
        },
    )

    # Generate flags
    report.flags = _generate_flags(report, health)

    # Compute score
    report.overall_score = _compute_score(report)

    return report


def _non_repo_report(tool: Tool, now: datetime) -> ProductionReadinessReport:
    """Build a minimal report for non-GitHub-repo tools."""
    return ProductionReadinessReport(
        tool_id=tool.id or 0,
        tool_name=tool.name,
        tool_url=tool.url,
        assessed_at=now,
        assessment_type="non_repo",
        flags=[
            f"Assessment limited for non-repository tools (type={tool.type}). "
            "Manual review required."
        ],
        overall_score=0.0,
    )


def _generate_flags(
    report: ProductionReadinessReport,
    health: GitHubRepoHealth,
) -> list[str]:
    """Generate human-readable warning flags from assessment data."""
    flags: list[str] = []
    now = datetime.now(UTC)

    # Commit recency
    if report.last_commit_date:
        age = now - report.last_commit_date
        months = age.days // 30
        years = age.days // 365
        if years >= 2:
            flags.append(f"Project appears abandoned ({years} years since last commit)")
        elif months >= 6:
            flags.append(f"Last commit was {months} months ago")

    # Release cadence
    if report.has_recent_release is False:
        flags.append("No releases in the last 12 months")

    # Issue health
    if report.open_issue_count is not None and report.open_issue_count > 500:
        flags.append(f"High open issue count ({report.open_issue_count})")

    if report.avg_issue_age_days is not None and report.avg_issue_age_days > 180:
        flags.append(f"Average issue age is {int(report.avg_issue_age_days)} days")

    # Contributors
    if report.contributor_count_1y is not None:
        if report.contributor_count_1y <= 1:
            flags.append("Single contributor \u2014 bus-factor risk")
        elif report.contributor_count_1y <= 3:
            flags.append(f"Low contributor count ({report.contributor_count_1y})")

    # CI / Tests / Security
    if report.has_ci is False:
        flags.append("No CI detected")
    if report.has_tests is False:
        flags.append("No test directory detected")
    if report.has_security_md is False:
        flags.append("No SECURITY.md \u2014 no vulnerability reporting process")

    # License
    if report.license_risk == "high":
        flags.append(
            f"License ({report.license_spdx}) has restrictions that may conflict "
            "with enterprise use"
        )
    elif report.license_risk == "medium":
        flags.append(
            f"License ({report.license_spdx}) has copyleft terms \u2014 "
            "review before commercial use"
        )

    # CVEs
    if report.cve_count > 0:
        flags.append(f"{report.cve_count} known CVE(s) found \u2014 review before production use")

    # Archived / Fork
    if health.is_archived:
        flags.append("Repository is archived")
    if health.is_fork:
        flags.append("Repository is a fork \u2014 check upstream")

    # Errors
    if health.errors:
        flags.append(
            f"Some signals unavailable ({len(health.errors)} API errors)"
        )

    return flags


# Scoring weights — transparent and simple
_WEIGHTS: list[tuple[str, float]] = [
    ("recent_commit", 0.20),
    ("has_releases", 0.10),
    ("issue_health", 0.10),
    ("contributors", 0.15),
    ("ci", 0.10),
    ("tests", 0.10),
    ("security_md", 0.05),
    ("license", 0.10),
    ("no_cves", 0.10),
]


def _compute_score(report: ProductionReadinessReport) -> float:
    """Compute weighted production readiness score (0.0 to 1.0).

    Unknown/null signals are excluded from both numerator and denominator,
    so the score reflects only what we actually know.
    """
    now = datetime.now(UTC)
    signals: dict[str, float | None] = {}

    # Recent commit
    if report.last_commit_date:
        age_days = (now - report.last_commit_date).days
        if age_days < 180:
            signals["recent_commit"] = 1.0
        elif age_days < 365:
            signals["recent_commit"] = 0.5
        else:
            signals["recent_commit"] = 0.0
    else:
        signals["recent_commit"] = None

    # Releases
    if report.has_recent_release is not None:
        signals["has_releases"] = 1.0 if report.has_recent_release else 0.0
    else:
        signals["has_releases"] = None

    # Issue health
    if report.open_issue_count is not None:
        if report.open_issue_count < 100:
            signals["issue_health"] = 1.0
        elif report.open_issue_count < 500:
            signals["issue_health"] = 0.5
        else:
            signals["issue_health"] = 0.0
    else:
        signals["issue_health"] = None

    # Contributors
    if report.contributor_count_1y is not None:
        if report.contributor_count_1y > 5:
            signals["contributors"] = 1.0
        elif report.contributor_count_1y >= 2:
            signals["contributors"] = 0.5
        else:
            signals["contributors"] = 0.0
    else:
        signals["contributors"] = None

    # CI
    if report.has_ci is not None:
        signals["ci"] = 1.0 if report.has_ci else 0.0
    else:
        signals["ci"] = None

    # Tests
    if report.has_tests is not None:
        signals["tests"] = 1.0 if report.has_tests else 0.0
    else:
        signals["tests"] = None

    # SECURITY.md
    if report.has_security_md is not None:
        signals["security_md"] = 1.0 if report.has_security_md else 0.0
    else:
        signals["security_md"] = None

    # License
    if report.license_risk != "unknown":
        signals["license"] = {"low": 1.0, "medium": 0.5, "high": 0.0}[report.license_risk]
    else:
        signals["license"] = None

    # CVEs
    if report.cve_count == 0:
        signals["no_cves"] = 1.0
    elif report.cve_count <= 2:
        signals["no_cves"] = 0.5
    else:
        signals["no_cves"] = 0.0

    # Weighted average of available signals
    total_weight = 0.0
    weighted_sum = 0.0

    for name, weight in _WEIGHTS:
        val = signals.get(name)
        if val is not None:
            total_weight += weight
            weighted_sum += weight * val

    if total_weight == 0.0:
        return 0.0

    return round(weighted_sum / total_weight, 2)


def report_to_dict(report: ProductionReadinessReport) -> dict[str, Any]:
    """Convert a report to a JSON-serializable dict."""
    return {
        "tool_id": report.tool_id,
        "tool_name": report.tool_name,
        "tool_url": report.tool_url,
        "assessed_at": report.assessed_at.isoformat(),
        "assessment_type": report.assessment_type,
        "last_commit_date": (
            report.last_commit_date.isoformat() if report.last_commit_date else None
        ),
        "has_recent_release": report.has_recent_release,
        "release_count_1y": report.release_count_1y,
        "open_issue_count": report.open_issue_count,
        "avg_issue_age_days": report.avg_issue_age_days,
        "contributor_count_1y": report.contributor_count_1y,
        "has_ci": report.has_ci,
        "has_tests": report.has_tests,
        "has_security_md": report.has_security_md,
        "license_spdx": report.license_spdx,
        "license_risk": report.license_risk,
        "cve_count": report.cve_count,
        "cve_details": report.cve_details,
        "overall_score": report.overall_score,
        "flags": report.flags,
    }
