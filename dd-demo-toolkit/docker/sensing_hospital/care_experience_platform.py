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
PATIENT_CONTEXT_URL = os.getenv("PATIENT_CONTEXT_URL", "http://patient-context-service:8080")
CLINICAL_ALERTS_URL = os.getenv("CLINICAL_ALERTS_URL", "http://clinical-alerts-service:8080")
SESSION = requests.Session()

app = FastAPI()


@app.get("/healthz")
def healthz():
    return {"ok": True}


def _get(url: str, **kw):
    r = SESSION.get(url, timeout=10, **kw)
    return r.json()


@app.post("/ingest")
def ingest(event: dict):
    """Fan out to the on-prem RTLS (location), the patient-context service
    (Postgres-backed identity), and the clinical-alerts service (Postgres +
    Redis). This makes the platform a branching node in the service map rather
    than a single hop, and the trace shows parallel downstream dependencies."""
    device_id = event.get("device_id", "unknown")
    statsd.increment("care.platform.ingest_total")
    start = time.monotonic()
    location = patient = alert = None

    try:
        location = _get(f"{RTLS_URL}/resolve", params={"device_id": device_id})
    except requests.RequestException as e:
        statsd.increment("care.platform.ingest_errors_total", tags=["dependency:rtls"])
        log.warning("rtls resolve failed for %s: %s", device_id, e)

    try:
        patient = _get(f"{PATIENT_CONTEXT_URL}/patient", params={"device_id": device_id})
    except requests.RequestException as e:
        statsd.increment("care.platform.ingest_errors_total", tags=["dependency:patient-context"])
        log.warning("patient-context lookup failed for %s: %s", device_id, e)

    # Only raise a clinical alert when we resolved a patient.
    if patient and patient.get("bound"):
        try:
            r = SESSION.post(
                f"{CLINICAL_ALERTS_URL}/evaluate",
                json={
                    "patient_id": patient.get("patient_id"),
                    "bed_id": patient.get("bed_id"),
                    "acuity": patient.get("acuity", "stable"),
                    "kind": event.get("event", "device_event"),
                },
                timeout=10,
            )
            alert = r.json()
        except requests.RequestException as e:
            statsd.increment("care.platform.ingest_errors_total", tags=["dependency:clinical-alerts"])
            log.warning("clinical-alerts evaluate failed for %s: %s", device_id, e)

    statsd.gauge("care.platform.ingest_latency_ms", (time.monotonic() - start) * 1000.0)
    return {"status": "processed", "device_id": device_id,
            "location": location, "patient": patient, "alert": alert}
