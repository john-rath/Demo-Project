"""Provision the RUM browser application — turnkey, 1Password-native.

Runs on the HOST (needs the `op` CLI + write access to `.env`), wrapped in
`op run` so DD_API_KEY/DD_APP_KEY are resolved for the Datadog API call.

What it does (only when `rum` is in DD_DEMO_PRODUCTS), idempotently:
  1. Find-or-create the RUM application via the Datadog API.
  2. Store the client token as a field in the SAME 1Password item your
     DD_API_KEY already references (vault/item auto-derived from the op:// ref).
  3. Write `.env`: DD_RUM_APPLICATION_ID (not a secret) and
     DD_CLIENT_TOKEN=op://<vault>/<item>/rum-client-token (a reference, which
     env_manager accepts). `op run` resolves the ref into the care-portal
     container at `make up` — no plain secret on disk, no manual paste.

Designed to never break `make ui`: any recoverable problem warns and exits 0.
Run directly via `make rum-provision`, or automatically as part of `make ui`.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import requests

from dd_demo_toolkit_ui import env_manager

ENV_PATH = Path(os.getenv("DD_DEMO_ENV_PATH", ".env")).resolve()
RUM_APP_NAME = os.getenv("RUM_APP_NAME", "Care Experience Portal")
TOKEN_FIELD = "rum-client-token"


def _warn_exit(msg: str) -> None:
    """Print a notice and exit 0 so the calling `make ui` flow never breaks."""
    print(f"  [rum-provision] {msg}")
    sys.exit(0)


def main() -> None:
    if not ENV_PATH.exists():
        _warn_exit(f".env not found at {ENV_PATH}; skipping RUM provisioning.")

    on_disk = env_manager.read_env(ENV_PATH, mask=False)

    # 1. Gate on the product picker selection.
    products = [p.strip() for p in (on_disk.get("DD_DEMO_PRODUCTS") or "").split(",") if p.strip()]
    if "rum" not in products:
        _warn_exit("'rum' not selected in DD_DEMO_PRODUCTS; skipping.")

    # 2. Idempotency: already provisioned?
    if on_disk.get("DD_RUM_APPLICATION_ID") and (on_disk.get("DD_CLIENT_TOKEN") or "").startswith("op://"):
        _warn_exit("RUM already provisioned (app id + op:// token reference present); skipping.")

    # 3. Derive the 1Password vault/item from the existing DD_API_KEY reference.
    api_ref = on_disk.get("DD_API_KEY", "")
    m = re.match(r"op://([^/]+)/([^/]+)/", api_ref)
    if not m:
        _warn_exit(
            "DD_API_KEY is not an op:// reference, so the vault/item can't be "
            "auto-derived. Set DD_RUM_APPLICATION_ID / DD_CLIENT_TOKEN manually "
            "or run `make rum-create`."
        )
    vault, item = m.group(1), m.group(2)

    # 4. Resolve API keys (op run provides these in the environment).
    api_key = os.getenv("DD_API_KEY", "")
    app_key = os.getenv("DD_APP_KEY", "")
    if not api_key or api_key.startswith("op://") or not app_key or app_key.startswith("op://"):
        _warn_exit("DD_API_KEY/DD_APP_KEY not resolved — run via `make rum-provision` (op run).")

    site = os.getenv("DD_SITE", "datadoghq.com")
    base = f"https://api.{site}"
    headers = {"DD-API-KEY": api_key, "DD-APPLICATION-KEY": app_key, "Content-Type": "application/json"}

    # 5. Find-or-create the RUM application.
    try:
        listing = requests.get(f"{base}/api/v2/rum/applications", headers=headers, timeout=30)
        listing.raise_for_status()
        existing = next(
            (d for d in listing.json().get("data", [])
             if d.get("attributes", {}).get("name") == RUM_APP_NAME),
            None,
        )
        if existing:
            app_pub_id = existing["id"]
            detail = requests.get(f"{base}/api/v2/rum/applications/{app_pub_id}",
                                  headers=headers, timeout=30).json()["data"]["attributes"]
            app_id = detail.get("application_id") or app_pub_id
            token = detail.get("client_token", "")
            action = "reused"
        else:
            body = {"data": {"type": "rum_application",
                             "attributes": {"name": RUM_APP_NAME, "type": "browser"}}}
            created = requests.post(f"{base}/api/v2/rum/applications", headers=headers,
                                    json=body, timeout=30)
            created.raise_for_status()
            attrs = created.json()["data"]["attributes"]
            app_id = attrs.get("application_id") or created.json()["data"]["id"]
            token = attrs.get("client_token", "")
            action = "created"
    except requests.RequestException as e:
        _warn_exit(f"Datadog RUM API call failed: {e}")

    if not token or not app_id:
        _warn_exit("RUM app has no application_id/client_token in the API response; skipping.")

    # 6. Store the client token in 1Password (creates/updates the field).
    try:
        subprocess.run(
            ["op", "item", "edit", item, "--vault", vault, f"{TOKEN_FIELD}[password]={token}"],
            check=True, capture_output=True, text=True,
        )
    except FileNotFoundError:
        _warn_exit("`op` CLI not found on PATH; cannot store the token. Run `make rum-create` instead.")
    except subprocess.CalledProcessError as e:
        _warn_exit(f"`op item edit` failed (vault={vault}, item={item}): {e.stderr.strip()}")

    # 7. Write the app id (plain, non-secret) + the token REFERENCE to .env.
    ref = f"op://{vault}/{item}/{TOKEN_FIELD}"
    try:
        env_manager.write_env(ENV_PATH, {
            "DD_RUM_APPLICATION_ID": app_id,
            "DD_CLIENT_TOKEN": ref,
        })
    except (ValueError, env_manager.PlainSecretRejected) as e:
        _warn_exit(f"failed to write .env: {e}")

    print(f"  [rum-provision] RUM app {action}: {RUM_APP_NAME!r}")
    print(f"  [rum-provision]   DD_RUM_APPLICATION_ID={app_id}")
    print(f"  [rum-provision]   DD_CLIENT_TOKEN={ref}  (token stored in 1Password: {vault}/{item})")
    print("  [rum-provision] care-portal will pick these up on the next `make up`.")


if __name__ == "__main__":
    main()
