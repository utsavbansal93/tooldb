"""Pre-invocation safety checks.

Prevents accidental execution of malicious or dangerous tools.
Inspired by supply chain attacks (xz/liblzma) — we can't prevent all
attacks, but we can flag obvious risks before invocation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tooldb.models import Tool

# Domains considered safe sources for tools
_TRUSTED_DOMAINS = frozenset({
    "github.com",
    "gitlab.com",
    "bitbucket.org",
    "pypi.org",
    "npmjs.com",
    "crates.io",
    "pkg.go.dev",
    "rubygems.org",
    "nuget.org",
    "hub.docker.com",
})

# Shell metacharacters that could indicate command injection in templates
_DANGEROUS_PATTERNS = re.compile(
    r"\$\(|`|&&|\|\||;|>\s*/|<\s*/|eval\s|exec\s"
)


@dataclass
class SafetyVerdict:
    """Result of a pre-invocation safety check."""

    safe: bool
    warnings: list[str] = field(default_factory=list)
    blocked_reason: str | None = None


def check_invocation_safety(tool: Tool) -> SafetyVerdict:
    """Check whether a tool is safe to invoke.

    Returns a SafetyVerdict with safe=False if invocation should be blocked,
    or safe=True (possibly with warnings) if it can proceed.

    Checks:
    1. Status "avoid" → blocked
    2. URL from unrecognized domain → warn
    3. invocation_template with dangerous shell patterns → warn
    4. Wrapper file world-writable or suspicious symlink → warn
    """
    warnings: list[str] = []

    # 1. Status gate
    if tool.my_status == "avoid":
        return SafetyVerdict(
            safe=False,
            blocked_reason=f"Tool '{tool.name}' has status 'avoid' — refusing to invoke",
        )

    # 2. URL domain check
    if tool.url:
        domain_ok = any(domain in tool.url for domain in _TRUSTED_DOMAINS)
        if not domain_ok:
            warnings.append(
                f"Tool URL ({tool.url}) is not from a recognized source. "
                "Verify the tool's origin before running."
            )

    # 3. Template injection check
    if tool.invocation_template and _DANGEROUS_PATTERNS.search(tool.invocation_template):
            warnings.append(
                "Invocation template contains potentially dangerous shell patterns "
                f"({tool.invocation_template!r}). Review before executing."
            )

    # 4. Wrapper integrity
    if tool.wrapper_path:
        wrapper = Path(tool.wrapper_path)
        if wrapper.exists():
            # World-writable check
            try:
                mode = wrapper.stat().st_mode
                if mode & 0o002:
                    warnings.append(
                        f"Wrapper file {tool.wrapper_path} is world-writable — "
                        "anyone on the system could have modified it."
                    )
            except OSError:
                warnings.append(f"Cannot stat wrapper file {tool.wrapper_path}")

            # Symlink check
            if wrapper.is_symlink():
                target = wrapper.resolve()
                if not str(target).startswith(str(wrapper.parent)):
                    warnings.append(
                        f"Wrapper {tool.wrapper_path} is a symlink pointing "
                        f"outside its directory to {target}"
                    )

    return SafetyVerdict(safe=True, warnings=warnings)
