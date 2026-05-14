"""
Datadog credential validator for the UI's "Test connection" button.

Hits the public `/api/v1/validate` endpoint (free, fast, accepts only
the API key — not the app key) plus `/api/v1/api_key` (requires both
api+app keys, so doubles as an app-key sanity check).

Kept deliberately small and dependency-light: no async (we run it from
a FastAPI endpoint that's tiny, no point making it async-only), no
retries (the user is sitting in front of the screen — they'll click
again if the network blipped).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import requests

# Reuse the toolkit's authoritative site→base-url map. Importing from
# dd_api keeps the UI in sync if the toolkit ever adds a new site
# (e.g. ap2) without us having to remember to update a second list.
from dd_demo_toolkit.utils.dd_api import DatadogAPIClient


@dataclass
class ValidationResult:
    """Result of a credential validation attempt."""
    ok: bool
    error: Optional[str] = None
    # When ok=True, the API key validity is confirmed. ``app_key_ok`` is
    # True only if the app-key check also passed. We separate the two so
    # the UI can surface "API key valid but app key wrong" — that's the
    # common SE failure mode (they pasted the API key into both fields).
    api_key_ok: bool = False
    app_key_ok: bool = False


def validate_credentials(
    api_key: str,
    app_key: str,
    site: str,
    *,
    timeout: float = 5.0,
) -> ValidationResult:
    """Validate that the given credentials work against the given site.

    Returns a ValidationResult with `ok=True` only if BOTH keys verify.
    """
    if not api_key:
        return ValidationResult(ok=False, error="DD_API_KEY is empty.")
    if not app_key:
        return ValidationResult(ok=False, error="DD_APP_KEY is empty.")
    if site not in DatadogAPIClient.SITE_MAPPING:
        return ValidationResult(
            ok=False,
            error=(
                f"Unknown DD_SITE: {site}. "
                f"Valid sites: {sorted(DatadogAPIClient.SITE_MAPPING)}"
            ),
        )

    base_url = DatadogAPIClient.SITE_MAPPING[site]

    # Step 1: API-key-only check via /api/v1/validate.
    try:
        r1 = requests.get(
            f"{base_url}/api/v1/validate",
            headers={"DD-API-KEY": api_key},
            timeout=timeout,
        )
    except requests.RequestException as e:
        return ValidationResult(
            ok=False,
            error=f"Could not reach {base_url}: {type(e).__name__}: {e}",
        )

    if r1.status_code == 403:
        return ValidationResult(ok=False, api_key_ok=False, error="API key rejected by Datadog (403).")
    if r1.status_code >= 400:
        return ValidationResult(
            ok=False,
            error=f"API-key validation returned HTTP {r1.status_code}: {r1.text[:200]}",
        )

    # Step 2: combined API+APP key check. The /api/v1/api_key endpoint
    # requires both keys; if the app key is wrong we'll see a 403 here
    # while step 1 succeeded.
    try:
        r2 = requests.get(
            f"{base_url}/api/v1/api_key",
            headers={
                "DD-API-KEY": api_key,
                "DD-APPLICATION-KEY": app_key,
            },
            timeout=timeout,
        )
    except requests.RequestException as e:
        return ValidationResult(
            ok=False,
            api_key_ok=True,
            error=f"Could not reach {base_url} for app-key check: {type(e).__name__}: {e}",
        )

    if r2.status_code == 403:
        return ValidationResult(
            ok=False,
            api_key_ok=True,
            app_key_ok=False,
            error="API key is valid, but APP key was rejected (403). "
                  "Check that DD_APP_KEY is an Application Key, not a second API Key.",
        )
    if r2.status_code >= 400:
        return ValidationResult(
            ok=False,
            api_key_ok=True,
            error=f"APP-key validation returned HTTP {r2.status_code}: {r2.text[:200]}",
        )

    return ValidationResult(ok=True, api_key_ok=True, app_key_ok=True)
