"""
Core types and shared query helpers for the local asset validator.

Pure stdlib — **no network, no `op`, no credentials**. This is what lets the
same validation run in the `dd-demo` CLI, in the UI server in-process, and in
CI with nothing configured. Every rule a validator emits carries a stable
``rule_id`` and a ``style_guide_ref`` back to STYLE_GUIDE.md, so a finding is
always traceable to the documented bug it prevents.
"""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterator, List, Optional, Tuple


class Severity(enum.IntEnum):
    """Ordered so callers can do ``max(...)`` / threshold comparisons."""

    INFO = 10
    WARNING = 20
    ERROR = 30

    @property
    def label(self) -> str:
        return self.name.lower()


@dataclass
class Finding:
    """One validation result. ``file`` is repo-relative when possible."""

    severity: Severity
    rule_id: str
    resource_type: str
    message: str
    style_guide_ref: str = ""
    resource_name: str = ""
    file: str = ""
    location: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "severity": self.severity.label,
            "rule_id": self.rule_id,
            "resource_type": self.resource_type,
            "resource_name": self.resource_name,
            "file": self.file,
            "location": self.location,
            "message": self.message,
            "style_guide_ref": self.style_guide_ref,
        }


# --------------------------------------------------------------------------
# Query-string predicates — shared by the monitor / dashboard / notebook / SLO
# validators so the Datadog query rules have a single implementation.
# --------------------------------------------------------------------------

# Percentile aggregator token (pNN:). STYLE_GUIDE §1.1 — only valid on
# distribution metrics with percentiles enabled; flagged everywhere else.
# The look-behind keeps us from matching a `p95` that's part of a metric name.
_PERCENTILE_RE = re.compile(r"(?<![\w.])p(?:50|75|90|95|99)\s*:")
# `by {dims}` clause.
_BY_RE = re.compile(r"\bby\s*\{")
# First `<aggregator>:<metric>` token in a query string (mirrors the proven
# extraction in tests/test_dashboard_query_coverage.py).
_FIRST_METRIC_RE = re.compile(r"\w+:([\w.]+)")


def has_percentile(query: str) -> bool:
    return bool(query) and bool(_PERCENTILE_RE.search(query))


def has_logical_operator(query: str) -> bool:
    return bool(query) and ("||" in query or "&&" in query)


def as_count_before_by(query: str) -> bool:
    """True when ``.as_count()`` appears BEFORE a ``by {…}`` clause.

    STYLE_GUIDE §1.2 — ``by {dim}`` must come before ``.as_count()``; the
    reversed form produces an opaque parser error at deploy time.
    """
    if not query or ".as_count()" not in query:
        return False
    m = _BY_RE.search(query)
    if not m:
        return False
    return query.index(".as_count()") < m.start()


def first_metric(query: str) -> Optional[str]:
    """The metric name following the first ``<agg>:`` token, or None."""
    if not query:
        return None
    m = _FIRST_METRIC_RE.match(query.strip())
    return m.group(1) if m else None


# --------------------------------------------------------------------------
# Dashboard widget traversal — lifted/generalized from
# tests/test_dashboard_query_coverage.py so the linter and that test agree.
# --------------------------------------------------------------------------

# Widget types that never carry a metric data source.
NO_QUERY_TYPES = frozenset(
    {
        "note", "free_text", "image", "iframe",
        "alert_graph", "alert_value", "check_status",
        "slo", "event_stream", "event_timeline",
        "manage_status", "trace_service", "service_summary",
        "run_workflow",
    }
)

# Non-env_prefix metric namespaces allowed in dashboards because a REAL
# docker-compose service emits them (not the vertical simulator) — the
# STYLE_GUIDE §1.9 "corresponding service in docker-compose" exception:
#   otelcol.*         — OTel Collector self-telemetry (every `make up`)
#   postgresql.*      — Datadog Agent Postgres integration (DBM / mock-app)
#   care.companion.*  — AI Care Companion LLM-Obs metrics (mock-app service)
# Longer term these move into per-vertical products.yaml (Phase 2); until then
# this is the single shared allowlist used by the linter AND by
# tests/test_dashboard_query_coverage.py.
PLATFORM_METRIC_PREFIXES = frozenset({"otelcol", "postgresql", "care.companion"})


def iter_widgets(widgets: List[dict]) -> Iterator[Tuple[str, str, dict]]:
    """Yield ``(title, widget_type, definition)`` for every non-group widget,
    recursing into ``group`` widgets."""
    for widget in widgets or []:
        if not isinstance(widget, dict):
            continue
        defn = widget.get("definition", {}) or {}
        wtype = defn.get("type", "")
        title = defn.get("title") or wtype
        if wtype == "group":
            yield from iter_widgets(defn.get("widgets", []))
            continue
        yield (title, wtype, defn)


def request_query_strings(req: dict) -> List[str]:
    """All metric query strings in a widget/notebook request — new-style
    ``queries[].query`` (data_source metrics) plus legacy ``q``."""
    out: List[str] = []
    if not isinstance(req, dict):
        return out
    for q in req.get("queries", []) or []:
        if isinstance(q, dict) and q.get("data_source", "metrics") == "metrics":
            s = q.get("query")
            if isinstance(s, str) and s:
                out.append(s)
    legacy = req.get("q")
    if isinstance(legacy, str) and legacy:
        out.append(legacy)
    return out
