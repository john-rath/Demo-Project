"""Validator for ``notebooks.yaml`` (STYLE_GUIDE §1.5/§8.3/§8.4b)."""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

import yaml

from .core import (
    Finding,
    Severity,
    as_count_before_by,
    has_percentile,
    request_query_strings,
)

_VALID_TYPES = {
    "postmortem", "runbook", "investigation",
    "documentation", "report", "workspace", "threat_hunting",
}


def _load(path: Path) -> list:
    with open(path) as f:
        config = yaml.safe_load(f)
    if not config:
        return []
    return config if isinstance(config, list) else config.get("notebooks", []) or []


def validate(path, env_prefix: Optional[str] = None, rel: Optional[str] = None) -> List[Finding]:
    rel = rel or str(path)
    try:
        notebooks = _load(Path(path))
    except yaml.YAMLError as e:
        return [Finding(Severity.ERROR, "DDN000", "notebook", f"YAML parse error: {e}", file=rel)]

    findings: List[Finding] = []
    for idx, nb in enumerate(notebooks):
        if not isinstance(nb, dict):
            continue
        name = nb.get("name", f"notebook[{idx}]")

        # §8.4b — notebook type must be one of the allowed values
        ntype = nb.get("type", "investigation")
        if ntype not in _VALID_TYPES:
            findings.append(Finding(Severity.ERROR, "DDN003", "notebook",
                f"Notebook type '{ntype}' is invalid — must be one of "
                f"{', '.join(sorted(_VALID_TYPES))}.", "§8.4b", name, rel))

        for cidx, cell in enumerate(nb.get("cells", []) or []):
            if not isinstance(cell, dict):
                continue
            attrs = cell.get("attributes", {}) if isinstance(cell.get("attributes"), dict) else {}
            defn = attrs.get("definition", {}) if isinstance(attrs.get("definition"), dict) else {}
            if defn.get("type") != "timeseries":
                continue
            loc = f"cell #{cidx} (timeseries)"

            # §8.3b — legend hidden by default
            if defn.get("show_legend") is not True:
                findings.append(Finding(Severity.WARNING, "DDN002", "notebook",
                    "Timeseries cell missing 'show_legend: true' (multi-series charts "
                    "are unreadable without it).", "§8.3b", name, rel, loc))

            for req in defn.get("requests", []) or []:
                if not isinstance(req, dict):
                    continue
                # §1.5 — every timeseries request needs formulas or it renders empty
                if not req.get("formulas"):
                    findings.append(Finding(Severity.ERROR, "DDN001", "notebook",
                        "Timeseries request missing 'formulas:' — the chart renders empty.",
                        "§1.5", name, rel, loc))
                for qs in request_query_strings(req):
                    if as_count_before_by(qs):
                        findings.append(Finding(Severity.ERROR, "DDN004", "notebook",
                            "'.as_count()' before 'by {…}' in a notebook query.",
                            "§1.2", name, rel, loc))
                    if has_percentile(qs):
                        findings.append(Finding(Severity.WARNING, "DDN005", "notebook",
                            "Percentile aggregator in a notebook query; prefer avg:/max:.",
                            "§1.1", name, rel, loc))

    return findings
