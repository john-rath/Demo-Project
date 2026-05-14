"""
Tests for the FastAPI app built by dd_demo_toolkit_ui.server.build_app.

Uses FastAPI's TestClient — no network, no uvicorn — so the suite is
fast (<1s) and deterministic. The dd_validator endpoint is exercised
separately in test_ui_dd_validator.py with a mocked requests.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dd_demo_toolkit_ui import env_manager as em
from dd_demo_toolkit_ui.server import UIConfig, build_app


# ----- Fixtures -------------------------------------------------------------


@pytest.fixture
def verticals_dir(tmp_path: Path) -> Path:
    """Build a minimal verticals tree the ConfigLoader will accept.

    Two verticals; one has an overlay; both have valid (if minimal) config.yaml.
    Mirrors the real toolkit layout so we exercise the same code paths.
    """
    vdir = tmp_path / "verticals"
    # Minimum config the ConfigLoader will accept: non-empty device_categories,
    # each with a `devices` list. Empty services list is fine.
    minimal_body = (
        "locations:\n"
        "  dimensions: {}\n"
        "device_categories:\n"
        "  widgets:\n"
        "    devices: []\n"
        "services: []\n"
    )
    # vertical: alpha
    alpha = vdir / "alpha"
    alpha.mkdir(parents=True)
    (alpha / "config.yaml").write_text(
        "vertical:\n"
        "  name: alpha\n"
        "  display_name: Alpha Demo\n"
        "  env_prefix: alpha\n"
        + minimal_body
    )
    # vertical: beta + overlay 'extra'
    beta = vdir / "beta"
    beta.mkdir()
    (beta / "config.yaml").write_text(
        "vertical:\n"
        "  name: beta\n"
        "  display_name: Beta Demo\n"
        "  env_prefix: beta\n"
        + minimal_body
    )
    overlays = beta / "overlays"
    overlays.mkdir()
    (overlays / "extra.yaml").write_text("# overlay placeholder\n")
    return vdir


@pytest.fixture
def app_and_paths(tmp_path: Path, verticals_dir: Path):
    """Build the app pointed at the temp verticals tree.

    gitignore guard is off because we're in a tmp dir with no repo.
    static dir doesn't exist — we don't want StaticFiles trying to mount it
    in unit tests; the build_app call falls through to the fallback branch
    and we don't hit `/` in the assertions.
    """
    env_path = tmp_path / ".env"
    cfg = UIConfig(
        verticals_dir=verticals_dir,
        env_path=env_path,
        static_dir=tmp_path / "nonexistent-static",
        require_gitignore=False,
    )
    app = build_app(cfg)
    return app, cfg


@pytest.fixture
def client(app_and_paths) -> TestClient:
    app, _ = app_and_paths
    return TestClient(app)


# ----- /api/health ----------------------------------------------------------


def test_health_returns_ok_and_paths(client, app_and_paths):
    _, cfg = app_and_paths
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["env_path"] == str(cfg.env_path)
    assert body["env_exists"] is False


# ----- /api/sites -----------------------------------------------------------


def test_sites_includes_us_and_eu(client):
    r = client.get("/api/sites")
    assert r.status_code == 200
    sites = r.json()
    assert "datadoghq.com" in sites
    assert "datadoghq.eu" in sites
    # Sorted contract.
    assert sites == sorted(sites)


# ----- /api/verticals -------------------------------------------------------


def test_list_verticals_returns_display_names_and_overlays(client):
    r = client.get("/api/verticals")
    assert r.status_code == 200
    body = r.json()
    by_name = {v["name"]: v for v in body}
    assert set(by_name) == {"alpha", "beta"}
    assert by_name["alpha"]["display_name"] == "Alpha Demo"
    assert by_name["alpha"]["overlays"] == []
    assert by_name["beta"]["display_name"] == "Beta Demo"
    assert by_name["beta"]["overlays"] == ["extra"]


def test_overlays_endpoint_404s_for_unknown_vertical(client):
    r = client.get("/api/verticals/does-not-exist/overlays")
    assert r.status_code == 404


def test_overlays_endpoint_returns_overlay_list(client):
    r = client.get("/api/verticals/beta/overlays")
    assert r.status_code == 200
    assert r.json() == ["extra"]


# ----- /api/env GET ---------------------------------------------------------


def test_get_env_when_missing_returns_empty(client):
    r = client.get("/api/env")
    assert r.status_code == 200
    assert r.json() == {}


def test_get_env_masks_secrets(client, app_and_paths):
    _, cfg = app_and_paths
    cfg.env_path.write_text(
        "DD_API_KEY=topsecret123abcd\n"
        "DD_SITE=datadoghq.com\n"
    )
    r = client.get("/api/env")
    body = r.json()
    assert body["DD_SITE"] == "datadoghq.com"
    assert body["DD_API_KEY"].endswith("abcd")
    assert "topsecret" not in body["DD_API_KEY"]


# ----- /api/env POST --------------------------------------------------------


def test_post_env_writes_new_value(client, app_and_paths):
    _, cfg = app_and_paths
    r = client.post("/api/env", json={
        "DD_SITE": "datadoghq.eu",
        "DD_DEMO_VERTICAL": "beta",
    })
    assert r.status_code == 200
    on_disk = em.read_env(cfg.env_path, mask=False)
    assert on_disk["DD_SITE"] == "datadoghq.eu"
    assert on_disk["DD_DEMO_VERTICAL"] == "beta"


def test_post_env_keep_existing_preserves_secret(client, app_and_paths):
    _, cfg = app_and_paths
    cfg.env_path.write_text("DD_API_KEY=original-secret-zyxw\n")

    r = client.post("/api/env", json={
        "DD_API_KEY": em.KEEP_EXISTING,
        "DD_SITE": "datadoghq.com",
    })
    assert r.status_code == 200
    on_disk = em.read_env(cfg.env_path, mask=False)
    assert on_disk["DD_API_KEY"] == "original-secret-zyxw"
    assert on_disk["DD_SITE"] == "datadoghq.com"


def test_post_env_response_is_masked(client, app_and_paths):
    r = client.post("/api/env", json={"DD_API_KEY": "newkey-abcdefgh"})
    assert r.status_code == 200
    body = r.json()
    assert body["DD_API_KEY"].endswith("efgh")
    assert "newkey" not in body["DD_API_KEY"]


def test_post_env_round_trip_preserves_hand_edited_var(client, app_and_paths):
    """Project plan Risk R10: don't clobber keys the user added by hand."""
    _, cfg = app_and_paths
    cfg.env_path.write_text(
        "MY_CUSTOM=hand-edited-value\n"
        "DD_SITE=datadoghq.com\n"
    )
    r = client.post("/api/env", json={"DD_SITE": "datadoghq.eu"})
    assert r.status_code == 200
    on_disk = em.read_env(cfg.env_path, mask=False)
    assert on_disk["MY_CUSTOM"] == "hand-edited-value"
    assert on_disk["DD_SITE"] == "datadoghq.eu"
