"""patient-context-service — CLOUD service, Postgres-backed read path.

Resolves a device_id to its bound patient + bed + acuity from sensing-postgres.
One branch of the care-experience-platform fan-out; its DB calls show up as
Postgres spans in the service map (real infra dependency).
"""
from __future__ import annotations

import logging

from fastapi import FastAPI

from db import pg_cursor
from metrics import statsd

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("patient-context-service")

app = FastAPI()


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/patient")
def patient(device_id: str = "unknown"):
    statsd.increment("care.patient_context.lookups_total")
    with pg_cursor() as cur:
        cur.execute(
            """
            SELECT p.patient_id, p.display_name, p.acuity,
                   b.bed_id, b.floor, b.wing, b.department
            FROM device_bindings d
            JOIN patients p ON p.patient_id = d.patient_id
            JOIN beds b     ON b.bed_id = p.bed_id
            WHERE d.device_id = %s
            """,
            (device_id,),
        )
        row = cur.fetchone()
    if not row:
        statsd.increment("care.patient_context.unbound_total")
        return {"device_id": device_id, "bound": False}
    return {
        "device_id": device_id,
        "bound": True,
        "patient_id": row[0],
        "display_name": row[1],
        "acuity": row[2],
        "bed_id": row[3],
        "floor": row[4],
        "wing": row[5],
        "department": row[6],
    }
