"""Discovery source protocol and shared types."""

from __future__ import annotations

from typing import Protocol, TypedDict, runtime_checkable


class ToolCandidate(TypedDict):
    """A tool discovered from an external source, not yet persisted."""

    name: str
    url: str
    type: str  # "repo", "api", "service", "cli"
    description: str
    task_tags: list[str]
    license: str | None
    stars: int | None
    auth_required: bool | None
    cost_tier: str | None


@runtime_checkable
class DiscoverySource(Protocol):
    """Protocol for tool discovery sources."""

    source_name: str

    async def search(self, task: str, limit: int = 5) -> list[ToolCandidate]: ...
