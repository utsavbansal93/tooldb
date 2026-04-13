"""Tests for public-api-lists discovery source.

All tests mock httpx — no real API calls.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from tooldb.discovery.public_apis import ALL_JSON_URL, PublicApisSource

MOCK_ENTRIES = [
    {
        "name": "OpenWeatherMap",
        "url": "https://openweathermap.org/api",
        "description": "Access current weather data for any location",
        "auth": "apiKey",
        "https": True,
        "cors": "Yes",
        "category": "Weather",
    },
    {
        "name": "Cat Facts",
        "url": "https://catfact.ninja",
        "description": "Daily cat facts",
        "auth": "No",
        "https": True,
        "cors": "Yes",
        "category": "Animals",
    },
    {
        "name": "WeatherAPI",
        "url": "https://www.weatherapi.com",
        "description": "Weather API with realtime forecast",
        "auth": "apiKey",
        "https": True,
        "cors": "Yes",
        "category": "Weather",
    },
]


# ──────────────────── Happy path ────────────────────


class TestPublicApisHappyPath:
    @respx.mock
    @pytest.mark.asyncio
    async def test_parses_entries(self) -> None:
        respx.get(ALL_JSON_URL).mock(return_value=httpx.Response(200, json=MOCK_ENTRIES))

        source = PublicApisSource()
        results = await source.search("weather", limit=5)
        await source.close()

        assert len(results) == 2
        assert results[0]["name"] == "OpenWeatherMap"
        assert results[0]["type"] == "api"
        assert results[0]["cost_tier"] == "free"
        assert results[0]["auth_required"] is True

    @respx.mock
    @pytest.mark.asyncio
    async def test_token_matching(self) -> None:
        respx.get(ALL_JSON_URL).mock(return_value=httpx.Response(200, json=MOCK_ENTRIES))

        source = PublicApisSource()
        # "cat" should match Cat Facts
        results = await source.search("cat facts")
        await source.close()
        assert len(results) >= 1
        assert any(r["name"] == "Cat Facts" for r in results)

    @respx.mock
    @pytest.mark.asyncio
    async def test_no_auth_parsed(self) -> None:
        respx.get(ALL_JSON_URL).mock(return_value=httpx.Response(200, json=MOCK_ENTRIES))

        source = PublicApisSource()
        results = await source.search("cat")
        await source.close()
        cat_result = [r for r in results if r["name"] == "Cat Facts"]
        assert len(cat_result) == 1
        assert cat_result[0]["auth_required"] is False

    @respx.mock
    @pytest.mark.asyncio
    async def test_in_memory_cache(self) -> None:
        """Second search should NOT re-fetch the JSON."""
        route = respx.get(ALL_JSON_URL).mock(return_value=httpx.Response(200, json=MOCK_ENTRIES))

        source = PublicApisSource()
        await source.search("weather")
        await source.search("cat")
        await source.close()

        assert route.call_count == 1


# ──────────────────── Failure modes ────────────────────


class TestPublicApisFailures:
    @respx.mock
    @pytest.mark.asyncio
    async def test_malformed_entries_skipped(self) -> None:
        bad_entries = [
            {"name": "Good", "url": "https://good.com", "description": "ok", "category": "Test"},
            "not a dict",
            {"missing_required": True},
        ]
        respx.get(ALL_JSON_URL).mock(return_value=httpx.Response(200, json=bad_entries))

        source = PublicApisSource()
        results = await source.search("good")
        await source.close()
        # Should get the one good entry, skip the bad ones
        assert len(results) >= 1

    @respx.mock
    @pytest.mark.asyncio
    async def test_404_returns_empty(self) -> None:
        respx.get(ALL_JSON_URL).mock(return_value=httpx.Response(404))

        source = PublicApisSource()
        results = await source.search("anything")
        await source.close()
        assert results == []

    @respx.mock
    @pytest.mark.asyncio
    async def test_invalid_json_returns_empty(self) -> None:
        respx.get(ALL_JSON_URL).mock(
            return_value=httpx.Response(
                200, content=b"not json", headers={"content-type": "text/plain"}
            )
        )

        source = PublicApisSource()
        results = await source.search("anything")
        await source.close()
        assert results == []
