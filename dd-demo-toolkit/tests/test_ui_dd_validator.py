"""
Tests for dd_demo_toolkit_ui.dd_validator.

The validator hits Datadog over HTTPS; we mock `requests.get` so the
suite stays offline-friendly. We're testing the *result mapping*: which
HTTP responses produce which ValidationResult shape — that's where the
UI's user-facing error strings come from.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests

from dd_demo_toolkit_ui.dd_validator import (
    ReferenceResolutionError,
    ValidationResult,
    resolve_secret_reference,
    validate_credentials,
)


class _FakeResponse:
    def __init__(self, status_code: int, text: str = ""):
        self.status_code = status_code
        self.text = text


def _patch_get(responses):
    """Patch requests.get to return the given responses in order.

    `responses` is a list of `_FakeResponse` (or exceptions to raise).
    Each call to requests.get pops the next item.
    """
    queue = list(responses)

    def _fake_get(*a, **kw):
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    return patch("dd_demo_toolkit_ui.dd_validator.requests.get", side_effect=_fake_get)


def test_empty_api_key_fails_immediately():
    r = validate_credentials("", "appkey", "datadoghq.com")
    assert r.ok is False
    assert "DD_API_KEY" in r.error


def test_empty_app_key_fails_immediately():
    r = validate_credentials("apikey", "", "datadoghq.com")
    assert r.ok is False
    assert "DD_APP_KEY" in r.error


def test_unknown_site_fails():
    r = validate_credentials("api", "app", "not-a-real-site.com")
    assert r.ok is False
    assert "DD_SITE" in r.error


def test_happy_path():
    with _patch_get([_FakeResponse(200, "{}"), _FakeResponse(200, "{}")]):
        r = validate_credentials("api", "app", "datadoghq.com")
    assert r == ValidationResult(ok=True, api_key_ok=True, app_key_ok=True)


def test_api_key_403_surfaces_as_rejected():
    with _patch_get([_FakeResponse(403, '{"errors":["bad key"]}')]):
        r = validate_credentials("api", "app", "datadoghq.com")
    assert r.ok is False
    assert r.api_key_ok is False
    assert "403" in r.error or "rejected" in r.error.lower()


def test_app_key_403_after_api_key_ok_is_distinguished():
    """The common SE failure: pasted the API key into both fields. We want
    a distinct error message that calls this out."""
    with _patch_get([_FakeResponse(200, "{}"), _FakeResponse(403, "{}")]):
        r = validate_credentials("api", "app", "datadoghq.com")
    assert r.ok is False
    assert r.api_key_ok is True
    assert r.app_key_ok is False
    assert "APP key" in r.error


def test_network_error_returns_helpful_message():
    with _patch_get([requests.ConnectionError("dns blew up")]):
        r = validate_credentials("api", "app", "datadoghq.com")
    assert r.ok is False
    assert "Could not reach" in r.error


# ----- resolve_secret_reference --------------------------------------------


def test_resolve_secret_reference_plain_value_is_pass_through():
    """Non-reference strings are returned unchanged. This is what makes
    the validator work for both pre- and post-migration callers without
    branching at the call site."""
    assert resolve_secret_reference("plainvalue123") == "plainvalue123"
    assert resolve_secret_reference("") == ""


def test_resolve_op_reference_happy_path():
    """`op read <ref>` returns the secret on stdout; we strip and return it."""
    fake = MagicMock(returncode=0, stdout="resolved-secret-1234\n", stderr="")
    with patch("dd_demo_toolkit_ui.dd_validator.shutil.which", return_value="/usr/local/bin/op"), \
         patch("dd_demo_toolkit_ui.dd_validator.subprocess.run", return_value=fake) as p:
        out = resolve_secret_reference("op://Employee/Datadog/api-key")
    assert out == "resolved-secret-1234"
    # Verify the command shape — we want `op read <ref>` exactly, no shell
    # interpolation, with the reference as a single argv element.
    args, kwargs = p.call_args
    assert args[0] == ["op", "read", "op://Employee/Datadog/api-key"]


def test_resolve_op_reference_missing_op_cli():
    with patch("dd_demo_toolkit_ui.dd_validator.shutil.which", return_value=None):
        with pytest.raises(ReferenceResolutionError, match="1Password CLI"):
            resolve_secret_reference("op://Employee/Datadog/api-key")


def test_resolve_op_reference_op_fails():
    fake = MagicMock(returncode=1, stdout="", stderr="not signed in\n")
    with patch("dd_demo_toolkit_ui.dd_validator.shutil.which", return_value="/x/op"), \
         patch("dd_demo_toolkit_ui.dd_validator.subprocess.run", return_value=fake):
        with pytest.raises(ReferenceResolutionError, match="not signed in"):
            resolve_secret_reference("op://Employee/Datadog/api-key")


def test_resolve_op_reference_empty_output():
    """An item with a missing/empty field is a real-world failure mode —
    `op read` returns success with no stdout. Distinct error message
    so users can debug their vault path."""
    fake = MagicMock(returncode=0, stdout="\n", stderr="")
    with patch("dd_demo_toolkit_ui.dd_validator.shutil.which", return_value="/x/op"), \
         patch("dd_demo_toolkit_ui.dd_validator.subprocess.run", return_value=fake):
        with pytest.raises(ReferenceResolutionError, match="empty value"):
            resolve_secret_reference("op://Employee/Datadog/api-key")


def test_resolve_vault_reference_not_implemented():
    """We recognize vault: refs so they round-trip in env_manager, but
    the UI doesn't auto-resolve them — explicit error tells users to
    wrap their own commands instead."""
    with pytest.raises(ReferenceResolutionError, match="vault references"):
        resolve_secret_reference("vault:secret/dd/api_key")


def test_resolve_keychain_reference_not_implemented():
    with pytest.raises(ReferenceResolutionError, match="keychain references"):
        resolve_secret_reference("keychain://dd_api_key")
