"""care-event-router — ON-PREM/edge service, front of the ASYNC path.

Receives raw device/room events and publishes them onto the `care-events`
Redis Stream. This decouples ingestion from processing: the care-event-consumer
worker drains the stream and drives the cloud fan-out. The async boundary is
deliberate — it's the realistic shape (edge buffers, cloud processes) and it
shows a queue in the architecture, not just request/response.

DD_SERVICE/DD_TAGS (deployment:on-prem) come from docker-compose.
"""
from __future__ import annotations

import logging

from fastapi import FastAPI

from db import stream_publish
from metrics import statsd

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("care-event-router")

app = FastAPI()
_counters = {"events": 0}


@app.get("/healthz")
def healthz():
    return {"ok": True, "events": _counters["events"]}


@app.post("/events")
def events(event: dict):
    _counters["events"] += 1
    statsd.increment("care.router.events_total", tags=[f"device_type:{event.get('device_type', 'unknown')}"])
    try:
        msg_id = stream_publish(event)
        return {"accepted": True, "stream_id": msg_id}
    except Exception as e:  # redis down / transient
        statsd.increment("care.router.publish_errors_total")
        log.warning("stream publish failed: %s", e)
        return {"accepted": False, "error": str(e)}
