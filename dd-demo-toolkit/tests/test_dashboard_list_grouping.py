"""
Tests for per-vertical dashboard-list grouping (the API-supported stand-in for
"favorites", which Datadog does not expose via API).

On deploy, the toolkit groups each vertical's dashboards into a shared manual
dashboard list ("dd-demo-toolkit — <vertical>"), creating it if absent and
reusing it otherwise; on teardown the list is removed (exact-name match for a
single vertical, prefix match for the all-verticals sweep). Grouping is
non-fatal — a listing failure must not fail the deploy.
"""

import json
from pathlib import Path

from dd_demo_toolkit.resources.dashboards import DashboardManager, _dashboard_list_name


class _FakeAPI:
    site = "datadoghq.com"

    def __init__(self, existing_lists=None, dashboards_for_teardown=None):
        self._dash_seq = 0
        self._list_seq = 0
        self.lists = list(existing_lists or [])          # [{"id","name"}]
        self.created_lists = []                          # names created
        self.added = []                                  # [(list_id, items)]
        self.deleted_lists = []                          # list ids deleted
        self.deleted_dashboards = []
        self._dashboards_for_teardown = list(dashboards_for_teardown or [])

    # deploy path
    def create_dashboard(self, payload):
        self._dash_seq += 1
        return {"id": f"dash-{self._dash_seq}"}

    def list_dashboard_lists(self):
        return {"dashboard_lists": list(self.lists)}

    def create_dashboard_list(self, name):
        self._list_seq += 1
        lid = f"list-{self._list_seq}"
        self.lists.append({"id": lid, "name": name})
        self.created_lists.append(name)
        return {"id": lid}

    def add_dashboards_to_list(self, list_id, items):
        self.added.append((list_id, items))
        return {"dashboards": items}

    # teardown path
    def list_dashboards(self):
        return {"dashboards": list(self._dashboards_for_teardown)}

    def delete_dashboard(self, did):
        self.deleted_dashboards.append(did)

    def delete_dashboard_list(self, list_id):
        self.deleted_lists.append(list_id)


def _mk_vertical(tmp_path: Path, n=2, layout="ordered") -> Path:
    d = tmp_path / "healthcare" / "dashboards"
    d.mkdir(parents=True)
    for i in range(n):
        (d / f"dash{i}.json").write_text(
            json.dumps({"title": f"D{i}", "layout_type": layout, "widgets": []})
        )
    return tmp_path / "healthcare"


def test_deploy_creates_list_and_adds_dashboards(tmp_path):
    vpath = _mk_vertical(tmp_path, n=2)
    api = _FakeAPI()

    res = DashboardManager().deploy(str(vpath), api, vertical_name="healthcare")

    assert res["total_created"] == 2
    assert api.created_lists == ["dd-demo-toolkit — healthcare"]
    assert len(api.added) == 1
    list_id, items = api.added[0]
    assert res["dashboard_list_id"] == list_id
    assert {i["id"] for i in items} == {"dash-1", "dash-2"}
    assert all(i["type"] == "custom_timeboard" for i in items)


def test_deploy_reuses_existing_list(tmp_path):
    vpath = _mk_vertical(tmp_path, n=1)
    api = _FakeAPI(existing_lists=[{"id": "L9", "name": "dd-demo-toolkit — healthcare"}])

    res = DashboardManager().deploy(str(vpath), api, vertical_name="healthcare")

    assert api.created_lists == []             # reused, not recreated
    assert api.added[0][0] == "L9"
    assert res["dashboard_list_id"] == "L9"


def test_free_layout_uses_screenboard_type(tmp_path):
    vpath = _mk_vertical(tmp_path, n=1, layout="free")
    api = _FakeAPI()

    DashboardManager().deploy(str(vpath), api, vertical_name="healthcare")

    _, items = api.added[0]
    assert items[0]["type"] == "custom_screenboard"


def test_dry_run_makes_no_list_calls(tmp_path):
    vpath = _mk_vertical(tmp_path, n=2)
    api = _FakeAPI()

    res = DashboardManager().deploy(str(vpath), api, vertical_name="healthcare", dry_run=True)

    assert api.created_lists == []
    assert api.added == []
    assert res["dashboard_list_id"] is None


def test_grouping_failure_is_non_fatal(tmp_path):
    vpath = _mk_vertical(tmp_path, n=1)

    class _BoomAPI(_FakeAPI):
        def list_dashboard_lists(self):
            raise RuntimeError("boom")

    api = _BoomAPI()
    res = DashboardManager().deploy(str(vpath), api, vertical_name="healthcare")

    assert res["total_created"] == 1           # dashboards still deployed
    assert res["total_errors"] == 0            # grouping error is not a deploy error
    assert res["dashboard_list_id"] is None


def test_teardown_deletes_only_matching_list(tmp_path):
    api = _FakeAPI(existing_lists=[
        {"id": "L1", "name": "dd-demo-toolkit — healthcare"},
        {"id": "LX", "name": "A customer's own list"},
    ])
    res = DashboardManager().teardown(api, vertical_name="healthcare")
    assert api.deleted_lists == ["L1"]
    assert res["deleted_list_ids"] == ["L1"]


def test_teardown_all_verticals_deletes_by_prefix(tmp_path):
    api = _FakeAPI(existing_lists=[
        {"id": "L1", "name": "dd-demo-toolkit — healthcare"},
        {"id": "L2", "name": "dd-demo-toolkit — finance"},
        {"id": "LX", "name": "A customer's own list"},
    ])
    DashboardManager().teardown(api, vertical_name=None)
    assert sorted(api.deleted_lists) == ["L1", "L2"]


def test_list_name_helper():
    assert _dashboard_list_name("healthcare") == "dd-demo-toolkit — healthcare"
