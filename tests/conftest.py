"""Shared test fixtures for ToolDB."""

from __future__ import annotations

import pytest

from tooldb.db.cache import ToolCache
from tooldb.models import Tool


@pytest.fixture
def cache() -> ToolCache:
    """In-memory ToolCache for testing."""
    return ToolCache(":memory:")


def make_tool(**overrides: object) -> Tool:
    """Create a Tool with sensible defaults. Override any field via kwargs."""
    defaults: dict[str, object] = {
        "name": "test-tool",
        "url": "https://github.com/example/test-tool",
        "type": "repo",
        "task_tags": ["testing"],
        "source": "manual",
    }
    defaults.update(overrides)
    return Tool(**defaults)  # type: ignore[arg-type]
