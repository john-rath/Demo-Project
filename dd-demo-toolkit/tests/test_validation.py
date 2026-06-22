"""
Tests for the local asset validator (dd_demo_toolkit/validation).

Two layers:
  1. Rule fixtures — the STYLE_GUIDE ✅/❌ snippets encoded as inputs so the
     doc and the linter can't silently drift.
  2. Regression — every shipped vertical must be free of ERROR-severity
     findings (warnings are allowed). This is the gate that makes
     contribute-back safe: a PR that introduces a deploy-blocking footgun
     fails here, offline, with no credentials.

No credentials / network required (runs in `make test` and CI).
"""

import json
import textwrap
from pathlib import Path

import pytest

from dd_demo_toolkit.config import ConfigLoader
from dd_demo_toolkit.validation import Severity, summarize, validate_vertical
from dd_demo_toolkit.validation import (
    core,
    dashboards,
    monitors,
    notebooks,
    slos,
    workflows,
)


def _write(tmp_path: Path, rel: str, content: str) -> Path:
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return p


# --------------------------------------------------------------------------
# 1. Query predicate unit tests (STYLE_GUIDE §1.1, §1.2, §1.3)
# --------------------------------------------------------------------------

def test_has_percentile():
    assert core.has_percentile("p95:hospital.app.latency_ms{*}")
    assert core.has_percentile("avg(last_5m):p99:hospital.app.latency_ms{*}")
    assert not core.has_percentile("avg:hospital.app.latency_ms{*}")
    # `p95` inside a metric name is not an aggregator
    assert not core.has_percentile("max:hospital.pyxis.p95_score{*}")


def test_has_logical_operator():
    assert core.has_logical_operator("avg:x{*} < 34.8 || avg:x{*} > 36.2")
    assert core.has_logical_operator("a && b")
    assert not core.has_logical_operator("avg:x{*} > 5")


def test_as_count_before_by():
    assert core.as_count_before_by("sum:x{*}.as_count() by {device_id}")
    assert not core.as_count_before_by("sum:x{*} by {device_id}.as_count()")
    assert not core.as_count_before_by("sum:x{*}.as_count()")
    assert not core.as_count_before_by("avg:x{*} by {d}")


def test_first_metric():
    assert core.first_metric("avg:hospital.device.online{*} by {floor}") == "hospital.device.online"
    assert core.first_metric("sum:finserv.app.errors_total{*}.as_count()") == "finserv.app.errors_total"
    assert core.first_metric("") is None


# --------------------------------------------------------------------------
# 2. Per-resource validator fixtures
# --------------------------------------------------------------------------

def test_monitor_logical_operator_is_error(tmp_path):
    p = _write(tmp_path, "monitors.yaml", textwrap.dedent("""
        monitors:
          - name: "Bad Range"
            type: "query alert"
            query: "avg(last_5m):avg:hospital.x{*} < 1 || avg:hospital.x{*} > 9"
            message: "x"
    """))
    fs = monitors.validate(p)
    assert any(f.rule_id == "DDM001" and f.severity == Severity.ERROR for f in fs)


def test_monitor_percentile_is_warning_not_error(tmp_path):
    p = _write(tmp_path, "monitors.yaml", textwrap.dedent("""
        - name: "P99"
          type: "query alert"
          query: "avg(last_5m):p99:hospital.app.latency_ms{*} > 2000"
          message: "x"
    """))
    fs = monitors.validate(p)
    assert any(f.rule_id == "DDM002" and f.severity == Severity.WARNING for f in fs)
    assert not any(f.severity == Severity.ERROR for f in fs)


def test_monitor_missing_required_field(tmp_path):
    p = _write(tmp_path, "monitors.yaml", textwrap.dedent("""
        - name: "X"
          type: "query alert"
          query: "avg:hospital.x{*} > 1"
    """))
    fs = monitors.validate(p)
    assert any(f.rule_id == "DDM005" for f in fs)  # missing 'message'


def test_monitor_forbidden_tag_key(tmp_path):
    p = _write(tmp_path, "monitors.yaml", textwrap.dedent("""
        - name: "X"
          type: "query alert"
          query: "avg:hospital.x{*} > 1"
          message: "x"
          tags: ["overlay:acme"]
    """))
    fs = monitors.validate(p)
    assert any(f.rule_id == "DDT001" and f.severity == Severity.ERROR for f in fs)


