"""Create / delete the RUM (browser) application for the care-portal frontend.

RUM shows no data until a RUM *application* exists in Datadog — that's what
mints the applicationId + clientToken the browser SDK needs. This creates one
via the API and prints the two values to set (UI Configure tab RUM fields, or
.env). The app name is generic + env-overridable so it isn't tied to a vertical.

Usage (via `make rum-create` / `make rum-delete`, wrapped in `op run`):
    python manage_rum.py create
    python manage_rum.py delete

Env: DD_API_KEY, DD_APP_KEY, DD_SITE, RUM_APP_NAME (default "Care Experience Portal").
"""
from __future__ import annotations

import os
import sys

import requests

SITE = os.getenv("DD_SITE", "datadoghq.com")
API = f"https://api.{SITE}"
NAME = os.getenv("RUM_APP_NAME", "Care Experience Portal")
HEADERS = {
    "DD-API-KEY": os.getenv("DD_API_KEY", ""),
    "DD-APPLICATION-KEY": os.getenv("DD_APP_KEY", ""),
    "Content-Type": "application/json",
}


def _require_keys():
    if not (HEADERS["DD-API-KEY"] and HEADERS["DD-APPLICATION-KEY"]):
        sys.exit("missing DD_API_KEY / DD_APP_KEY")


def create():
    _require_keys()
    body = {"data": {"type": "rum_application", "attributes": {"name": NAME, "type": "browser"}}}
    r = requests.post(f"{API}/api/v2/rum/applications", headers=HEADERS, json=body, timeout=30)
    if r.status_code >= 300:
        sys.exit(f"create failed [{r.status_code}]: {r.text[:300]}")
    data = r.json()["data"]
    attrs = data.get("attributes", {})
    app_id = attrs.get("application_id") or data.get("id")
    token = attrs.get("client_token", "")
    print(f"  ✓ RUM application created: {NAME!r}")
    print(f"    application_id: {app_id}")
    print(f"    client_token  : {token}")
    print("\n  Set these so the care-portal initializes RUM (UI Configure tab, or .env):")
    print(f"    DD_RUM_APPLICATION_ID={app_id}")
    print(f"    DD_CLIENT_TOKEN={token}   # public-by-design; store as an op:// ref per policy")
    print("  Then recreate the portal: make down-mock-app && make up-mock-app, open http://localhost:8800")


def delete():
    _require_keys()
    r = requests.get(f"{API}/api/v2/rum/applications", headers=HEADERS, timeout=30)
    r.raise_for_status()
    ids = [d["id"] for d in r.json().get("data", [])
           if d.get("attributes", {}).get("name") == NAME]
    if not ids:
        print(f"  no RUM application named {NAME!r} to delete")
        return
    for app_id in ids:
        requests.delete(f"{API}/api/v2/rum/applications/{app_id}", headers=HEADERS, timeout=30)
    print(f"  ✓ deleted {len(ids)} RUM application(s) named {NAME!r}")


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "create"
    {"create": create, "delete": delete}.get(action, lambda: sys.exit(f"unknown action {action}"))()
