"""
Tests for ProcessSupervisor and the FastAPI process-control endpoints.

We don't spawn docker compose in unit tests — too slow, requires docker
on the test runner, and the supervisor's behavior is independent of what
the child actually does. Instead we monkey-patch PROCESS_DEFS to point
"simulator" / "setup" etc. at small shell commands that emit known lines
and either exit quickly or hang.
"""

from __future__ import annotations

import asyncio
import os
import signal
import sys
from pathlib import Path
from typing import Dict

import pytest
from fastapi.testclient import TestClient

from dd_demo_toolkit_ui import process_supervisor as ps_mod
from dd_demo_toolkit_ui.process_supervisor import (
    AlreadyRunningError,
    EnvNotResolvedError,
    NotRunningError,
    ProcessState,
    ProcessSupervisor,
    UnknownProcessError,
)
from dd_demo_toolkit_ui.server import UIConfig, build_app


# ----- Fixtures: fake PROCESS_DEFS so tests don't shell out to docker ------


def _fake_defs(tmp_path: Path) -> Dict[str, Dict[str, object]]:
    """A dict shaped like PROCESS_DEFS but with cheap shell commands.

    - "simulator" prints lines forever (long-running).
    - "setup" prints two lines then exits 0 (one-shot success).
    - "teardown" prints one line then exits 1 (one-shot failure).
    - "teardown-all" prints to stderr → tests merging.
    """
    return {
        "simulator": {
            "argv": [
                "sh", "-c",
                # Print line, then sleep 60 to simulate a long-running daemon.
                # PYTHONUNBUFFERED-equivalent: flush each echo.
                'echo "sim line 1"; echo "sim line 2"; sleep 60',
            ],
            "stop_signal": signal.SIGTERM,
            "stop_followup_argv": None,
            "long_running": True,
        },
        "setup": {
            "argv": [
                "sh", "-c",
                'echo "deploy: dashboards"; echo "deploy: monitors"; exit 0',
            ],
            "stop_signal": signal.SIGTERM,
            "stop_followup_argv": None,
            "long_running": False,
        },
        "teardown": {
            "argv": [
                "sh", "-c",
                'echo "teardown failed"; exit 1',
            ],
            "stop_signal": signal.SIGTERM,
            "stop_followup_argv": None,
            "long_running": False,
        },
        "teardown-all": {
            "argv": [
                "sh", "-c",
                # stderr should be merged with stdout in the supervisor.
                'echo "to-stderr" >&2; echo "to-stdout"; exit 0',
            ],
            "stop_signal": signal.SIGTERM,
            "stop_followup_argv": None,
            "long_running": False,
        },
    }


@pytest.fixture
def patched_defs(tmp_path: Path, monkeypatch):
    fake = _fake_defs(tmp_path)
    monkeypatch.setattr(ps_mod, "PROCESS_DEFS", fake)
    # Also clear any DD_API_KEY/DD_APP_KEY in os.environ so the
    # _validate_env_resolved check doesn't trip on real op://... leftover
    # from the surrounding shell.
    monkeypatch.delenv("DD_API_KEY", raising=False)
    monkeypatch.delenv("DD_APP_KEY", raising=False)
    return fake


@pytest.fixture
def supervisor(tmp_path: Path, patched_defs):
    return ProcessSupervisor(
        project_dir=tmp_path,
        max_log_lines=100,
        # Short grace so the SIGKILL escalation test runs fast.
        stop_grace_seconds=0.5,
    )


# ----- ProcessSupervisor unit tests ----------------------------------------


def test_names_returns_known_processes(supervisor):
    assert supervisor.names() == ["setup", "simulator", "teardown", "teardown-all"]


def test_unknown_process_raises(supervisor):
    with pytest.raises(UnknownProcessError):
        supervisor.status("does-not-exist")


def test_status_before_start_is_idle(supervisor):
    s = supervisor.status("simulator")
    assert s["state"] == "idle"
    assert s["pid"] is None
    assert s["log_lines_buffered"] == 0


