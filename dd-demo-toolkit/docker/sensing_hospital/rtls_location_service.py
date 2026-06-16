"""rtls-location-service — ON-PREM edge service, the cascade root cause.

Resolves a device ping to a bed/room location. Holds an in-memory
`poll_rate_per_min`; when a (simulated) firmware config pushes the poll rate
up, the service's resolve latency climbs — this is the root cause that
saturates the cloud care-experience-platform downstream. The remediation
controller clamps the poll rate back via /admin/poll-rate, and latency
recovers — a real detect→repair loop.

Auto-instrumented by dd-trace-py (run via `ddtrace-run uvicorn ...`), so the
real Datadog Agent gets APM traces + logs. DD_SERVICE/DD_ENV/DD_TAGS are set
in docker-compose (deployment:on-prem).
"""
from __future__ import annotations

import logging
import os
import random
import time

from fastapi import FastAPI

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("rtls-location-service")

BASELINE_POLL = float(os.getenv("RTLS_BASELINE_POLL_PER_MIN", "12"))
# When True, the service self-drives a slow poll-rate climb so the demo shows
# a cascade without manual curling. Remediation still clamps it.
AUTO_CASCADE = os.getenv("RTLS_AUTO_CASCADE", "false").lower() == "true"

app = FastAPI()
_state = {"poll_rate_per_min": BASELINE_POLL, "started": time.time()}


def _resolve_latency_ms() -> float:
    # Latency is flat near baseline poll, then climbs steeply as the poll rate
    # saturates the on-prem location index. ~45ms at 12/min → ~1.8s at 190/min.
    poll = _state["poll_rate_per_min"]
    over = max(0.0, poll - 40.0)
    return 45.0 + (over ** 1.4) * 0.9 + random.uniform(-5, 5)


@app.get("/healthz")
def healthz():
    return {"ok": True, "poll_rate_per_min": _state["poll_rate_per_min"]}


@app.get("/resolve")
def resolve(device_id: str = "unknown"):
    if AUTO_CASCADE:
        _maybe_drift()
    latency_ms = _resolve_latency_ms()
    time.sleep(latency_ms / 1000.0)
    return {
        "device_id": device_id,
        "bed": f"MedSurg-{random.randint(301, 348)}",
        "resolve_latency_ms": round(latency_ms, 1),
        "poll_rate_per_min": _state["poll_rate_per_min"],
    }


@app.post("/admin/poll-rate")
def set_poll_rate(rate: float):
    """Clamp/raise the inventory poll rate. The remediation controller POSTs
    here with rate=60 to recover; a demo operator can POST a high value to
    trigger the cascade manually."""
    old = _state["poll_rate_per_min"]
    _state["poll_rate_per_min"] = max(1.0, rate)
    log.info("poll_rate changed %.0f -> %.0f/min", old, _state["poll_rate_per_min"])
    return {"poll_rate_per_min": _state["poll_rate_per_min"], "previous": old}


def _maybe_drift():
    # Every ~2 min of wall clock, nudge the poll rate up toward 190 to emulate
    # the firmware-config storm; clamp resumes via /admin/poll-rate.
    elapsed = time.time() - _state["started"]
    cycle = int(elapsed // 120) % 3
    if cycle == 1 and _state["poll_rate_per_min"] < 190:
        _state["poll_rate_per_min"] = min(190.0, _state["poll_rate_per_min"] + 6.0)
