"""Mock AdventHealth "Sensing Hospital" cloud billing for Cloud Cost Management.

Generates FOCUS-format cost records (the FinOps Open Cost & Usage Spec that
Datadog CCM ingests as Custom Costs) so the demo can show "what the AI care
platform costs to run" — per service and team, with the AI Care Companion
inference spend broken out.

  generate  -> writes ccm/focus_costs.csv (upload via CCM UI → Custom Costs)
  upload    -> best-effort POST to /api/v2/cost/custom_costs (FOCUS JSON)

CCM is the cherry-on-top, so the CSV is the reliable artifact; the API upload
is best-effort (the exact request wrapper differs by account/version — the
script prints the response so you can confirm/adjust). Run via `make
ccm-generate` / `make ccm-upload` (upload wrapped in op run for the keys).
"""
from __future__ import annotations

import csv
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import requests

OUT = Path(os.getenv("CCM_CSV", str(Path(__file__).parent / "focus_costs.csv")))
CURRENCY = "USD"
PROVIDER = "AdventHealth Cloud"

# Monthly billed cost per platform service (FOCUS ServiceName) + team owner.
# The AI Care Companion inference line is the headline for the AI cost story.
SERVICES = [
    ("ai-care-companion",          "AI/ML",            "digital-health",   4200.0),
    ("care-experience-platform",   "Compute",          "digital-health",   1850.0),
    ("care-summary-api",           "Compute",          "digital-health",    640.0),
    ("rtls-location-service",      "Compute",          "biomed",            520.0),
    ("clinical-alerts-service",    "Compute",          "clinical-systems",  430.0),
    ("sensing-postgres",           "Database",         "digital-health",    1120.0),
    ("sensing-redis",              "Cache",            "digital-health",     310.0),
    ("rum-care-portal",            "Digital Experience","digital-health",    280.0),
    ("datadog-observability",      "Observability",    "platform",          900.0),
]

# FOCUS column subset Datadog CCM uses for custom costs.
COLUMNS = [
    "BillingAccountId", "BillingAccountName", "BillingPeriodStart", "BillingPeriodEnd",
    "ChargePeriodStart", "ChargePeriodEnd", "BilledCost", "EffectiveCost",
    "BillingCurrency", "ProviderName", "ServiceName", "ServiceCategory",
    "ChargeCategory", "ChargeDescription", "Tags",
]


def _periods(months_back: int = 3):
    today = date.today().replace(day=1)
    for i in range(months_back, 0, -1):
        start = (today - timedelta(days=1)).replace(day=1)
        for _ in range(i - 1):
            start = (start - timedelta(days=1)).replace(day=1)
        # next month start
        end = (start.replace(day=28) + timedelta(days=7)).replace(day=1)
        yield start, end


def _rows():
    for start, end in _periods():
        # mild month-over-month growth on the AI line to show a trend
        for name, category, team, base in SERVICES:
            cost = round(base * (1.0 + 0.06 * (start.month % 3)), 2)
            yield {
                "BillingAccountId": "adventhealth-sensing-hospital",
                "BillingAccountName": "AdventHealth Sensing Hospital",
                "BillingPeriodStart": start.isoformat(),
                "BillingPeriodEnd": end.isoformat(),
                "ChargePeriodStart": start.isoformat(),
                "ChargePeriodEnd": end.isoformat(),
                "BilledCost": cost,
                "EffectiveCost": cost,
                "BillingCurrency": CURRENCY,
                "ProviderName": PROVIDER,
                "ServiceName": name,
                "ServiceCategory": category,
                "ChargeCategory": "Usage",
                "ChargeDescription": f"{name} monthly run cost",
                "Tags": f"team:{team},vertical:healthcare,dd-demo-toolkit:true",
            }


def generate():
    rows = list(_rows())
    with OUT.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)
    print(f"  ✓ wrote {len(rows)} FOCUS cost rows → {OUT}")
    print("  Upload in Datadog: Cloud Cost → Custom Costs → Upload (FOCUS CSV).")


def upload():
    api, app = os.getenv("DD_API_KEY", ""), os.getenv("DD_APP_KEY", "")
    if not api or api.startswith("op://") or not app or app.startswith("op://"):
        sys.exit("DD_API_KEY/DD_APP_KEY not resolved — run via `make ccm-upload` (op run).")
    base = f"https://api.{os.getenv('DD_SITE', 'datadoghq.com')}"
    headers = {"DD-API-KEY": api, "DD-APPLICATION-KEY": app, "Content-Type": "application/json"}
    rows = list(_rows())
    payload = {"data": {"type": "custom_costs", "attributes": {"costs": rows}}}
    r = requests.post(f"{base}/api/v2/cost/custom_costs", headers=headers, json=payload, timeout=60)
    print(f"  POST /api/v2/cost/custom_costs → {r.status_code}")
    print("  body:", r.text[:600])
    if r.status_code >= 300:
        print("  (If the wrapper is rejected, upload focus_costs.csv via the CCM UI instead.)")


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "generate"
    {"generate": generate, "upload": upload}.get(action, lambda: sys.exit(f"unknown action {action}"))()