@pytest.mark.asyncio
async def test_start_one_shot_runs_to_completion(supervisor):
    """A one-shot process should transition idle → running → exited(0)."""
    status = await supervisor.start("setup")
    assert status["state"] == "running"
    assert status["pid"] is not None

    # Wait for it to finish. setup's fake command exits ~immediately.
    h = supervisor.handles["setup"]
    assert h.waiter_task is not None
    await asyncio.wait_for(h.waiter_task, timeout=5.0)

    final = supervisor.status("setup")
    assert final["state"] == "exited"
    assert final["exit_code"] == 0
    # Both fake lines should be in the buffer.
    assert final["log_lines_buffered"] == 2


@pytest.mark.asyncio
async def test_start_captures_nonzero_exit(supervisor):
    await supervisor.start("teardown")
    await asyncio.wait_for(supervisor.handles["teardown"].waiter_task, timeout=5.0)
    s = supervisor.status("teardown")
    assert s["state"] == "exited"
    assert s["exit_code"] == 1
    assert s["last_error"] is not None
    assert "code 1" in s["last_error"]


@pytest.mark.asyncio
async def test_start_merges_stderr_into_stdout_buffer(supervisor):
    await supervisor.start("teardown-all")
    await asyncio.wait_for(
        supervisor.handles["teardown-all"].waiter_task, timeout=5.0
    )
    lines = list(supervisor.handles["teardown-all"].log_buffer)
    # Both stderr ("to-stderr") and stdout ("to-stdout") must be captured.
    assert "to-stderr" in lines
    assert "to-stdout" in lines


@pytest.mark.asyncio
async def test_start_twice_raises_already_running(supervisor):
    await supervisor.start("simulator")
    try:
        with pytest.raises(AlreadyRunningError):
            await supervisor.start("simulator")
    finally:
        await supervisor.stop("simulator")
        # Wait for clean exit so the test doesn't leak a subprocess.
        await asyncio.wait_for(
            supervisor.handles["simulator"].waiter_task, timeout=5.0
        )


@pytest.mark.asyncio
async def test_stop_signals_and_transitions_to_stopping(supervisor):
    await supervisor.start("simulator")
    status = await supervisor.stop("simulator")
    assert status["state"] == "stopping"
    # Wait for the child to actually exit (it was sleeping).
    await asyncio.wait_for(
        supervisor.handles["simulator"].waiter_task, timeout=5.0
    )
    final = supervisor.status("simulator")
    assert final["state"] == "exited"


@pytest.mark.asyncio
async def test_stop_when_not_running_raises(supervisor):
    with pytest.raises(NotRunningError):
        await supervisor.stop("simulator")


@pytest.mark.asyncio
async def test_restart_after_exit_clears_buffer(supervisor):
    """A second start on a previously-exited process should reset the
    log buffer so the UI doesn't show stale lines mixed with new ones."""
    await supervisor.start("setup")
    await asyncio.wait_for(supervisor.handles["setup"].waiter_task, timeout=5.0)
    assert len(supervisor.handles["setup"].log_buffer) == 2

    await supervisor.start("setup")
    # Buffer was cleared on restart; new run hasn't necessarily written yet.
    assert len(supervisor.handles["setup"].log_buffer) <= 2
    await asyncio.wait_for(supervisor.handles["setup"].waiter_task, timeout=5.0)
    assert len(supervisor.handles["setup"].log_buffer) == 2


@pytest.mark.asyncio
async def test_env_not_resolved_blocks_start(supervisor, monkeypatch):
    """If DD_API_KEY is still an op:// literal, starting any process
    must fail fast — otherwise docker compose would happily inject the
    literal into the container and we'd get 403s 30 seconds later."""
    monkeypatch.setenv("DD_API_KEY", "op://Employee/Datadog/api-key")
    with pytest.raises(EnvNotResolvedError) as excinfo:
        await supervisor.start("setup")
    assert "make ui" in str(excinfo.value)


# ----- SSE log subscription -------------------------------------------------


