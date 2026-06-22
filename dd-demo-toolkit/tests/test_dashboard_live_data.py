"""
Integration tests: every metric referenced in every dashboard must return at
least one data point from the Datadog API in the past hour.

Run via ``make validate-live`` (wraps with ``op run`` to resolve credentials).
Skipped automatically when DD_API_KEY / DD_APP_KEY are absent.

These tests are marked ``pytest.mark.integration`` and excluded from the
default ``make test`` run so they don't block offline development.
"""

import json
import os
import re
import time
from pathlib import Path
from typing import List, Set, Tuple

import pytest

REPO_ROOT = Path(__file__).parent.parent
VERTICALS_ROOT = REPO_ROOT / "verticals"

pytestmark = pytest.mark.integration

# Widget types that never contain metric data sources.
_NO_QUERY_TYPES = frozenset({
    "note", "free_text", "image", "iframe",
    "alert_graph", "alert_value", "check_status",
    "slo", "event_stream", "event_timeline",
    "manage_status", "trace_service", "service_summary",
    "run_workflow",
})


def _extract_metric_names_from_widgets(widgets: list) -> Set[str]:
    names: Set[str] = set()
    for widget in widgets:
        defn = widget.get("definition", {})
        widget_type = defn.get("type", "")

        if widget_type == "group":
            names |= _extract_metric_names_from_widgets(defn.get("widgets", []))
            continue

        if widget_type in _NO_QUERY_TYPES:
            continue

        for req in defn.get("requests", []):
            for q in req.get("queries", []):
                if q.get("data_source") == "metrics":
                    query_str = q.get("query", "")
                    match = re.match(r"\w+:([\w.]+)", query_str)
                    if match:
                        names.add(match.group(1))
            q_str = req.get("q", "")
            if q_str:
                for match in re.finditer(r"\w+:([\w.]+)", q_str):
                    names.add(match.group(1))
    return names


def _all_dashboard_metrics() -> List[Tuple[str, str]]:
    """Return sorted list of (metric_name, source_dashboard_rel_path) tuples, deduplicated."""
    seen: Set[str] = set()
    results: List[Tuple[str, str]] = []
    for dashboard_path in sorted(VERTICALS_ROOT.rglob("dashboards/*.json")):
        rel = str(dashboard_path.relative_to(REPO_ROOT))
        data = json.loads(dashboard_path.read_text())
        for metric in _extract_metric_names_from_widgets(data.get("widgets", [])):
            if metric not in seen:
                seen.add(metric)
                results.append((metric, rel))
    return sorted(results)


@pytest.fixture(scope="session")
def dd_client():
    if not os.getenv("DD_API_KEY") or not os.getenv("DD_APP_KEY"):
        pytest.skip("DD_API_KEY / DD_APP_KEY not set — run via 'make validate-live'")
    from dd_demo_toolkit.utils.dd_api import DatadogAPIClient
    return DatadogAPIClient()


@pytest.mark.parametrize(
    "metric_name,source_dashboard",
    _all_dashboard_metrics(),
    ids=[m for m, _ in _all_dashboard_metrics()],
)
def test_metric_has_live_data(dd_client, metric_name: str, source_dashboard: str) -> None:
    """Each metric must have at least one data point in the past hour.

    Uses ``avg:<metric>{*}`` to check for any data regardless of tag values.
    Failures mean the simulator isn't emitting the metric (empty chart in demo).
    """
    now = int(time.time())
    one_hour_ago = now - 3600

    response = dd_client.query_metrics(
        query=f"avg:{metric_name}{{*}}",
        from_ts=one_hour_ago,
        to_ts=now,
    )

    series = response.get("series", [])
    has_data = any(
        point[1] is not None
        for s in series
        for point in s.get("pointlist", [])
    )

    assert has_data, (
        f"Metric '{metric_name}' has no data in the past hour. "
        f"Source: {source_dashboard}. "
        "Is the simulator running? Did 'make up' complete successfully?"
    )
