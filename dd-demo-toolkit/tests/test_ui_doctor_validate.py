"""
Tests for the UI front-door endpoints: GET /api/doctor and GET /api/validate.

These are what make the doctor + validate engines usable from `make ui`
without the terminal. Exercised via FastAPI's TestClient against the real
`verticals/` tree (credential-free, offline).
"""

from pathlib import Path

from fastapi.testclient import TestClient

from dd_demo_toolkit_ui.server import UIConfig, build_app

REPO = Path(__file__).parent.parent
VERTICALS = REPO / "verticals"
STATIC = REPO / "dd_demo_toolkit_ui" / "static"


def _client(tmp_path, env_body="DD_DEMO_VERTICAL=healthcare\n") -> TestClient:
    env = tmp_path / ".env"
    env.write_text(env_body)
    cfg = UIConfig(
        verticals_dir=VERTICALS,
        env_path=env,
        project_dir=tmp_path,
        static_dir=STATIC,
        require_gitignore=False,
        enable_supervisor=False,
    )
    return TestClient(build_app(cfg))


def test_doctor_endpoint_shape(tmp_path):
    c = _client(tmp_path)
    r = c.get("/api/doctor?quick=true")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body["checks"], list) and body["checks"]
    assert "blocking" in body
    names = {ch["name"] for ch in body["checks"]}
    # The asset-lint and vertical checks always run; the port check is omitted
    # in-UI (the UI owns the port).
    assert "Assets validate" in names
    assert "Vertical selected" in names
    assert not any("port" in n.lower() for n in names)
    # quick=true skips the network credential check.
    cred = next(ch for ch in body["checks"] if ch["name"].startswith("Datadog credentials"))
    assert cred["skipped"] is True


def test_validate_endpoint_defaults_to_env_vertical(tmp_path):
    c = _client(tmp_path)
    r = c.get("/api/validate")
    assert r.status_code == 200
    body = r.json()
    assert body["vertical"] == "healthcare"
    assert body["error"] is None
    # Healthcare base is clean of deploy-blocking (ERROR) findings.
    assert body["summary"]["errors"] == 0
    assert isinstance(body["findings"], list)


def test_validate_endpoint_explicit_vertical_and_overlay(tmp_path):
    c = _client(tmp_path)
    r = c.get("/api/validate?vertical=healthcare&sub_vertical=adventhealth")
    assert r.status_code == 200
    body = r.json()
    assert body["vertical"] == "healthcare"
    assert body["sub_vertical"] == "adventhealth"
    assert body["summary"]["errors"] == 0  # overlay namespace issues are warnings, not errors


def test_validate_endpoint_no_vertical_selected(tmp_path):
    c = _client(tmp_path, env_body="DD_SITE=datadoghq.com\n")
    r = c.get("/api/validate")
    assert r.status_code == 200
    body = r.json()
    assert body["vertical"] is None
    assert "no vertical" in (body["error"] or "")
