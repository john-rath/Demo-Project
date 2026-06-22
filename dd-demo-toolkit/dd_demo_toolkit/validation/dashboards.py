"""Validator for ``dashboards/*.json`` (STYLE_GUIDE §1.1/§1.2/§1.4/§1.9/§4.2)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional

from .core import (
    PLATFORM_METRIC_PREFIXES,
    NO_QUERY_TYPES,
    Finding,
    Severity,
    as_count_before_by,
    first_metric,
    has_percentile,
    iter_widgets,
    request_query_strings,
)


def validate(path, env_prefix: Optional[str] = None, rel: Optional[str] = None) -> List[Finding]:
    rel = rel or str(path)
    try:
        data = json.loads(Path(path).read_text())
    except (json.JSONDecodeError, OSError) as e:
        return [Finding(Severity.ERROR, "DDD000", "dashboard", f"JSON parse error: {e}", file=rel)]

    title = data.get("title", "") or Path(path).name
    findings: List[Finding] = []

    for wtitle, wtype, defn in iter_widgets(data.get("widgets", [])):
        if wtype in NO_QUERY_TYPES:
            continue
        loc = f"widget '{wtitle}'"

        # §4.2c — query_value does not support `suffix`
        if wtype == "query_value" and "suffix" in defn:
            findings.append(Finding(Severity.ERROR, "DDD003", "dashboard",
                "query_value widget has 'suffix' (not in the schema → 400); "
                "use 'custom_unit' or omit.", "§4.2c", title, rel, loc))

        for req in defn.get("requests", []) or []:
            if not isinstance(req, dict):
                continue
            has_queries = bool(isinstance(req.get("queries"), list) and req.get("queries"))

            # §4.2b — new-format request needs response_format
            if has_queries and "response_format" not in req:
                findings.append(Finding(Severity.ERROR, "DDD002", "dashboard",
                    "Request has 'queries' but no 'response_format' — rejected by the API.",
                    "§4.2b", title, rel, loc))
            # §4.2b — legacy on_right_yaxis at request root in new format
            if has_queries and "on_right_yaxis" in req:
                findings.append(Finding(Severity.WARNING, "DDD007", "dashboard",
                    "Legacy 'on_right_yaxis' at request root in a new-format request; omit it.",
                    "§4.2b", title, rel, loc))
            # §1.4 — scalar widgets need an explicit aggregator
            if wtype == "query_value":
                for q in req.get("queries", []) or []:
                    if (isinstance(q, dict)
                            and q.get("data_source", "metrics") == "metrics"
                            and "aggregator" not in q):
                        findings.append(Finding(Severity.WARNING, "DDD005", "dashboard",
                            "query_value query missing explicit 'aggregator' "
                            "(sum/avg/max/min/last).", "§1.4", title, rel, loc))

            for qs in request_query_strings(req):
                if as_count_before_by(qs):
                    findings.append(Finding(Severity.ERROR, "DDD004", "dashboard",
                        "'.as_count()' before 'by {…}' in a widget query.", "§1.2", title, rel, loc))
                if has_percentile(qs):
                    findings.append(Finding(Severity.WARNING, "DDD006", "dashboard",
                        "Percentile aggregator in a widget query; prefer avg:/max: unless "
                        "percentiles are enabled on the metric.", "§1.1", title, rel, loc))
                if env_prefix:
                    metric = first_metric(qs)
                    if (metric
                            and not metric.startswith(f"{env_prefix}.")
                            and not any(metric.startswith(p) for p in PLATFORM_METRIC_PREFIXES)):
                        # WARNING, not ERROR: a non-env_prefix metric renders an
                        # empty widget — it does NOT cause a deploy 400. Many
                        # overlay/product dashboards legitimately use mock-app
                        # namespaces (e.g. care.companion.* from the AI Care
                        # Companion service). The live check (`make validate-live`)
                        # is the authoritative empty-data gate.
                        findings.append(Finding(Severity.WARNING, "DDD001", "dashboard",
                            f"Metric '{metric}' doesn't start with env_prefix '{env_prefix}.' — "
                            "renders empty unless its emitting service runs (e.g. the mock app); "
                            "check for a typo otherwise.", "§1.9", title, rel, loc))

    return findings
