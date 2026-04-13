"""Structured logging for ToolDB.

Every cascade decision, discovery call, and invocation is logged with
structured JSON fields for traceability.
"""

from __future__ import annotations

import json
import logging
import sys

logger = logging.getLogger("tooldb")


class StructuredFormatter(logging.Formatter):
    """Formats log records as JSON lines with structured extra fields."""

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, object] = {
            "ts": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "msg": record.getMessage(),
        }
        # Merge any extra structured fields
        if hasattr(record, "structured"):
            entry.update(record.structured)  # type: ignore[arg-type]
        return json.dumps(entry, default=str)


def setup_logging(level: str = "INFO") -> None:
    """Configure tooldb logger with structured JSON output."""
    log = logging.getLogger("tooldb")
    if log.handlers:
        return  # already configured
    log.setLevel(getattr(logging, level.upper(), logging.INFO))
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(StructuredFormatter())
    log.addHandler(handler)
    log.propagate = False


def log_cascade_decision(event: str, **kwargs: object) -> None:
    """Log a cascade layer decision with structured data.

    Example:
        log_cascade_decision("cache_hit", layer=1, task="pdf converter", tool_id=42)
    """
    extra = {"structured": {"event": event, **kwargs}}
    logger.info("cascade: %s", event, extra=extra)


def log_discovery(source: str, event: str, **kwargs: object) -> None:
    """Log a discovery source event."""
    extra = {"structured": {"source": source, "event": event, **kwargs}}
    logger.info("discovery[%s]: %s", source, event, extra=extra)


def log_invocation(event: str, **kwargs: object) -> None:
    """Log a tool invocation event."""
    extra = {"structured": {"event": event, **kwargs}}
    logger.info("invoke: %s", event, extra=extra)
