"""Shared tag-key checks (STYLE_GUIDE §2.4) used by monitors/SLOs/workflows."""

from __future__ import annotations

from typing import List, Optional

from .core import Finding, Severity

# Keys that must never be invented — overlays are identified by
# device_manufacturer / incident_domain VALUES, not new keys.
_FORBIDDEN_KEYS_ERROR = {"sub_vertical", "customer", "overlay"}
# Discouraged: Datadog already has first-class handling for these.
_FORBIDDEN_KEYS_WARN = {"env": "use Datadog's standard env tag",
                        "severity": "use the built-in monitor priority field"}


def check_tags(
    tags: Optional[list],
    *,
    resource_type: str,
    resource_name: str,
    file: str,
) -> List[Finding]:
    findings: List[Finding] = []
    for tag in tags or []:
        if not isinstance(tag, str) or ":" not in tag:
            continue
        key = tag.split(":", 1)[0].strip()
        if key in _FORBIDDEN_KEYS_ERROR:
            findings.append(
                Finding(
                    Severity.ERROR, "DDT001", resource_type,
                    f"Forbidden tag key '{key}:' — identify overlays by "
                    f"device_manufacturer/incident_domain values, not a new key.",
                    "§2.4", resource_name, file, f"tag '{tag}'",
                )
            )
        elif key in _FORBIDDEN_KEYS_WARN:
            findings.append(
                Finding(
                    Severity.WARNING, "DDT002", resource_type,
                    f"Avoid inventing tag key '{key}:' — {_FORBIDDEN_KEYS_WARN[key]}.",
                    "§2.4", resource_name, file, f"tag '{tag}'",
                )
            )
    return findings
