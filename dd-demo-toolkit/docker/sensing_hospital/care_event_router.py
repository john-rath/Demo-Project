"""care-event-router — ON-PREM/edge service.

Receives raw device events from the edge fleet, lightly enriches them, and
forwards to the cloud care-experience-platform /ingest. First hop in the
trace, so its latency reflects the full downstream cascade as experienced
closest to the device.

DD_SERVICE/DD_TAGS (deployment:on-prem) come from docker-compose.
"""
from __future__ import annotations

import logging
import os

import requests
from fastapi import FastAPI

from metrics import statsd

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("care-event-router")

PLATFORM_URL = os.getenv("PLATFORM_URL", "http://care-experience-platform:8080")
SESSION = requests.Session()

app = FastAPI()
_counters = {"events": 0}


@app.get("/healthz")
def healthz():
    return {"ok": True, "events": _counters["events"]}


@app.post("/events")
def events(event: dict):
    _counters["events"] += 1
    statsd.increment("care.router.events_total", tags=[f"device_type:{event.get('device_type', 'unknown')}"])
    enriched = dict(event)
    enriched["routed_by"] = "care-event-router"
    try:
        r = SESSION.post(f"{PLATFORM_URL}/ingest", json=enriched, timeout=12)
        return {"accepted": True, "platform": r.json()}
    except requests.RequestException as e:
        statsd.increment("care.router.forward_errors_total")
        log.warning("forward to platform failed: %s", e)
        return {"accepted": False, "error": str(e)}
