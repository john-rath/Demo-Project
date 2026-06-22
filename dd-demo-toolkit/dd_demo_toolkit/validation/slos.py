"""Validator for ``slos.yaml`` (STYLE_GUIDE §6, §1.1/§1.2, §2.4)."""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

import yaml

from .core import (
    Finding,
    Severity,
    as_count_before_by,
    has_percentile,
)
from .tags import check_tags


def _load(path: Path) -> list:
    with open(path) as f:
        config = yaml.safe_load(f)
    if not config:
        return []
    return config if isinstance(config, list) else config.get("slos", []) or []


def validate(path, env_prefix: Optional[str] = None, rel: Optional[str] = None) -> List[Finding]:
    rel = rel or str(path)
    try:
        slos = _load(Path(path))
    except yaml.YAMLError as e:
        return [Finding(Severity.ERROR, "DDS000", "slo", f"YAML parse error: {e}", file=rel)]

    findings: List[Finding] = []
    for idx, slo in enumerate(slos):
        if not isinstance(slo, dict):
            continue
        name = slo.get("name", f"slo[{idx}]")

        for field in ("name", "type"):
            if field not in slo:
                findings.append(Finding(Severity.ERROR, "DDS005", "slo",
                                        f"Missing required field '{field}'.", "§6", name, rel))

        query = slo.get("query")
        if isinstance(query, dict):
            for part in ("numerator", "denominator"):
                expr = query.get(part)
                if not isinstance(expr, str) or not expr:
                    continue
                if ".as_count()" not in expr:
                    findings.append(Finding(Severity.WARNING, "DDS001", "slo",
                        f"SLO {part} should use '.as_count()' (count good/total events).",
                        "§6", name, rel, part))
                if as_count_before_by(expr):
                    findings.append(Finding(Severity.ERROR, "DDS003", "slo",
                        f"'.as_count()' before 'by {{…}}' in SLO {part}.", "§1.2", name, rel, part))
                if has_percentile(expr):
                    findings.append(Finding(Severity.WARNING, "DDS002", "slo",
                        f"Percentile aggregator in SLO {part}; SLOs should count good/total events.",
                        "§1.1", name, rel, part))

        findings += check_tags(slo.get("tags"), resource_type="slo", resource_name=name, file=rel)
    return findings
