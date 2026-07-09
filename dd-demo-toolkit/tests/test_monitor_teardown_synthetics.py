"""
Regression tests for Synthetics-monitor handling in MonitorManager.teardown.

Bug (2026-06-30): Datadog auto-creates a backing monitor (type
"synthetics alert") for every Synthetics test. When the Synthetics test is
tagged dd-demo-toolkit:true (as the sensing-hospital synthetics are), its
monitor inherits the tag and is swept up by the teardown tag filter — but
DELETE /api/v1/monitor/{id} rejects it with:

    400 - "... is a Synthetics monitor and can only be deleted in Synthetics"

so every `dd-demo setup --clean` / `teardown` logged an ERROR per Synthetics
monitor. The fix skips type=="synthetics alert" monitors (tracked under
total_skipped) and defensively downgrades the 400 to a skip if `type` is
absent from the list payload. See STYLE_GUIDE.md §5.6.
"""

from dd_demo_toolkit.resources.monitors import MonitorManager

_TAGS = ["vertical:healthcare", "dd-demo-toolkit:true"]
_SYNTH_400 = (
    '400 - {"errors":["Monitor (...) is a Synthetics monitor and can only be '
    'deleted in Synthetics"]}'
)


class _FakeAPI:
    """Minimal Datadog API stand-in; raises the real 400 for Synthetics IDs."""

    def __init__(self, monitors):
        self._monitors = monitors
        self.delete_calls = []

    def list_monitors(self):
        return {"monitors": self._monitors}

    def delete_monitor(self, mid):
        self.delete_calls.append(mid)
        m = next(x for x in self._monitors if x["id"] == mid)
        if (m.get("type") or "").lower() == "synthetics alert" or m.get("_synthetic_no_type"):
            raise RuntimeError(_SYNTH_400)


def test_teardown_skips_typed_synthetics_monitors():
    monitors = [
        {"id": 1, "name": "[Healthcare/Ascension] RTLS Lag", "type": "query alert", "tags": _TAGS},
        {"id": 2, "name": "[Healthcare] WiFi Util", "type": "query alert", "tags": _TAGS},
        {"id": 296180420, "name": "synthetics A", "type": "synthetics alert", "tags": _TAGS},
        {"id": 296180421, "name": "synthetics B", "type": "synthetics alert", "tags": _TAGS},
    ]
    api = _FakeAPI(monitors)

    res = MonitorManager().teardown(api, vertical_name="healthcare")

    # Real toolkit monitors deleted; Synthetics monitors never delete-attempted.
    assert set(res["deleted_ids"]) == {1, 2}
    assert res["total_deleted"] == 2
    assert 296180420 not in api.delete_calls
    assert 296180421 not in api.delete_calls
    # Synthetics skipped, not errored.
    assert res["total_skipped"] == 2
    assert set(res["skipped_synthetics_ids"]) == {296180420, 296180421}
    assert res["total_errors"] == 0


def test_teardown_defensively_skips_untyped_synthetics_on_400():
    """If the list payload omits `type`, the delete 400s — treat as a skip."""
    monitors = [
        {"id": 1, "name": "[Healthcare] real", "type": "query alert", "tags": _TAGS},
        {"id": 999, "name": "synthetics no-type", "tags": _TAGS, "_synthetic_no_type": True},
    ]
    api = _FakeAPI(monitors)

    res = MonitorManager().teardown(api, vertical_name="healthcare")

    assert res["deleted_ids"] == [1]
    assert res["total_deleted"] == 1
    assert res["total_skipped"] == 1
    assert res["skipped_synthetics_ids"] == [999]
    assert res["total_errors"] == 0  # the 400 must NOT count as a teardown error


def test_teardown_leaves_untagged_customer_monitors_untouched():
    monitors = [
        {"id": 1, "name": "[Healthcare] toolkit", "type": "query alert", "tags": _TAGS},
        {"id": 50, "name": "customer monitor", "type": "query alert", "tags": ["team:x"]},
    ]
    api = _FakeAPI(monitors)

    res = MonitorManager().teardown(api, vertical_name="healthcare")

    assert 50 not in api.delete_calls
    assert res["deleted_ids"] == [1]
    assert res["total_errors"] == 0
