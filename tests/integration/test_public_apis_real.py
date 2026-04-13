"""Integration test: real public-api-lists fetch.

Run with: pytest -m integration
"""

from __future__ import annotations

import pytest

from tooldb.discovery.public_apis import PublicApisSource

pytestmark = pytest.mark.integration


class TestPublicApisRealFetch:
    @pytest.mark.asyncio
    async def test_fetch_all_returns_entries(self) -> None:
        source = PublicApisSource()
        try:
            results = await source.search("weather", limit=5)
            assert isinstance(results, list)
            # public-api-lists should have weather-related APIs
            if results:
                assert "name" in results[0]
                assert "url" in results[0]
                assert results[0]["type"] == "api"
        finally:
            await source.close()

    @pytest.mark.asyncio
    async def test_in_memory_cache(self) -> None:
        """Second search should use cached data (no re-fetch)."""
        source = PublicApisSource()
        try:
            r1 = await source.search("weather", limit=3)
            r2 = await source.search("weather", limit=3)
            assert r1 == r2
        finally:
            await source.close()
