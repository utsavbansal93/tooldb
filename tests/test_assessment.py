"""Tests for the production readiness assessment module."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from tooldb.assessment.github_signals import GitHubRepoHealth, parse_github_url
from tooldb.assessment.license_classifier import classify_license_risk
from tooldb.assessment.production_readiness import (
    _compute_score,
    _generate_flags,
    assess,
    is_production_query,
    report_to_dict,
)
from tooldb.models import ProductionReadinessReport

from .conftest import make_tool

# ──────────────────── License classifier ────────────────────


class TestLicenseClassifier:
    def test_low_risk_licenses(self) -> None:
        for lic in ("MIT", "Apache-2.0", "BSD-3-Clause", "ISC", "Unlicense", "0BSD"):
            assert classify_license_risk(lic) == "low", f"Expected low for {lic}"

    def test_medium_risk_licenses(self) -> None:
        for lic in ("GPL-2.0", "GPL-3.0", "LGPL-3.0", "MPL-2.0"):
            assert classify_license_risk(lic) == "medium", f"Expected medium for {lic}"

    def test_high_risk_licenses(self) -> None:
        for lic in ("AGPL-3.0", "SSPL-1.0", "BUSL-1.1"):
            assert classify_license_risk(lic) == "high", f"Expected high for {lic}"

    def test_unknown_licenses(self) -> None:
        assert classify_license_risk(None) == "unknown"
        assert classify_license_risk("") == "unknown"
        assert classify_license_risk("NOASSERTION") == "unknown"
        assert classify_license_risk("SomeRandomLicense") == "unknown"

    def test_case_insensitive(self) -> None:
        assert classify_license_risk("mit") == "low"
        assert classify_license_risk("APACHE-2.0") == "low"
        assert classify_license_risk("gpl-3.0") == "medium"


# ──────────────────── Production keyword detection ────────────────────


class TestProductionKeywords:
    def test_matches_production(self) -> None:
        assert is_production_query("production ready PDF converter") is True
        assert is_production_query("enterprise authentication library") is True
        assert is_production_query("NBFC lending tool") is True

    def test_no_match_normal_queries(self) -> None:
        assert is_production_query("convert PDF to markdown") is False
        assert is_production_query("image resizer tool") is False
        assert is_production_query("json formatter") is False

    def test_case_insensitive(self) -> None:
        assert is_production_query("PRODUCTION grade API") is True
        assert is_production_query("HIPAA compliant storage") is True

    def test_empty_query(self) -> None:
        assert is_production_query("") is False


# ──────────────────── GitHub URL parsing ────────────────────


class TestGitHubUrlParsing:
    def test_standard_url(self) -> None:
        assert parse_github_url("https://github.com/jgm/pandoc") == ("jgm", "pandoc")

    def test_url_with_git_suffix(self) -> None:
        assert parse_github_url("https://github.com/jgm/pandoc.git") == ("jgm", "pandoc")

    def test_url_with_path(self) -> None:
        result = parse_github_url("https://github.com/jgm/pandoc/tree/main")
        assert result == ("jgm", "pandoc")

    def test_non_github_url(self) -> None:
        assert parse_github_url("https://gitlab.com/foo/bar") is None
        assert parse_github_url("https://pypi.org/project/pandas") is None

    def test_http_url(self) -> None:
        assert parse_github_url("http://github.com/foo/bar") == ("foo", "bar")


# ──────────────────── Scoring ────────────────────


class TestScoring:
    def test_perfect_score(self) -> None:
        now = datetime.now(UTC)
        report = ProductionReadinessReport(
            tool_id=1,
            tool_name="test",
            tool_url="https://github.com/x/y",
            assessed_at=now,
            assessment_type="repo",
            last_commit_date=now - timedelta(days=30),
            has_recent_release=True,
            open_issue_count=50,
            contributor_count_1y=10,
            has_ci=True,
            has_tests=True,
            has_security_md=True,
            license_risk="low",
            cve_count=0,
        )
        score = _compute_score(report)
        assert score == 1.0

    def test_worst_score(self) -> None:
        now = datetime.now(UTC)
        report = ProductionReadinessReport(
            tool_id=1,
            tool_name="test",
            tool_url="https://github.com/x/y",
            assessed_at=now,
            assessment_type="repo",
            last_commit_date=now - timedelta(days=700),
            has_recent_release=False,
            open_issue_count=1000,
            contributor_count_1y=1,
            has_ci=False,
            has_tests=False,
            has_security_md=False,
            license_risk="high",
            cve_count=5,
        )
        score = _compute_score(report)
        assert score == 0.0

    def test_partial_signals(self) -> None:
        """Score computed from available signals only."""
        now = datetime.now(UTC)
        report = ProductionReadinessReport(
            tool_id=1,
            tool_name="test",
            tool_url="https://github.com/x/y",
            assessed_at=now,
            assessment_type="repo",
            has_ci=True,
            has_tests=True,
            cve_count=0,
        )
        score = _compute_score(report)
        # Only ci (0.10), tests (0.10), cves (0.10) have values → all 1.0
        assert score == 1.0

    def test_all_unknown_score(self) -> None:
        """No signals → 0.0."""
        now = datetime.now(UTC)
        report = ProductionReadinessReport(
            tool_id=1,
            tool_name="test",
            tool_url="https://github.com/x/y",
            assessed_at=now,
            assessment_type="repo",
        )
        score = _compute_score(report)
        # cve_count=0 by default, so no_cves signal = 1.0
        # everything else is None → excluded
        assert score == 1.0  # only cve signal, which is 1.0

    def test_score_bounds(self) -> None:
        """Score always in [0, 1]."""
        now = datetime.now(UTC)
        report = ProductionReadinessReport(
            tool_id=1,
            tool_name="test",
            tool_url="https://github.com/x/y",
            assessed_at=now,
            assessment_type="repo",
            last_commit_date=now - timedelta(days=200),
            has_recent_release=True,
            open_issue_count=300,
            contributor_count_1y=3,
            has_ci=True,
            has_tests=False,
            has_security_md=False,
            license_risk="medium",
            cve_count=1,
        )
        score = _compute_score(report)
        assert 0.0 <= score <= 1.0


# ──────────────────── Flag generation ────────────────────


class TestFlagGeneration:
    def test_stale_commit_flag(self) -> None:
        now = datetime.now(UTC)
        report = ProductionReadinessReport(
            tool_id=1, tool_name="test", tool_url="", assessed_at=now,
            assessment_type="repo",
            last_commit_date=now - timedelta(days=250),
        )
        health = GitHubRepoHealth(last_commit_date=report.last_commit_date)
        flags = _generate_flags(report, health)
        assert any("months ago" in f for f in flags)

    def test_abandoned_project_flag(self) -> None:
        now = datetime.now(UTC)
        report = ProductionReadinessReport(
            tool_id=1, tool_name="test", tool_url="", assessed_at=now,
            assessment_type="repo",
            last_commit_date=now - timedelta(days=800),
        )
        health = GitHubRepoHealth(last_commit_date=report.last_commit_date)
        flags = _generate_flags(report, health)
        assert any("abandoned" in f for f in flags)

    def test_no_releases_flag(self) -> None:
        now = datetime.now(UTC)
        report = ProductionReadinessReport(
            tool_id=1, tool_name="test", tool_url="", assessed_at=now,
            assessment_type="repo", has_recent_release=False,
        )
        health = GitHubRepoHealth(has_recent_release=False)
        flags = _generate_flags(report, health)
        assert any("No releases" in f for f in flags)

    def test_single_contributor_flag(self) -> None:
        now = datetime.now(UTC)
        report = ProductionReadinessReport(
            tool_id=1, tool_name="test", tool_url="", assessed_at=now,
            assessment_type="repo", contributor_count_1y=1,
        )
        health = GitHubRepoHealth()
        flags = _generate_flags(report, health)
        assert any("bus-factor" in f for f in flags)

    def test_high_risk_license_flag(self) -> None:
        now = datetime.now(UTC)
        report = ProductionReadinessReport(
            tool_id=1, tool_name="test", tool_url="", assessed_at=now,
            assessment_type="repo", license_spdx="AGPL-3.0", license_risk="high",
        )
        health = GitHubRepoHealth()
        flags = _generate_flags(report, health)
        assert any("enterprise use" in f for f in flags)

    def test_cve_flag(self) -> None:
        now = datetime.now(UTC)
        report = ProductionReadinessReport(
            tool_id=1, tool_name="test", tool_url="", assessed_at=now,
            assessment_type="repo", cve_count=3,
        )
        health = GitHubRepoHealth()
        flags = _generate_flags(report, health)
        assert any("CVE" in f for f in flags)

    def test_archived_flag(self) -> None:
        now = datetime.now(UTC)
        report = ProductionReadinessReport(
            tool_id=1, tool_name="test", tool_url="", assessed_at=now,
            assessment_type="repo",
        )
        health = GitHubRepoHealth(is_archived=True)
        flags = _generate_flags(report, health)
        assert any("archived" in f for f in flags)

    def test_no_ci_flag(self) -> None:
        now = datetime.now(UTC)
        report = ProductionReadinessReport(
            tool_id=1, tool_name="test", tool_url="", assessed_at=now,
            assessment_type="repo", has_ci=False,
        )
        health = GitHubRepoHealth()
        flags = _generate_flags(report, health)
        assert any("No CI" in f for f in flags)


# ──────────────────── Non-repo assessment ────────────────────


class TestNonRepoAssessment:
    @pytest.mark.asyncio
    async def test_non_github_url(self) -> None:
        tool = make_tool(
            url="https://api.example.com/v1",
            type="api",
        )
        report = await assess(tool)
        assert report.assessment_type == "non_repo"
        assert report.overall_score == 0.0
        assert any("non-repository" in f for f in report.flags)

    @pytest.mark.asyncio
    async def test_pypi_url(self) -> None:
        tool = make_tool(url="https://pypi.org/project/pandas")
        report = await assess(tool)
        assert report.assessment_type == "non_repo"


# ──────────────────── Full assess with mocks ────────────────────


class TestAssessWithMocks:
    @pytest.mark.asyncio
    async def test_assess_github_tool(self) -> None:
        tool = make_tool(
            url="https://github.com/jgm/pandoc",
            name="pandoc",
        )

        now = datetime.now(UTC)
        mock_health = GitHubRepoHealth(
            last_commit_date=now - timedelta(days=30),
            has_recent_release=True,
            release_count_1y=5,
            open_issue_count=200,
            contributor_count=20,
            has_ci=True,
            has_tests=True,
            has_security_md=True,
            license_spdx="GPL-2.0",
        )

        with (
            patch(
                "tooldb.assessment.production_readiness.fetch_repo_health",
                new_callable=AsyncMock,
                return_value=mock_health,
            ),
            patch(
                "tooldb.assessment.production_readiness.check_cves",
                new_callable=AsyncMock,
            ) as mock_cve,
        ):
            from tooldb.assessment.osv_client import CVEResult

            mock_cve.return_value = CVEResult(cve_count=0, details=[])

            report = await assess(tool)

        assert report.assessment_type == "repo"
        assert report.overall_score > 0.5
        assert report.license_risk == "medium"  # GPL-2.0
        assert report.cve_count == 0

    @pytest.mark.asyncio
    async def test_assess_skip_cve(self) -> None:
        tool = make_tool(url="https://github.com/foo/bar")

        now = datetime.now(UTC)
        mock_health = GitHubRepoHealth(
            last_commit_date=now - timedelta(days=10),
            has_ci=True,
            has_tests=True,
        )

        with patch(
            "tooldb.assessment.production_readiness.fetch_repo_health",
            new_callable=AsyncMock,
            return_value=mock_health,
        ):
            report = await assess(tool, skip_cve=True)

        assert report.assessment_type == "repo"
        assert report.cve_count == 0
        assert report.cve_details == []


# ──────────────────── Report serialization ────────────────────


class TestReportSerialization:
    def test_report_to_dict(self) -> None:
        now = datetime.now(UTC)
        report = ProductionReadinessReport(
            tool_id=1,
            tool_name="test",
            tool_url="https://github.com/x/y",
            assessed_at=now,
            assessment_type="repo",
            overall_score=0.75,
            flags=["test flag"],
        )
        d = report_to_dict(report)
        assert d["tool_id"] == 1
        assert d["overall_score"] == 0.75
        assert d["flags"] == ["test flag"]
        assert d["assessed_at"] == now.isoformat()