def test_dashboard_response_format_suffix_aggregator(tmp_path):
    dash = {
        "title": "T",
        "widgets": [{
            "definition": {
                "type": "query_value", "title": "kpi", "suffix": "%",
                "requests": [{
                    "queries": [{"data_source": "metrics", "name": "q", "query": "avg:hospital.x{*}"}],
                }],
            }
        }],
    }
    p = _write(tmp_path, "dashboards/d.json", json.dumps(dash))
    rules = {f.rule_id for f in dashboards.validate(p, env_prefix="hospital")}
    assert "DDD002" in rules  # missing response_format
    assert "DDD003" in rules  # suffix on query_value
    assert "DDD005" in rules  # query_value missing aggregator


def test_dashboard_wrong_namespace_is_warning(tmp_path):
    # A non-env_prefix metric renders an empty widget (not a 400), so it is a
    # WARNING — overlay/product dashboards legitimately use other namespaces.
    dash = {
        "title": "T",
        "widgets": [{
            "definition": {
                "type": "timeseries", "title": "w",
                "requests": [{
                    "response_format": "timeseries",
                    "queries": [{"data_source": "metrics", "name": "q", "query": "avg:wrong.metric{*}"}],
                }],
            }
        }],
    }
    p = _write(tmp_path, "dashboards/d.json", json.dumps(dash))
    fs = dashboards.validate(p, env_prefix="hospital")
    assert any(f.rule_id == "DDD001" and f.severity == Severity.WARNING for f in fs)
    assert not any(f.severity == Severity.ERROR for f in fs)


def test_notebook_type_formulas_legend(tmp_path):
    p = _write(tmp_path, "notebooks.yaml", textwrap.dedent("""
        notebooks:
          - name: "N"
            type: executive_report
            cells:
              - type: notebook_cells
                attributes:
                  definition:
                    type: timeseries
                    requests:
                      - response_format: timeseries
                        queries:
                          - {data_source: metrics, name: q, query: "avg:hospital.x{*}"}
    """))
    rules = {f.rule_id for f in notebooks.validate(p)}
    assert "DDN003" in rules  # invalid notebook type
    assert "DDN001" in rules  # timeseries missing formulas
    assert "DDN002" in rules  # missing show_legend


def test_workflow_description_limit_and_unverified_step(tmp_path):
    long_desc = "x" * 301
    p = _write(tmp_path, "workflows.yaml", textwrap.dedent(f"""
        - name: "W"
          description: "{long_desc}"
          trigger: {{type: monitor}}
          steps:
            - name: do_thing
              type: totally_made_up_action
    """))
    rules = {f.rule_id for f in workflows.validate(p)}
    assert "DDW001" in rules  # description > 300
    assert "DDW002" in rules  # unverified action type


def test_slo_missing_as_count_is_warning(tmp_path):
    p = _write(tmp_path, "slos.yaml", textwrap.dedent("""
        - name: "S"
          type: metric
          query:
            numerator: "sum:hospital.app.ok_total{*}"
            denominator: "sum:hospital.app.req_total{*}.as_count()"
    """))
    fs = slos.validate(p)
    assert any(f.rule_id == "DDS001" for f in fs)  # numerator missing .as_count()


def test_clean_monitor_has_no_findings(tmp_path):
    p = _write(tmp_path, "monitors.yaml", textwrap.dedent("""
        - name: "[Healthcare] Battery Low"
          type: "query alert"
          query: "avg(last_5m):avg:hospital.device.battery_pct{*} by {device_id} < 10"
          message: "battery low"
          tags: ["team:biomed"]
    """))
    assert monitors.validate(p) == []


# --------------------------------------------------------------------------
# 3. Regression: shipped verticals must have zero ERROR findings
# --------------------------------------------------------------------------

@pytest.mark.parametrize("vertical", ConfigLoader("verticals").list_verticals())
def test_shipped_verticals_have_no_errors(vertical):
    findings = validate_vertical(vertical, verticals_dir="verticals")
    errors = [f for f in findings if f.severity == Severity.ERROR]
    assert not errors, "shipped vertical has deploy-blocking findings:\n" + "\n".join(
        f"  {f.rule_id} {f.file} · {f.location}: {f.message}" for f in errors
    )
