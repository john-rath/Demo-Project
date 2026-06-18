"""Validator for ``monitors.yaml`` (STYLE_GUIDE §1.x, §5, §2.4)."""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

import yaml

from .core import (
    Finding,
    Severity,
    as_count_before_by,
    has_logical_operator,
    has_percentile,
)
from .tags import check_tags

_REQUIRED = ["name", "type", "query", "message"]


def _load(path: Path) -> list:
    with open(path) as f:
        config = yaml.safe_load(f)
    if not config:
        return []
    return config if isinstance(config, list) else config.get("monitors", []) or []


def validate(path, env_prefix: Optional[str] = None, rel: Optional[str] = None) -> List[Finding]:
    rel = rel or str(path)
    try:
        monitors = _load(Path(path))
    except yaml.YAMLError as e:
        return [Finding(Severity.ERROR, "DDM000", "monitor", f"YAML parse error: {e}", file=rel)]

    findings: List[Finding] = []
    for idx, m in enumerate(monitors):
        if not isinstance(m, dict):
            findings.append(Finding(Severity.ERROR, "DDM005", "monitor",
                                    f"Monitor #{idx} is not a mapping.", "§5", file=rel))
            continue
        name = m.get("name", f"monitor[{idx}]")

        for field in _REQUIRED:
            if field not in m:
                findings.append(Finding(Severity.ERROR, "DDM005", "monitor",
                                        f"Missing required field '{field}'.", "§5", name, rel))

        query = m.get("query", "")
        if isinstance(query, str) and query:
            if has_logical_operator(query):
                findings.append(Finding(Severity.ERROR, "DDM001", "monitor",
                    "Query uses '||'/'&&'; query alerts don't support logical operators — "
                    "split into two monitors or use a composite.", "§1.3", name, rel, "query"))
            if as_count_before_by(query):
                findings.append(Finding(Severity.ERROR, "DDM003", "monitor",
                    "'.as_count()' appears before 'by {…}' — put 'by {dim}' first.",
                    "§1.2", name, rel, "query"))
            if has_percentile(query):
                findings.append(Finding(Severity.WARNING, "DDM002", "monitor",
                    "Percentile aggregator (pNN:) only returns data on distribution metrics "
                    "with percentiles enabled; prefer avg:/max:.", "§1.1", name, rel, "query"))

        findings += check_tags(m.get("tags"), resource_type="monitor", resource_name=name, file=rel)
    return findings
