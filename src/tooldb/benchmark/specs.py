"""Benchmark specification parsing and validation."""

from __future__ import annotations

import hashlib
from pathlib import Path

from tooldb.models import BenchmarkSpec


def validate_spec(spec: BenchmarkSpec) -> list[str]:
    """Validate a benchmark spec. Returns list of error messages (empty = valid)."""
    errors: list[str] = []

    if not spec.task_type:
        errors.append("task_type is required")

    if spec.target_type not in ("tool", "recipe"):
        errors.append(f"target_type must be 'tool' or 'recipe', got {spec.target_type!r}")

    if spec.criteria_type not in ("deterministic", "llm_judge", "eyeball"):
        errors.append(
            f"criteria_type must be deterministic|llm_judge|eyeball, "
            f"got {spec.criteria_type!r}"
        )

    if (
        spec.criteria_type == "deterministic"
        and "command" not in spec.criteria_spec
        and "predicate" not in spec.criteria_spec
    ):
        errors.append(
            "deterministic criteria_spec must have 'command' or 'predicate' key"
        )

    if spec.criteria_type == "llm_judge" and "rubric" not in spec.criteria_spec:
            errors.append("llm_judge criteria_spec must have 'rubric' key")

    return errors


def fixture_content_hash(fixture_path: str) -> str:
    """Compute content hash of a fixture file.

    Returns SHA-256 hex digest of the file contents.
    Same content always produces same hash.
    """
    path = Path(fixture_path)
    if not path.exists():
        return hashlib.sha256(fixture_path.encode()).hexdigest()
    content = path.read_bytes()
    return hashlib.sha256(content).hexdigest()
