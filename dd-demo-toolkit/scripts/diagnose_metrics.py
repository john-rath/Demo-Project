#!/usr/bin/env python3
"""
Diagnostic script: check whether specific metrics exist in Datadog.
Uses only Python stdlib — no pip dependencies required.

Usage:
    export $(grep -v '^#' .env | grep '=' | xargs) 2>/dev/null
    python3 scripts/diagnose_metrics.py
"""

import json
import os
import sys
import time
import urllib.request
import urllib.error
import urllib.parse

# --- Config ---
DD_API_KEY = os.getenv("DD_API_KEY")
DD_APP_KEY = os.getenv("DD_APP_KEY")
DD_SITE = os.getenv("DD_SITE", "datadoghq.com")

SITE_MAP = {
    "datadoghq.com": "https://api.datadoghq.com",
    "us3.datadoghq.com": "https://api.us3.datadoghq.com",
    "us5.datadoghq.com": "https://api.us5.datadoghq.com",
    "datadoghq.eu": "https://api.datadoghq.eu",
    "ap1.datadoghq.com": "https://api.ap1.datadoghq.com",
    "ddog-gov.com": "https://api.ddog-gov.com",
}

BASE_URL = SITE_MAP.get(DD_SITE, f"https://api.{DD_SITE}")

# The 3 failing metrics + known-working controls
METRICS_TO_CHECK = [
    "hospital.device.battery_pct",
    "hospital.env.room_temperature_f",
    "hospital.app.errors_total",
    "hospital.pump.occlusion_alerts_total",
    "hospital.nursecall.response_time_sec",
    "hospital.env.ups_runtime_min",
]

SEARCH_PREFIXES = [
    "hospital.pump",
    "hospital.nursecall",
    "hospital.env.ups",
]

TEST_MONITORS = [
    {
        "name": "[DIAG] Pump Occlusion Test",
        "query": "sum(last_5m):sum:hospital.pump.occlusion_alerts_total{} > 10",
        "threshold": 10,
    },
    {
        "name": "[DIAG] Nurse Call Test",
        "query": "avg(last_5m):avg:hospital.nursecall.response_time_sec{} > 180",
        "threshold": 180,
    },
    {
        "name": "[DIAG] UPS Runtime Test",
        "query": "min(last_5m):min:hospital.env.ups_runtime_min{} < 15",
        "threshold": 15,
    },
]


def dd_request(method, path, body=None):
    """Make a Datadog API request using stdlib only."""
    url = f"{BASE_URL}{path}"
    headers = {
        "DD-API-KEY": DD_API_KEY,
        "DD-APPLICATION-KEY": DD_APP_KEY,
        "Content-Type": "application/json",
    }
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        error_body = e.read().decode()
        return {"_error": True, "_status": e.code, "_body": error_body}


def main():
    if not DD_API_KEY or not DD_APP_KEY:
        print("ERROR: DD_API_KEY and DD_APP_KEY must be set.")
        print("Run:  export $(grep -v '^#' .env | grep '=' | xargs) 2>/dev/null")
        sys.exit(1)

    print(f"Datadog site: {DD_SITE} ({BASE_URL})\n")

    # --- STEP 1: Exact metric search ---
    print("=" * 70)
    print("STEP 1: Search for each metric by exact name")
    print("=" * 70)
    for name in METRICS_TO_CHECK:
        resp = dd_request("GET", f"/api/v1/search?q=metrics:{name}")
        if resp.get("_error"):
            print(f"  ERROR    {name}: {resp['_status']} {resp['_body'][:200]}")
            continue
        results = resp.get("results", {}).get("metrics", [])
        found = name in results
        status = "FOUND \u2713" if found else "NOT FOUND \u2717"
        print(f"  {status}  {name}")
        if results and not found:
            print(f"           Partial matches: {results[:5]}")

    # --- STEP 2: Prefix search ---
    print()
    print("=" * 70)
    print("STEP 2: Search by prefix (catch name transformations)")
    print("=" * 70)
    for prefix in SEARCH_PREFIXES:
        resp = dd_request("GET", f"/api/v1/search?q=metrics:{prefix}")
        if resp.get("_error"):
            print(f"  ERROR for '{prefix}': {resp['_status']} {resp['_body'][:200]}")
            continue
        results = resp.get("results", {}).get("metrics", [])
        print(f"\n  Prefix '{prefix}' \u2192 {len(results)} metric(s):")
        for m in sorted(results):
            print(f"    - {m}")
        if not results:
            print("    (none)")

    # --- STEP 3: Try creating monitors ---
    print()
    print("=" * 70)
    print("STEP 3: Attempt monitor creation (capture raw API error)")
    print("=" * 70)

    created_ids = []
    for mon in TEST_MONITORS:
        payload = {
            "name": mon["name"],
            "type": "query alert",
            "query": mon["query"],
            "message": "Diagnostic test - will be deleted immediately",
            "tags": ["dd-demo-toolkit:diagnostic"],
            "options": {"thresholds": {"critical": mon["threshold"]}},
        }
        resp = dd_request("POST", "/api/v1/monitor", body=payload)
        if resp.get("_error"):
            print(f"\n  FAILED   {mon['name']}")
            print(f"           Status: {resp['_status']}")
            print(f"           Body:   {resp['_body'][:500]}")
        else:
            mid = resp.get("id")
            print(f"\n  SUCCESS  {mon['name']}  (id={mid})")
            if mid:
                created_ids.append(mid)

    # Cleanup
    if created_ids:
        print(f"\n  Cleaning up {len(created_ids)} diagnostic monitor(s)...")
        for mid in created_ids:
            resp = dd_request("DELETE", f"/api/v1/monitor/{mid}")
            if resp.get("_error"):
                print(f"    Failed to delete {mid} - delete manually")
            else:
                print(f"    Deleted {mid}")

    print()
    print("=" * 70)
    print("DIAGNOSIS COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    main()
