#!/usr/bin/env python3
"""
Introspect existing workflows in a Datadog tenant and print the set of
real `actionId` strings in use, plus a YAML-snippet for updating
`_TYPE_TO_ACTION_ID` in dd_demo_toolkit/resources/workflows.py.

Why this exists: the Datadog Workflow Automation API rejects any payload
that contains an unknown `actionId` (400 "spec is invalid"). The action
catalog is documented unevenly across versions and tenants, so the only
reliable way to discover the IDs accepted by YOUR tenant is to read
back workflows that already work in it. The Datadog blueprint workflows
(visible on the Workflow Automation home page) are a good source.

Quick refresher on the canonical payload shape (per
https://docs.datadoghq.com/api/latest/workflow-automation/):
  - `actionId` follows `com.datadoghq.dd.<resource>.<action>` for
    native Datadog actions, `com.<vendor>.<action>` for integrations.
  - Step connections use `outboundEdges: [{branchName, nextStepName}]`
    — a list of OBJECTS, NOT a list of step-name strings.
  - Steps that target an integration need a `connectionLabel` that
    points at an entry in `spec.connectionEnvs`.

Usage:
    export $(grep -v '^#' .env | grep '=' | xargs) 2>/dev/null
    python3 scripts/introspect_workflow_actions.py
    python3 scripts/introspect_workflow_actions.py --include-blueprint  # also pull blueprint workflows
    python3 scripts/introspect_workflow_actions.py --workflow-id <id>   # dump one workflow's full spec
"""

import argparse
import collections
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request


DD_API_KEY = os.getenv("DD_API_KEY")
DD_APP_KEY = os.getenv("DD_APP_KEY")
DD_SITE = os.getenv("DD_SITE", "datadoghq.com")

SITE_MAP = {
    "datadoghq.com": "https://api.datadoghq.com",
    "datadoghq.eu": "https://api.datadoghq.eu",
    "us3.datadoghq.com": "https://api.us3.datadoghq.com",
    "us5.datadoghq.com": "https://api.us5.datadoghq.com",
    "ap1.datadoghq.com": "https://api.ap1.datadoghq.com",
    "ddog-gov.com": "https://api.ddog-gov.com",
}
API_HOST = SITE_MAP.get(DD_SITE, f"https://api.{DD_SITE}")


def _check_creds() -> None:
    if not DD_API_KEY or not DD_APP_KEY:
        print("ERROR: DD_API_KEY and DD_APP_KEY must be set in env.", file=sys.stderr)
        sys.exit(2)


def _get(path: str) -> dict:
    """GET JSON from the Datadog API."""
    url = API_HOST + path
    req = urllib.request.Request(
        url,
        headers={
            "DD-API-KEY": DD_API_KEY,
            "DD-APPLICATION-KEY": DD_APP_KEY,
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"ERROR: {e.code} {e.reason} on {path}\n{body}", file=sys.stderr)
        sys.exit(1)


def _list_workflows(include_blueprint: bool) -> list:
    """List every workflow the credentials can read (paginated)."""
    out = []
    page = 0
    while True:
        params = {
            "page[number]": str(page),
            "page[size]": "100",
        }
        if include_blueprint:
            params["filter[blueprint]"] = "true"
        path = "/api/v2/workflows?" + urllib.parse.urlencode(params)
        body = _get(path)
        data = body.get("data") or []
        out.extend(data)
        if len(data) < 100:
            break
        page += 1
    return out


def _get_workflow(workflow_id: str) -> dict:
    return _get(f"/api/v2/workflows/{workflow_id}")


def _extract_action_ids(workflow: dict) -> list:
    """Walk a workflow object and collect every step's actionId."""
    spec = (
        workflow.get("attributes", {}).get("spec")
        or workflow.get("spec")
        or workflow.get("data", {}).get("attributes", {}).get("spec", {})
    )
    if not isinstance(spec, dict):
        return []
    return [s.get("actionId") for s in spec.get("steps", []) if s.get("actionId")]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--include-blueprint",
        action="store_true",
        help="Also pull workflows marked as blueprints (Datadog's curated templates).",
    )
    parser.add_argument(
        "--workflow-id",
        help="If set, fetch this one workflow and dump its full spec JSON instead of aggregating.",
    )
    args = parser.parse_args()

    _check_creds()

    if args.workflow_id:
        wf = _get_workflow(args.workflow_id)
        print(json.dumps(wf, indent=2))
        return 0

    workflows = _list_workflows(include_blueprint=args.include_blueprint)
    if not workflows:
        print("No workflows found. Try --include-blueprint to also pull Datadog blueprints.")
        return 0

    print(f"Found {len(workflows)} workflow(s). Aggregating actionId usage...\n")

    histogram = collections.Counter()
    by_workflow = []
    for wf in workflows:
        name = wf.get("attributes", {}).get("name") or wf.get("id") or "?"
        ids = _extract_action_ids(wf)
        by_workflow.append((name, ids))
        histogram.update(ids)

    print("actionId histogram (count -> id):")
    print("-" * 80)
    for action_id, n in histogram.most_common():
        print(f"  {n:4d}  {action_id}")

    print("\nFirst step actionId per workflow (handy for trigger discovery):")
    print("-" * 80)
    for name, ids in by_workflow[:30]:
        first = ids[0] if ids else "(no steps)"
        print(f"  {name[:60]:<60s}  {first}")

    print("\nNext step: pick the actionIds you want from the histogram above")
    print("and paste them into _TYPE_TO_ACTION_ID in dd_demo_toolkit/resources/workflows.py.")
    print("Or set `action_id: <id>` per step in your workflow YAML.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
