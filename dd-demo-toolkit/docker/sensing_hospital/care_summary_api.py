"""care-summary-api — CLOUD read path behind the care-portal.

Synchronous aggregation the RUM frontend calls: resolves the patient
(patient-context-service) and pulls recent alerts (clinical-alerts-service),
then returns a care summary. Because the portal calls this same-origin and RUM
injects trace headers, a RUM session links to this backend trace and its
downstream fan-out — the RUM→APM correlation story end to end.

DD_SERVICE/DD_TAGS (deployment:cloud) come from docker-compose.
"""
from __future__ import annotations

import logging
import os

import requests
from fastapi import FastAPI

from metrics import statsd

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("care-summary-api")

PATIENT_CONTEXT_URL = os.getenv("PATIENT_CONTEXT_URL", "http://patient-context-service:8080")
CLINICAL_ALERTS_URL = os.getenv("CLINICAL_ALERTS_URL", "http://clinical-alerts-service:8080")
SESSION = requests.Session()

app = FastAPI()


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/summary")
def summary(device_id: str = "rtls_badge-000"):
    statsd.increment("care.summary.requests_total")
    patient = alerts = None
    try:
        patient = SESSION.get(f"{PATIENT_CONTEXT_URL}/patient",
                              params={"device_id": device_id}, timeout=10).json()
    except requests.RequestException as e:
        log.warning("patient-context failed: %s", e)
    pid = (patient or {}).get("patient_id")
    if pid:
        try:
            alerts = SESSION.get(f"{CLINICAL_ALERTS_URL}/alerts",
                                 params={"patient_id": pid}, timeout=10).json()
        except requests.RequestException as e:
            log.warning("clinical-alerts list failed: %s", e)
    return {"device_id": device_id, "patient": patient, "recent_alerts": alerts}
