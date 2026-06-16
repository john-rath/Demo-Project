"""clinical-alerts-service — CLOUD service, Postgres + Redis backed.

Evaluates a care event into an alert. Uses Redis for short-window dedup (so a
flapping device doesn't create duplicate alerts) and Postgres for the durable
alert record. Both calls are real spans, so the service map shows this node
depending on BOTH a cache and a database — the kind of infra fan-out a real
distributed system has.
"""
from __future__ import annotations

import logging

from fastapi import FastAPI

from db import get_redis, pg_cursor
from metrics import statsd

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("clinical-alerts-service")

DEDUP_TTL_SEC = 30

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
    return {"alert": "created", "alert_id": alert_id, "severity": severity}
