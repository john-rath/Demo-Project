"""Create / delete Datadog Synthetic tests that drive the Sensing Hospital app.

Because the mock app runs locally, the tests are assigned to a Datadog
Synthetics **private location** — a worker container on the compose network
executes them against the in-network service DNS names. The browser test loads
the RUM-instrumented care-portal (populating RUM); the API tests exercise the
read path and a health endpoint.

Usage (wrapped by `make synthetics-create` / `make synthetics-delete`, which
run it under `op run` so the keys resolve):

    python manage_synthetics.py create
    python manage_synthetics.py delete

Env:
    DD_API_KEY, DD_APP_KEY, DD_SITE                  — Datadog auth
    DD_SYNTHETICS_PRIVATE_LOCATION_ID                — e.g. "pl:abc123..."
    SYNTH_PORTAL_URL   (default http://care-portal:8080)
    SYNTH_SUMMARY_URL  (default http://care-summary-api:8080/summary?device_id=rtls_badge-000)
"""
from __future__ import annotations

import os
import sys

import requests

SITE = os.getenv("DD_SITE", "datadoghq.com")
API_BASE = f"https://api.{SITE}"
API_KEY = os.getenv("DD_API_KEY", "")
APP_KEY = os.getenv("DD_APP_KEY", "")
PL_ID = os.getenv("DD_SYNTHETICS_PRIVATE_LOCATION_ID", "")
PORTAL_URL = os.getenv("SYNTH_PORTAL_URL", "http://care-portal:8080")
SUMMARY_URL = os.getenv("SYNTH_SUMMARY_URL", "http://care-summary-api:8080/summary?device_id=rtls_badge-000")

TAGS = ["dd-demo-toolkit:true", "vertical:healthcare", "incident_domain:care-experience", "app:sensing-hospital"]
HEADERS = {"DD-API-KEY": API_KEY, "DD-APPLICATION-KEY": APP_KEY, "Content-Type": "application/json"}


def _require_env():
    missing = [k for k, v in {"DD_API_KEY": API_KEY, "DD_APP_KEY": APP_KEY,
                              "DD_SYNTHETICS_PRIVATE_LOCATION_ID": PL_ID}.items() if not v]
    if missing:
        sys.exit(f"missing required env: {', '.join(missing)} (create a private location in Datadog first)")


def _api_test_payload() -> dict:
    return {
        "name": "[Sensing Hospital] care-summary-api read path",
        "type": "api", "subtype": "http",
        "config": {
            "request": {"method": "GET", "url": SUMMARY_URL, "timeout": 30},
            "assertions": [
                {"type": "statusCode", "operator": "is", "target": 200},
                {"type": "responseTime", "operator": "lessThan", "target": 5000},
            ],
        },
        "locations": [PL_ID],
        "options": {"tick_every": 60, "min_failure_duration": 0, "min_location_failed": 1},
        "message": "Sensing Hospital care-summary-api degraded. @to-care-experience-oncall",
        "tags": TAGS,
        "status": "live",
    }


def _browser_test_payload() -> dict:
    return {
        "name": "[Sensing Hospital] Care Portal (RUM traffic)",
        "type": "browser",
        "config": {
            "request": {"method": "GET", "url": PORTAL_URL},
            "assertions": [],
            "setCookie": "",
        },
        "locations": [PL_ID],
        # Every 5 min generates a steady RUM session + linked APM trace.
        "options": {"tick_every": 300, "min_failure_duration": 0, "min_location_failed": 1,
                    "device_ids": ["laptop_large"]},
        "message": "Sensing Hospital care portal browser test failing. @to-care-experience-oncall",
        "tags": TAGS,
        "status": "live",
        "steps": [],
    }


def create():
    _require_env()
    for kind, url, payload in [
        ("api", f"{API_BASE}/api/v1/synthetics/tests/api", _api_test_payload()),
        ("browser", f"{API_BASE}/api/v1/synthetics/tests/browser", _browser_test_payload()),
    ]:
        r = requests.post(url, headers=HEADERS, json=payload, timeout=30)
        if r.status_code >= 300:
            print(f"  ✗ {kind} test create failed [{r.status_code}]: {r.text[:300]}")
        else:
            print(f"  ✓ {kind} test created: {r.json().get('public_id')}")


def delete():
    if not (API_KEY and APP_KEY):
        sys.exit("missing DD_API_KEY / DD_APP_KEY")
    r = requests.get(f"{API_BASE}/api/v1/synthetics/tests", headers=HEADERS, timeout=30)
    r.raise_for_status()
    ids = [t["public_id"] for t in r.json().get("tests", [])
           if "dd-demo-toolkit:true" in (t.get("tags") or []) and "app:sensing-hospital" in (t.get("tags") or [])]
    if not ids:
        print("  no sensing-hospital synthetic tests to delete")
        return
    d = requests.post(f"{API_BASE}/api/v1/synthetics/tests/delete", headers=HEADERS,
                      json={"public_ids": ids}, timeout=30)
    d.raise_for_status()
    print(f"  ✓ deleted {len(ids)} synthetic test(s)")


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "create"
    {"create": create, "delete": delete}.get(action, lambda: sys.exit(f"unknown action {action}"))()
