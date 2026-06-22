"""
Static analysis: every metric query in every dashboard JSON must start with
the vertical's env_prefix so the widget shows live data after ``make up``.

Bug classes caught:
  - otelcol.* metrics (require standalone OTel Collector binary, not in stack)
  - Wrong-namespace metrics (e.g. finserv.app.* for device-type widgets)
  - Typos in metric names that don't match the vertical's namespace

No credentials required — runs as part of ``make test``.
"""

import json
import re
from pathlib import Path
from typing import List, Tuple

import pytest
import yaml

REPO_ROOT = Path(__file__).parent.parent
VERTICALS_ROOT = REPO_ROOT / "verticals"

# Non-env_prefix namespaces emitted by real docker-compose services (otelcol,
# postgresql, care.companion). Imported from the validator so this static test
# and `dd-demo validate` share ONE allowlist and can't drift. Extend it there.
from dd_demo_toolkit.validation.core import (  # noqa: E402
    PLATFORM_METRIC_PREFIXES as _PLATFORM_METRIC_PREFIXES,
)

# Widget types that never contain metric data sources — skip them entirely.
_NO_QUERY_TYPES = frozenset({
    "note", "free_text", "image", "iframe",
    "alert_graph", "alert_value", "check_status",
    "slo", "event_stream", "event_timeline",
    "manage_status", "trace_service", "service_summary",
    "run_workflow",
})


def _load_env_prefix(vertical_name: str) -> str:
    config_path = VERTICALS_ROOT / vertical_name / "config.yaml"
    config = yaml.safe_load(config_path.read_text())
    return config["vertical"]["env_prefix"]


def _extract_metric_names(widgets: list) -> List[Tuple[str, str]]:
    """Return (widget_title_or_type, metric_name) for every metric query found."""
    results: List[Tuple[str, str]] = []
    for widget in widgets:
        defn = widget.get("definition", {})
        widget_type = defn.get("type", "")
        title = defn.get("title") or widget_type

        if widget_type == "group":
            results.extend(_extract_metric_names(defn.get("widgets", [])))
            continue

        if widget_type in _NO_QUERY_TYPES:
            continue

        for req in defn.get("requests", []):
            # New-style: requests[].queries[] with explicit data_source field.
            for q in req.get("queries", []):
                if q.get("data_source") == "metrics":
                    query_str = q.get("query", "")
                    match = re.match(r"\w+:([\w.]+)", query_str)
                    if match:
                        results.append((title, match.group(1)))

            # Old-style: requests[].q (single query string).
            q_str = req.get("q", "")
            if q_str:
                for match in re.finditer(r"\w+:([\w.]+)", q_str):
                    results.append((title, match.group(1)))

    return results


def _dashboard_params():
    for dashboard_path in sorted(VERTICALS_ROOT.rglob("dashboards/*.json")):
        rel = dashboard_path.relative_to(VERTICALS_ROOT)
        vertical_name = rel.parts[0]
        env_prefix = _load_env_prefix(vertical_name)
        yield pytest.param(
            dashboard_path,
            env_prefix,
            id=str(rel),
        )


@pytest.mark.parametrize("dashboard_path,env_prefix", list(_dashboard_params()))
def test_dashboard_metric_namespaces(dashboard_path: Path, env_prefix: str) -> None:
    """Every metric query in the dashboard must start with the vertical's env_prefix.

    Catches otelcol.* metrics, wrong-namespace metrics, and typos before a
    demo run reveals an empty chart.
    """
    data = json.loads(dashboard_path.read_text())
    metric_tuples = _extract_metric_names(data.get("widgets", []))

    if not metric_tuples:
        pytest.skip("no metric queries found in dashboard")

    violations = [
        (title, metric)
        for title, metric in metric_tuples
        if not metric.startswith(f"{env_prefix}.")
        and not any(metric.startswith(p) for p in _PLATFORM_METRIC_PREFIXES)
    ]

    assert not violations, (
        f"{dashboard_path.relative_to(REPO_ROOT)} has metrics that don't "
        f"start with '{env_prefix}.':\n"
        + "\n".join(f"  widget '{title}': {metric}" for title, metric in violations)
    )
