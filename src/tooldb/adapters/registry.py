"""Extract metadata from tool READMEs using an LLM (MCP-only).

The LLM call is injected — no direct API dependency. CLI mode skips
this module entirely.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from tooldb.logging import logger


class ExtractionError(Exception):
    """Raised when metadata extraction fails."""


@dataclass
class ToolMetadata:
    """Structured metadata extracted from a tool's README."""

    install_cmd: str | None = None
    invocation_template: str | None = None
    auth_method: str | None = None
    auth_env_var: str | None = None
    rate_limit_per_hour: int | None = None
    rate_limit_per_sec: float | None = None
    cost_tier: str | None = None
    dockerized: bool = False
    task_tags: list[str] = field(default_factory=list)


_EXTRACTION_PROMPT = """\
Extract structured metadata from the following tool README. Return ONLY valid JSON
with these fields (use null for unknown):

{{
  "install_cmd": "shell command to install, or null",
  "invocation_template": "example usage command/code, or null",
  "auth_method": "none|api_key|oauth2|bearer|basic, or null",
  "auth_env_var": "env var name for auth, or null",
  "rate_limit_per_hour": integer or null,
  "rate_limit_per_sec": float or null,
  "cost_tier": "free|freemium|paid|unknown",
  "dockerized": true/false,
  "task_tags": ["list", "of", "relevant", "tags"]
}}

README content:
{readme_content}
"""

MAX_README_LENGTH = 50_000


async def extract_metadata_from_readme(
    readme_content: str,
    llm_call: Callable[[str], Awaitable[str]] | None = None,
) -> ToolMetadata:
    """Extract structured metadata from a README using an LLM.

    Args:
        readme_content: Raw README text.
        llm_call: Async callable that takes a prompt and returns LLM response.
                  Required — raises ExtractionError if None.

    Returns:
        ToolMetadata with extracted fields.
    """
    if llm_call is None:
        raise ExtractionError(
            "extract_metadata_from_readme requires an llm_call callable "
            "(only available via MCP, not CLI)"
        )

    if not readme_content or not readme_content.strip():
        logger.warning("Empty README provided for extraction")
        return ToolMetadata()

    # Truncate overly long READMEs
    content = readme_content[:MAX_README_LENGTH]
    prompt = _EXTRACTION_PROMPT.format(readme_content=content)

    # Try up to 2 times
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            response = await llm_call(prompt)
            return _parse_response(response)
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            last_error = e
            logger.warning("Extraction attempt %d failed: %s", attempt + 1, e)

    raise ExtractionError(f"Failed to parse LLM response after 2 attempts: {last_error}")


def _parse_response(response: str) -> ToolMetadata:
    """Parse LLM JSON response into ToolMetadata."""
    # Strip markdown code fences if present
    text = response.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove first and last lines (fences)
        lines = [ln for ln in lines if not ln.strip().startswith("```")]
        text = "\n".join(lines)

    data: dict[str, Any] = json.loads(text)

    return ToolMetadata(
        install_cmd=data.get("install_cmd"),
        invocation_template=data.get("invocation_template"),
        auth_method=data.get("auth_method"),
        auth_env_var=data.get("auth_env_var"),
        rate_limit_per_hour=_safe_int(data.get("rate_limit_per_hour")),
        rate_limit_per_sec=_safe_float(data.get("rate_limit_per_sec")),
        cost_tier=data.get("cost_tier", "unknown"),
        dockerized=bool(data.get("dockerized", False)),
        task_tags=data.get("task_tags", []) or [],
    )


def _safe_int(val: Any) -> int | None:
    if val is None:
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


def _safe_float(val: Any) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None
