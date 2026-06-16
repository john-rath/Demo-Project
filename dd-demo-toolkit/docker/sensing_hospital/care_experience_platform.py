"""care-experience-platform — CLOUD service, the saturating tier.

Ingests enriched care events from the on-prem care-event-router, calls the
on-prem rtls-location-service to attach a bed/room location, then "processes"
the event. Because every ingest blocks on rtls /resolve, the cloud tier's
latency tracks the on-prem root cause — the on-prem→cloud cascade David asked
to show, visible as a real distributed trace in the Datadog service map.

DD_SERVICE/DD_TAGS (deployment:cloud) come from docker-compose.
"""
from __future__ import annotations

import logging
import os
import time

import requests
from fastapi import FastAPI

from metrics import statsd

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("care-experience-platform")

RTLS_URL = os.getenv("RTLS_URL", "http://rtls-location-service:8080")
SESSION = requests.Session()

app = FastAPI()


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.post("/ingest")
def ingest(event: dict):
    device_id = event.get("device_id", "unknown")
    location = None
    statsd.increment("care.platform.ingest_total")
    start = time.monotonic()
    try:
        r = SESSION.get(f"{RTLS_URL}/resolve", params={"device_id": device_id}, timeout=10)
        location = r.json()
    except requests.RequestException as e:
        statsd.increment("care.platform.ingest_errors_total")
        log.warning("rtls resolve failed for %s: %s", device_id, e)
    statsd.gauge("care.platform.ingest_latency_ms", (time.monotonic() - start) * 1000.0)
    return {"status": "processed", "device_id": device_id, "location": location}
