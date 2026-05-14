"""
Tests for dd_demo_toolkit_ui.dd_validator.

The validator hits Datadog over HTTPS; we mock `requests.get` so the
suite stays offline-friendly. We're testing the *result mapping*: which
HTTP responses produce which ValidationResult shape — that's where the
UI's user-facing error strings come from.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
import requests

from dd_demo_toolkit_ui.dd_validator import ValidationResult, validate_credentials


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
