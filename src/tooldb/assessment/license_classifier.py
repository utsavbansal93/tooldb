"""Classify SPDX license identifiers by risk level for enterprise use.

Pure function, no I/O.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tooldb.models import LicenseRisk

# Permissive licenses — generally safe for enterprise use
_LOW_RISK = frozenset({
    "MIT",
    "Apache-2.0",
    "BSD-2-Clause",
    "BSD-3-Clause",
    "ISC",
    "Unlicense",
    "CC0-1.0",
    "0BSD",
    "BSL-1.0",
    "Zlib",
    "PostgreSQL",
    "X11",
    "WTFPL",
})

# Copyleft licenses — usable but require legal review
_MEDIUM_RISK = frozenset({
    "GPL-2.0-only",
    "GPL-2.0-or-later",
    "GPL-3.0-only",
    "GPL-3.0-or-later",
    "LGPL-2.1-only",
    "LGPL-2.1-or-later",
    "LGPL-3.0-only",
    "LGPL-3.0-or-later",
    "MPL-2.0",
    "EPL-2.0",
    "CDDL-1.0",
    # Common short forms people use
    "GPL-2.0",
    "GPL-3.0",
    "LGPL-2.1",
    "LGPL-3.0",
})

# Restrictive licenses — often incompatible with enterprise/commercial use
_HIGH_RISK = frozenset({
    "AGPL-3.0-only",
    "AGPL-3.0-or-later",
    "AGPL-3.0",
    "SSPL-1.0",
    "BUSL-1.1",
    "Elastic-2.0",
    "CC-BY-NC-4.0",
    "CC-BY-NC-SA-4.0",
})


def classify_license_risk(spdx_id: str | None) -> LicenseRisk:
    """Classify an SPDX license identifier by enterprise risk level.

    Returns:
        "low"     — permissive, generally safe for commercial use
        "medium"  — copyleft, requires legal review before commercial use
        "high"    — restrictive, often incompatible with enterprise use
        "unknown" — unrecognized or missing license
    """
    if not spdx_id or spdx_id.strip() in ("", "NOASSERTION", "NONE"):
        return "unknown"

    normalized = spdx_id.strip()

    if normalized in _LOW_RISK:
        return "low"
    if normalized in _MEDIUM_RISK:
        return "medium"
    if normalized in _HIGH_RISK:
        return "high"

    # Check case-insensitively for common variations
    upper = normalized.upper()
    for lic in _LOW_RISK:
        if upper == lic.upper():
            return "low"
    for lic in _MEDIUM_RISK:
        if upper == lic.upper():
            return "medium"
    for lic in _HIGH_RISK:
        if upper == lic.upper():
            return "high"

    return "unknown"
