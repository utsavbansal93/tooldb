"""Rate-limited tool invocation engine.

Loads tool from cache, validates inputs, enforces rate limits,
and dispatches to wrapper or invocation_template.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import re
import shlex
import time
from pathlib import Path
from typing import Any

from tooldb.assessment.safety import check_invocation_safety
from tooldb.db.cache import ToolCache
from tooldb.logging import log_invocation, logger
from tooldb.models import Tool

RATE_LIMIT_STATE_DIR = Path.home() / ".tooldb"
RATE_LIMIT_STATE_FILE = RATE_LIMIT_STATE_DIR / "rate_limits.json"


class InvocationError(Exception):
    """Raised when a tool invocation fails."""


class RateLimitExceeded(InvocationError):
    """Raised when rate limits are exceeded."""


class ToolInvoker:
    """Rate-limited tool invocation engine."""

    def __init__(self, cache: ToolCache) -> None:
        self._cache = cache

    async def invoke(
        self,
        tool_id: int,
        inputs: dict[str, Any],
        *,
        dry_run: bool = False,
        timeout_s: float = 60.0,
    ) -> dict[str, Any]:
        """Invoke a tool with given inputs.

        1. Load tool, check status != broken/avoid
        2. Validate inputs against invocation_template (required keys)
        3. Sanitize inputs (escape shell metacharacters)
        4. If wrapper_path exists and file exists → import and call
        5. Elif wrapper_path set but missing → fallback to template, warn
        6. Else → generic invocation via invocation_template
        7. Enforce rate limits
        8. Update last_invocation_at, increment invocation_count

        Args:
            tool_id: ID of tool to invoke.
            inputs: Dict of input parameters.
            dry_run: Show constructed command without executing.
            timeout_s: Maximum execution time.

        Returns:
            {"output": ..., "status": "ok"|"error", "duration_ms": ...}
        """
        tool = self._cache.get(tool_id)
        if tool is None:
            raise InvocationError(f"Tool {tool_id} not found")

        if tool.my_status == "broken":
            raise InvocationError(
                f"Tool {tool.name} has status 'broken' — refusing to invoke"
            )

        # Safety check (covers status=avoid + template injection + wrapper integrity)
        verdict = check_invocation_safety(tool)
        if not verdict.safe:
            raise InvocationError(verdict.blocked_reason or "Safety check failed")
        for warning in verdict.warnings:
            logger.warning("Safety warning for tool %s: %s", tool.name, warning)

        # Validate required inputs
        required_keys = self._extract_template_keys(tool)
        missing = required_keys - set(inputs.keys())
        if missing:
            raise InvocationError(
                f"Missing required inputs: {', '.join(sorted(missing))}"
            )

        # Log extra keys
        extra = set(inputs.keys()) - required_keys
        if extra:
            logger.debug("Extra input keys ignored: %s", extra)

        # Sanitize inputs
        safe_inputs = {k: _sanitize_input(v) for k, v in inputs.items()}

        # Rate limit check
        self._enforce_rate_limit(tool)

        if dry_run:
            cmd = self._build_command(tool, safe_inputs)
            log_invocation("dry_run", tool_id=tool_id, command=cmd)
            return {"output": cmd, "status": "dry_run", "duration_ms": 0}

        start = time.monotonic()
        try:
            output = await asyncio.wait_for(
                self._execute(tool, safe_inputs, timeout_s=timeout_s),
                timeout=timeout_s,
            )
            duration_ms = (time.monotonic() - start) * 1000

            # Record success
            self._cache.record_use(tool_id, json.dumps(inputs)[:200])
            self._update_invocation_count(tool_id)

            log_invocation("success", tool_id=tool_id, duration_ms=round(duration_ms, 2))
            return {
                "output": output,
                "status": "ok",
                "duration_ms": round(duration_ms, 2),
            }
        except TimeoutError:
            duration_ms = (time.monotonic() - start) * 1000
            self._cache.record_failure(tool_id, f"Timed out after {timeout_s}s")
            log_invocation("timeout", tool_id=tool_id, timeout_s=timeout_s)
            return {
                "output": None,
                "status": "error",
                "duration_ms": round(duration_ms, 2),
            }
        except Exception as e:
            duration_ms = (time.monotonic() - start) * 1000
            log_invocation("error", tool_id=tool_id, error=str(e))
            return {
                "output": str(e),
                "status": "error",
                "duration_ms": round(duration_ms, 2),
            }

    async def _execute(self, tool: Tool, inputs: dict[str, str], timeout_s: float = 60.0) -> str:
        """Execute a tool. Tries wrapper first, then template."""
        # Try wrapper
        if tool.wrapper_path:
            wrapper = Path(tool.wrapper_path)
            if wrapper.exists():
                return await self._invoke_wrapper(wrapper, inputs)
            else:
                logger.warning(
                    "wrapper_path %s not found, falling back to template", tool.wrapper_path
                )

        # Fall back to template
        if tool.invocation_template:
            cmd = self._build_command(tool, inputs)
            return await asyncio.to_thread(self._run_shell, cmd, timeout_s)

        # No invocation method
        return f"No invocation method for tool {tool.name}"

    async def _invoke_wrapper(self, wrapper_path: Path, inputs: dict[str, str]) -> str:
        """Dynamically import and call a wrapper's invoke() function."""
        # Validate the wrapper is under an expected directory and not world-writable
        resolved = wrapper_path.resolve()
        if resolved.stat().st_mode & 0o002:
            raise InvocationError(f"Wrapper {wrapper_path} is world-writable — refusing to load")

        spec = importlib.util.spec_from_file_location("wrapper", wrapper_path)
        if spec is None or spec.loader is None:
            raise InvocationError(f"Cannot load wrapper from {wrapper_path}")

        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)  # type: ignore[union-attr]

        if not hasattr(module, "invoke"):
            raise InvocationError(f"Wrapper {wrapper_path} has no invoke() function")

        result = module.invoke(inputs)
        return str(result.get("output", ""))

    def _build_command(self, tool: Tool, inputs: dict[str, str]) -> str:
        """Build a shell command from invocation_template + inputs."""
        cmd = tool.invocation_template or ""
        for key, val in inputs.items():
            cmd = cmd.replace(f"{{{key}}}", val)
        return cmd

    @staticmethod
    def _run_shell(cmd: str, timeout: float = 60.0) -> str:
        """Run a shell command and return stdout."""
        import subprocess

        proc = subprocess.run(
            shlex.split(cmd), capture_output=True, text=True, timeout=timeout
        )
        if proc.returncode != 0:
            raise InvocationError(f"Command failed (rc={proc.returncode}): {proc.stderr}")
        return str(proc.stdout)

    def _extract_template_keys(self, tool: Tool) -> set[str]:
        """Extract {key} placeholders from invocation_template."""
        if not tool.invocation_template:
            return set()
        return set(re.findall(r"\{(\w+)\}", tool.invocation_template))

    def _enforce_rate_limit(self, tool: Tool) -> None:
        """Check and enforce per-hour rate limit."""
        if not tool.rate_limit_per_hour:
            return

        state = self._load_rate_state()
        tool_key = str(tool.id)
        now = time.time()

        entry = state.get(tool_key, {"count": 0, "window_start": now})
        window_start = entry.get("window_start", now)
        count = entry.get("count", 0)

        # Reset if window expired
        if now - window_start > 3600:
            entry = {"count": 0, "window_start": now}
            count = 0

        if count >= tool.rate_limit_per_hour:
            seconds_left = 3600 - (now - window_start)
            raise RateLimitExceeded(
                f"Rate limit exceeded for {tool.name}: "
                f"{tool.rate_limit_per_hour}/hour. "
                f"Resets in {int(seconds_left)}s"
            )

        entry["count"] = count + 1
        state[tool_key] = entry
        self._save_rate_state(state)

    def _load_rate_state(self) -> dict[str, Any]:
        """Load per-hour rate limit state from JSON file."""
        if not RATE_LIMIT_STATE_FILE.exists():
            return {}
        try:
            data: dict[str, Any] = json.loads(RATE_LIMIT_STATE_FILE.read_text())
            return data
        except (json.JSONDecodeError, OSError):
            logger.warning("Corrupt rate limit state file, treating as fresh")
            return {}

    def _save_rate_state(self, state: dict[str, Any]) -> None:
        """Save rate limit state to JSON file."""
        RATE_LIMIT_STATE_DIR.mkdir(parents=True, exist_ok=True)
        try:
            RATE_LIMIT_STATE_FILE.write_text(json.dumps(state))
        except OSError as e:
            logger.warning("Failed to save rate limit state: %s", e)

    def _update_invocation_count(self, tool_id: int) -> None:
        """Increment the invocation count for a tool."""
        self._cache.increment_invocation_count(tool_id)


def _sanitize_input(value: Any) -> str:
    """Sanitize an input value for shell use via shlex.quote()."""
    return shlex.quote(str(value))