@pytest.mark.asyncio
async def test_subscribe_logs_replays_buffer_and_streams_live(supervisor):
    """A subscriber connecting mid-run should get the backlog first, then
    live lines as they're produced."""
    await supervisor.start("setup")
    # Give the child a moment to produce both lines.
    await asyncio.sleep(0.2)

    lines = []
    async for line in supervisor.subscribe_logs("setup"):
        lines.append(line)
        if len(lines) >= 2:
            break

    # Both fake lines (or at least the deterministic prefix) should appear.
    joined = "\n".join(lines)
    assert "deploy: dashboards" in joined
    assert "deploy: monitors" in joined


@pytest.mark.asyncio
async def test_subscribe_logs_terminates_on_process_exit(supervisor):
    """The async generator must end (not hang forever) when the child
    process exits — otherwise SSE connections would leak."""
    await supervisor.start("setup")

    collected = []
    async def consume():
        async for line in supervisor.subscribe_logs("setup"):
            collected.append(line)

    # Should finish on its own within a few seconds.
    await asyncio.wait_for(consume(), timeout=5.0)
    assert len(collected) >= 2


# ----- Endpoint integration tests (TestClient) ------------------------------


@pytest.fixture
def app_and_client(tmp_path: Path, patched_defs):
    """Build the full FastAPI app with supervisor enabled, pointed at the
    patched PROCESS_DEFS so endpoints don't try to start docker."""
    # Minimum verticals tree so build_app doesn't error.
    vdir = tmp_path / "verticals"
    alpha = vdir / "alpha"
    alpha.mkdir(parents=True)
    (alpha / "config.yaml").write_text(
        "vertical:\n  name: alpha\n  display_name: A\n  env_prefix: a\n"
        "locations:\n  dimensions: {}\n"
        "device_categories:\n  w:\n    devices: []\n"
        "services: []\n"
    )

    cfg = UIConfig(
        verticals_dir=vdir,
        env_path=tmp_path / ".env",
        project_dir=tmp_path,
        static_dir=tmp_path / "no-static",
        require_gitignore=False,
        enable_supervisor=True,
    )
    app = build_app(cfg)
    return app, TestClient(app)


def test_list_processes_returns_idle_status(app_and_client):
    _, client = app_and_client
    r = client.get("/api/processes")
    assert r.status_code == 200
    body = r.json()
    names = {p["name"] for p in body}
    assert names == {"simulator", "setup", "teardown", "teardown-all"}
    assert all(p["state"] == "idle" for p in body)


def test_status_for_unknown_process_404(app_and_client):
    _, client = app_and_client
    r = client.get("/api/processes/nope/status")
    assert r.status_code == 404


def test_start_then_stop_endpoint_flow(app_and_client):
    _, client = app_and_client
    # Start
    r = client.post("/api/processes/setup/start")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["state"] == "running"
    assert body["pid"] is not None

    # Status reflects running (then quickly exited; either is OK for setup).
    r = client.get("/api/processes/setup/status")
    assert r.status_code == 200
    assert r.json()["state"] in ("running", "exited")


def test_start_already_running_returns_409(app_and_client):
    _, client = app_and_client
    r = client.post("/api/processes/simulator/start")
    assert r.status_code == 200
    try:
        r = client.post("/api/processes/simulator/start")
        assert r.status_code == 409
    finally:
        client.post("/api/processes/simulator/stop")


def test_stop_not_running_returns_409(app_and_client):
    _, client = app_and_client
    r = client.post("/api/processes/setup/stop")
    assert r.status_code == 409


def test_supervisor_disabled_returns_503(tmp_path):
    """build_app with enable_supervisor=False → process endpoints 503."""
    vdir = tmp_path / "verticals"
    alpha = vdir / "alpha"
    alpha.mkdir(parents=True)
    (alpha / "config.yaml").write_text(
        "vertical:\n  name: alpha\n  display_name: A\n  env_prefix: a\n"
        "locations:\n  dimensions: {}\n"
        "device_categories:\n  w:\n    devices: []\n"
        "services: []\n"
    )
    cfg = UIConfig(
        verticals_dir=vdir,
        env_path=tmp_path / ".env",
        project_dir=tmp_path,
        static_dir=tmp_path / "no-static",
        require_gitignore=False,
        enable_supervisor=False,
    )
    client = TestClient(build_app(cfg))
    r = client.get("/api/processes")
    assert r.status_code == 503
