"""Integration test: full cascade with real APIs.

Requires GITHUB_TOKEN env var. Run with: pytest -m integration
"""

from __future__ import annotations

import os

import pytest

from tooldb.cascade import Cascade
from tooldb.db.cache import ToolCache

pytestmark = pytest.mark.integration


@pytest.mark.skipif(
    not os.environ.get("GITHUB_TOKEN"),
    reason="GITHUB_TOKEN not set",
)
class TestCascadeReal:
    @pytest.mark.asyncio
    async def test_empty_cache_discovers_tools(self) -> None:
        """With an empty cache, cascade should reach L3+ and return results."""
        cache = ToolCache(":memory:")
        cascade = Cascade(cache)
        result = await cascade.find("markdown to PDF converter", max_layer=3)

        assert result.layer_reached >= 3
        assert len(result.tools) > 0
        # Results should be persisted
        all_tools = cache.list_tools()
        assert len(all_tools) > 0

    @pytest.mark.asyncio
    async def test_second_search_hits_cache(self) -> None:
        """After first search, second should hit cache (L2)."""
        cache = ToolCache(":memory:")
        cascade = Cascade(cache)

        # First: discover
        r1 = await cascade.find("json parser library", max_layer=3)
        assert r1.layer_reached >= 3

        # Second: should hit L2 (untried cache)
        r2 = await cascade.find("json parser library", max_layer=2)
        assert r2.layer_reached <= 2
        assert len(r2.tools) > 0
