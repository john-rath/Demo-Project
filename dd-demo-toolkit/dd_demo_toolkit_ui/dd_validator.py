"""
Datadog credential validator for the UI's "Test connection" button.

Hits the public `/api/v1/validate` endpoint (free, fast, accepts only
the API key — not the app key) plus `/api/v1/api_key` (requires both
api+app keys, so doubles as an app-key sanity check).

Kept deliberately small and dependency-light: no async (we run it from
a FastAPI endpoint that's tiny, no point making it async-only), no
retries (the user is sitting in front of the screen — they'll click
again if the network blipped).

Reference resolution: per corp secret-handling policy, the UI persists
op:// references rather than plain keys. Before calling Datadog we
resolve any reference into its real value via the 1Password CLI (`op
read <ref>`), then discard the plain value once the validation call
returns. The plain value never leaves this module.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from typing import Optional

import requests

# Reuse the toolkit's authoritative site→base-url map. Importing from
# dd_api keeps the UI in sync if the toolkit ever adds a new site
# (e.g. ap2) without us having to remember to update a second list.
from dd_demo_toolkit.utils.dd_api import DatadogAPIClient

from .env_manager import is_secret_reference


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


class ReferenceResolutionError(Exception):
    """Raised when ``resolve_secret_reference`` can't turn an op:// (or
    vault:/keychain://) reference into a real value. The message is
    surfaced verbatim to the UI; keep it actionable.
    """


def resolve_secret_reference(value: str, *, timeout: float = 5.0) -> str:
    """Return the plaintext value for a secret-store reference.

    Plain (non-reference) values are returned unchanged — this function
    is safe to call on every credential input from the UI, whether or not
    it's been migrated to a reference yet.

    Only ``op://`` references are auto-resolved. ``vault:`` and
    ``keychain://`` are recognized but raise ``ReferenceResolutionError``
    with a helpful message — users of those stores wrap their commands
    in their own resolver and shouldn't be using the UI's "Test
    connection" button directly until we add support.

    Raises:
        ReferenceResolutionError: if `op` is missing, not signed in,
            the reference resolves to nothing, or the resolver is not
            implemented for the given scheme.
    """
    if not is_secret_reference(value):
        return value

    if value.startswith("op://"):
        if shutil.which("op") is None:
            raise ReferenceResolutionError(
                "The 1Password CLI (`op`) is not installed. "
                "Install with `brew install --cask 1password-cli`, then "
                "sign in with `eval \"$(op signin)\"`."
            )
        try:
            r = subprocess.run(
                ["op", "read", value],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            raise ReferenceResolutionError(
                f"`op read {value}` timed out after {timeout}s. "
                "Is your 1Password session active?"
            )
        if r.returncode != 0:
            # op writes meaningful diagnostics to stderr — surface them.
            stderr = (r.stderr or "").strip()
            raise ReferenceResolutionError(
                f"`op read {value}` failed: {stderr or 'no stderr output'}"
            )
        resolved = (r.stdout or "").strip()
        if not resolved:
            raise ReferenceResolutionError(
                f"`op read {value}` returned an empty value. "
                "Check that the vault/item/field path is correct."
            )
        return resolved

    # vault: or keychain:// — recognized but not auto-resolved by the UI.
    scheme = value.split(":", 1)[0]
    raise ReferenceResolutionError(
        f"The UI's 'Test connection' button doesn't resolve {scheme} "
        f"references yet. Wrap your commands in your own resolver "
        "(e.g. `vault read ...` or `security find-generic-password ...`) "
        "or use a 1Password (`op://...`) reference instead."
    )


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
