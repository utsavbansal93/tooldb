"""Data models for ToolDB."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

ToolType = Literal["repo", "api", "service", "cli"]
ToolSource = Literal["cache", "github", "public_apis", "web", "manual"]
ToolStatus = Literal["untried", "works", "degraded", "broken", "avoid"]
CostTier = Literal["free", "freemium", "paid", "unknown"]
CriteriaType = Literal["deterministic", "llm_judge", "eyeball"]
TargetType = Literal["tool", "recipe"]
LicenseRisk = Literal["low", "medium", "high", "unknown"]
AssessmentType = Literal["repo", "non_repo"]


@dataclass
class BenchmarkResult:
    """Result of a single benchmark run."""

    task_type: str
    score: float
    ran_at: datetime
    fixture_hash: str  # content hash, not path hash


@dataclass
class Tool:
    """A discovered or manually registered tool."""

    name: str
    url: str
    type: ToolType
    task_tags: list[str] = field(default_factory=list)
    license: str | None = None
    auth_required: bool = False
    cost_tier: CostTier = "unknown"
    dockerized: bool = False
    source: ToolSource = "manual"
    my_status: ToolStatus = "untried"
    my_notes: str | None = None
    benchmark_results: list[BenchmarkResult] = field(default_factory=list)
    last_used_at: datetime | None = None
    last_used_for: str | None = None
    last_failure_reason: str | None = None
    # invocation metadata
    install_cmd: str | None = None
    invocation_template: str | None = None
    rate_limit_per_hour: int | None = None
    rate_limit_per_sec: float | None = None
    auth_method: str | None = None
    auth_env_var: str | None = None
    wrapper_path: str | None = None
    last_invocation_at: datetime | None = None
    # operational metadata
    readme_extracted_at: datetime | None = None
    metadata_version: int = 0
    invocation_count: int = 0
    schema_version: int = 1
    # primary key + timestamps
    id: int | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class RecipeStep:
    """A single step in a recipe pipeline."""

    tool_id: int
    params: dict[str, object] = field(default_factory=dict)
    output_to_next_input: str | None = None


@dataclass
class Recipe:
    """A multi-step pipeline of tools."""

    name: str
    description: str
    steps: list[RecipeStep] = field(default_factory=list)
    step_count: int = 0
    my_status: ToolStatus = "untried"
    my_notes: str | None = None
    benchmark_results: list[BenchmarkResult] = field(default_factory=list)
    last_validated_at: datetime | None = None
    id: int | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class BenchmarkSpec:
    """Specification for how to benchmark a tool or recipe."""

    task_type: str
    target_type: TargetType
    target_id: int
    fixture_path: str
    criteria_type: CriteriaType
    criteria_spec: dict[str, object] = field(default_factory=dict)
    budget: dict[str, object] = field(default_factory=dict)
    id: int | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class CascadeResult:
    """Result of a cascade search."""

    tools: list[Tool] = field(default_factory=list)
    recipes: list[Recipe] = field(default_factory=list)
    layer_reached: int = 0
    per_candidate_scores: dict[int, float] = field(default_factory=dict)
    negative_cached: bool = False
    source_timings: dict[str, float] = field(default_factory=dict)
    production_assessments: dict[int, Any] = field(default_factory=dict)


@dataclass
class ProductionReadinessReport:
    """Assessment of a tool's production readiness based on public signals."""

    tool_id: int
    tool_name: str
    tool_url: str
    assessed_at: datetime
    assessment_type: AssessmentType
    # repo health (all None for non_repo)
    last_commit_date: datetime | None = None
    has_recent_release: bool | None = None
    release_count_1y: int | None = None
    open_issue_count: int | None = None
    avg_issue_age_days: float | None = None
    contributor_count_1y: int | None = None
    has_ci: bool | None = None
    has_tests: bool | None = None
    has_security_md: bool | None = None
    license_spdx: str | None = None
    license_risk: LicenseRisk = "unknown"
    cve_count: int = 0
    cve_details: list[dict[str, str]] = field(default_factory=list)
    overall_score: float = 0.0
    flags: list[str] = field(default_factory=list)
    raw_data: dict[str, Any] = field(default_factory=dict)


def task_signature(task: str) -> str:
    """Compute a stable hash for a task string. Case-insensitive (unicode-safe)."""
    normalized = task.strip().casefold()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def normalize_url(url: str) -> str:
    """Normalize a URL for deduplication.

    - Upgrades http to https
    - Strips trailing slash
    """
    url = url.strip()
    if url.startswith("http://"):
        url = "https://" + url[7:]
    return url.rstrip("/")


def tokenize_task(task: str) -> list[str]:
    """Split a task string into searchable tokens.

    Splits on whitespace and common separators (→, |, /, etc.),
    lowercases, removes very short tokens (len < 2) and stopwords.
    """

    _STOPWORDS = frozenset(
        {
            "a",
            "an",
            "the",
            "to",
            "for",
            "of",
            "in",
            "on",
            "at",
            "is",
            "it",
            "and",
            "or",
            "but",
            "not",
            "with",
            "from",
            "by",
            "as",
            "be",
            "do",
            "if",
            "so",
            "my",
            "me",
            "we",
            "us",
            "i",
            "that",
            "this",
            "can",
        }
    )
    # Split on whitespace + common separators
    words = re.split(r"[\s→|/\\,;:!?\-_&+=#@<>\"\'()\[\]{}]+", task.strip().lower())
    tokens = []
    for word in words:
        cleaned = word.strip(".")
        if len(cleaned) >= 2 and cleaned not in _STOPWORDS:
            tokens.append(cleaned)
    return tokens
