"""clinical-alerts-service — CLOUD service, Postgres + Redis backed.

Evaluates a care event into an alert. Uses Redis for short-window dedup (so a
flapping device doesn't create duplicate alerts) and Postgres for the durable
alert record. Both calls are real spans, so the service map shows this node
depending on BOTH a cache and a database — the kind of infra fan-out a real
distributed system has.
"""
from __future__ import annotations

import logging
import os

import requests
from fastapi import FastAPI

from db import get_redis, pg_cursor
from metrics import statsd

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("clinical-alerts-service")

DEDUP_TTL_SEC = 30
NOTIFICATION_URL = os.getenv("NOTIFICATION_URL", "http://notification-service:8080")
SESSION = requests.Session()

app = FastAPI()


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.post("/evaluate")
def evaluate(body: dict):
    patient_id = body.get("patient_id")
    bed_id = body.get("bed_id")
    acuity = body.get("acuity", "stable")
    kind = body.get("kind", "device_event")
    statsd.increment("care.clinical_alerts.evaluations_total")

    severity = {"critical": "high", "guarded": "medium"}.get(acuity, "low")

    # Dedup on (patient, kind) within a short window via Redis SETNX.
    dedup_key = f"alert:{patient_id}:{kind}"
    is_new = bool(get_redis().set(dedup_key, "1", nx=True, ex=DEDUP_TTL_SEC))
    if not is_new:
        statsd.increment("care.clinical_alerts.deduped_total")
        return {"alert": "deduped", "severity": severity}

    with pg_cursor() as cur:
        cur.execute(
            "INSERT INTO alerts (patient_id, bed_id, severity, kind) "
            "VALUES (%s, %s, %s, %s) RETURNING alert_id",
            (patient_id, bed_id, severity, kind),
        )
        alert_id = cur.fetchone()[0]
    statsd.increment("care.clinical_alerts.alerts_created_total", tags=[f"severity:{severity}"])

    # Fan out to the notification service (care-team messaging).
    try:
        SESSION.post(f"{NOTIFICATION_URL}/notify",
                     json={"patient_id": patient_id, "severity": severity, "kind": kind}, timeout=10)
    except requests.RequestException as e:
        statsd.increment("care.clinical_alerts.notify_errors_total")
        log.warning("notification failed: %s", e)
    return {"alert": "created", "alert_id": alert_id, "severity": severity}


@app.get("/alerts")
def list_alerts(patient_id: str, limit: int = 10):
    statsd.increment("care.clinical_alerts.list_total")
    with pg_cursor() as cur:
        cur.execute(
            "SELECT alert_id, severity, kind, created_at FROM alerts "
            "WHERE patient_id = %s ORDER BY created_at DESC LIMIT %s",
            (patient_id, limit),
        )
        rows = cur.fetchall()
    return {
        "patient_id": patient_id,
        "alerts": [
            {"alert_id": r[0], "severity": r[1], "kind": r[2], "created_at": r[3].isoformat()}
            for r in rows
        ],
    }
