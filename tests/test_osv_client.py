"""Tests for the OSV.dev CVE client (mocked HTTP)."""

from __future__ import annotations

import httpx
import pytest
import respx

from tooldb.assessment.osv_client import check_cves


@pytest.fixture
def osv_api() -> respx.MockRouter:
    """Create a respx mock router for OSV.dev API."""
    with respx.mock(base_url="https://api.osv.dev") as router:
        yield router


class TestCheckCves:
    @pytest.mark.asyncio
    async def test_found_cves(self, osv_api: respx.MockRouter) -> None:
        osv_api.post("/v1/query").respond(
            json={
                "vulns": [
                    {
                        "id": "CVE-2024-1234",
                        "summary": "Buffer overflow in parser",
                        "severity": [{"score": "7.5"}],
                        "published": "2024-06-01T00:00:00Z",
                    },
                    {
                        "id": "CVE-2024-5678",
                        "summary": "SQL injection in query handler",
                        "severity": [],
                        "published": "2024-07-01T00:00:00Z",
                    },
                ]
            }
        )

        client = httpx.AsyncClient(base_url="https://api.osv.dev", timeout=5.0)
        async with client:
            result = await check_cves("test-package", ecosystems=["PyPI"], client=client)

        assert result.cve_count == 2
        assert len(result.details) == 2
        assert result.details[0]["id"] == "CVE-2024-1234"
        assert result.details[0]["severity"] == "7.5"
        assert not result.errors

    @pytest.mark.asyncio
    async def test_no_cves(self, osv_api: respx.MockRouter) -> None:
        osv_api.post("/v1/query").respond(json={})

        client = httpx.AsyncClient(base_url="https://api.osv.dev", timeout=5.0)
        async with client:
            result = await check_cves("safe-package", ecosystems=["PyPI"], client=client)

        assert result.cve_count == 0
        assert result.details == []
        assert not result.errors

    @pytest.mark.asyncio
    async def test_osv_timeout(self, osv_api: respx.MockRouter) -> None:
        osv_api.post("/v1/query").side_effect = httpx.ReadTimeout("timeout")

        client = httpx.AsyncClient(base_url="https://api.osv.dev", timeout=5.0)
        async with client:
            result = await check_cves("slow-package", ecosystems=["PyPI"], client=client)

        assert result.cve_count == 0
        assert len(result.errors) > 0
        assert any("timeout" in e.lower() for e in result.errors)

    @pytest.mark.asyncio
    async def test_osv_server_error(self, osv_api: respx.MockRouter) -> None:
        osv_api.post("/v1/query").respond(status_code=500)

        client = httpx.AsyncClient(base_url="https://api.osv.dev", timeout=5.0)
        async with client:
            result = await check_cves("error-package", ecosystems=["PyPI"], client=client)

        assert result.cve_count == 0

    @pytest.mark.asyncio
    async def test_deduplicates_across_ecosystems(self, osv_api: respx.MockRouter) -> None:
        """Same CVE found in two ecosystems should only appear once."""
        vuln = {
            "id": "CVE-2024-9999",
            "summary": "Shared vuln",
            "severity": [],
            "published": "2024-01-01",
        }
        # First ecosystem finds CVE, so we stop (break on first hit)
        osv_api.post("/v1/query").respond(json={"vulns": [vuln]})

        client = httpx.AsyncClient(base_url="https://api.osv.dev", timeout=5.0)
        async with client:
            result = await check_cves(
                "dual-package",
                ecosystems=["PyPI", "npm"],
                client=client,
            )

        assert result.cve_count == 1

    @pytest.mark.asyncio
    async def test_malformed_response(self, osv_api: respx.MockRouter) -> None:
        osv_api.post("/v1/query").respond(
            content=b"not json",
            headers={"content-type": "application/json"},
        )

        client = httpx.AsyncClient(base_url="https://api.osv.dev", timeout=5.0)
        async with client:
            result = await check_cves("bad-package", ecosystems=["PyPI"], client=client)

        assert result.cve_count == 0
        assert any("malformed" in e.lower() for e in result.errors)
