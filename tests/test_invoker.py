"""Tests for the tool invocation engine."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from tooldb.db.cache import ToolCache
from tooldb.invoker import InvocationError, RateLimitExceeded, ToolInvoker, _sanitize_input

from .conftest import make_tool


@pytest.fixture
def invoker_cache() -> ToolCache:
    return ToolCache(":memory:")


@pytest.fixture
def invoker(invoker_cache: ToolCache) -> ToolInvoker:
    return ToolInvoker(invoker_cache)


# ──────────────────── Happy path ────────────────────


class TestInvokerHappyPath:
    @pytest.mark.asyncio
    async def test_invoke_via_template(self, invoker_cache: ToolCache) -> None:
        tool = invoker_cache.upsert(
            make_tool(
                name="echo-tool",
                invocation_template="echo {query}",
                url="https://github.com/ex/echo",
            )
        )
        inv = ToolInvoker(invoker_cache)
        result = await inv.invoke(tool.id, {"query": "hello"})  # type: ignore[arg-type]
        assert result["status"] == "ok"
        assert "hello" in result["output"]

    @pytest.mark.asyncio
    async def test_invoke_via_wrapper(self, invoker_cache: ToolCache) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(
                'def invoke(inputs):\n    return {"output": f"got {inputs}", "status": "ok"}\n'
            )
            f.flush()

            tool = invoker_cache.upsert(
                make_tool(
                    name="wrapper-tool",
                    url="https://github.com/ex/wrapper",
                    wrapper_path=f.name,
                )
            )
            inv = ToolInvoker(invoker_cache)
            result = await inv.invoke(tool.id, {"x": "1"})  # type: ignore[arg-type]
            assert result["status"] == "ok"

    @pytest.mark.asyncio
    async def test_dry_run_shows_command(self, invoker_cache: ToolCache) -> None:
        tool = invoker_cache.upsert(
            make_tool(
                name="dry-tool",
                invocation_template="curl {url}",
                url="https://github.com/ex/dry",
            )
        )
        inv = ToolInvoker(invoker_cache)
        result = await inv.invoke(tool.id, {"url": "https://example.com"}, dry_run=True)  # type: ignore[arg-type]
        assert result["status"] == "dry_run"
        assert "curl" in result["output"]

    @pytest.mark.asyncio
    async def test_updates_invocation_count(self, invoker_cache: ToolCache) -> None:
        tool = invoker_cache.upsert(
            make_tool(
                name="count-tool",
                invocation_template="echo ok",
                url="https://github.com/ex/count",
            )
        )
        inv = ToolInvoker(invoker_cache)
        await inv.invoke(tool.id, {})  # type: ignore[arg-type]
        updated = invoker_cache.get(tool.id)  # type: ignore[arg-type]
        assert updated is not None
        assert updated.invocation_count == 1


# ──────────────────── Edge cases ────────────────────


class TestInvokerEdgeCases:
    @pytest.mark.asyncio
    async def test_tool_not_found(self, invoker: ToolInvoker) -> None:
        with pytest.raises(InvocationError, match="not found"):
            await invoker.invoke(99999, {})

    @pytest.mark.asyncio
    async def test_broken_tool_refused(self, invoker_cache: ToolCache) -> None:
        tool = invoker_cache.upsert(
            make_tool(name="broken", url="https://github.com/ex/broken", my_status="broken")
        )
        inv = ToolInvoker(invoker_cache)
        with pytest.raises(InvocationError, match="broken"):
            await inv.invoke(tool.id, {})  # type: ignore[arg-type]

    @pytest.mark.asyncio
    async def test_avoid_tool_refused(self, invoker_cache: ToolCache) -> None:
        tool = invoker_cache.upsert(
            make_tool(name="avoided", url="https://github.com/ex/avoided", my_status="avoid")
        )
        inv = ToolInvoker(invoker_cache)
        with pytest.raises(InvocationError, match="avoid"):
            await inv.invoke(tool.id, {})  # type: ignore[arg-type]

    @pytest.mark.asyncio
    async def test_missing_required_input(self, invoker_cache: ToolCache) -> None:
        tool = invoker_cache.upsert(
            make_tool(
                name="need-input",
                invocation_template="echo {query} {format}",
                url="https://github.com/ex/need",
            )
        )
        inv = ToolInvoker(invoker_cache)
        with pytest.raises(InvocationError, match="Missing required inputs"):
            await inv.invoke(tool.id, {"query": "hello"})  # type: ignore[arg-type]

    @pytest.mark.asyncio
    async def test_wrapper_path_missing_falls_back(self, invoker_cache: ToolCache) -> None:
        # Create tool without wrapper, then set wrapper_path directly in DB
        # to simulate the file going missing after initial setup
        tool = invoker_cache.upsert(
            make_tool(
                name="fallback",
                url="https://github.com/ex/fallback",
                invocation_template="echo fallback",
                my_status="degraded",
            )
        )
        # Simulate wrapper_path being set but file deleted
        invoker_cache.conn.execute(
            "UPDATE tools SET wrapper_path = ? WHERE id = ?",
            ("/nonexistent/wrapper.py", tool.id),
        )
        invoker_cache.conn.commit()

        inv = ToolInvoker(invoker_cache)
        result = await inv.invoke(tool.id, {})  # type: ignore[arg-type]
        assert result["status"] == "ok"
        assert "fallback" in result["output"]


# ──────────────────── Input sanitization ────────────────────


class TestInputSanitization:
    def test_shell_metacharacters_escaped(self) -> None:
        dangerous = "; rm -rf /"
        safe = _sanitize_input(dangerous)
        assert ";" not in safe or "\\;" in safe

    def test_pipe_escaped(self) -> None:
        assert "|" not in _sanitize_input("hello|world").replace("\\|", "")

    def test_backtick_escaped(self) -> None:
        assert "`" not in _sanitize_input("`whoami`").replace("\\`", "")


# ──────────────────── Rate limiting ────────────────────


class TestRateLimiting:
    @pytest.mark.asyncio
    async def test_rate_limit_exceeded(self, invoker_cache: ToolCache) -> None:
        tool = invoker_cache.upsert(
            make_tool(
                name="limited",
                url="https://github.com/ex/limited",
                invocation_template="echo ok",
                rate_limit_per_hour=2,
            )
        )
        inv = ToolInvoker(invoker_cache)

        # Use a temp rate state file
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "rate_limits.json"
            with patch("tooldb.invoker.RATE_LIMIT_STATE_FILE", state_file), patch(
                "tooldb.invoker.RATE_LIMIT_STATE_DIR", Path(tmpdir)
            ):
                await inv.invoke(tool.id, {})  # type: ignore[arg-type]
                await inv.invoke(tool.id, {})  # type: ignore[arg-type]

                with pytest.raises(RateLimitExceeded):
                    await inv.invoke(tool.id, {})  # type: ignore[arg-type]
