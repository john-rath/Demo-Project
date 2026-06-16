"""notification-service — CLOUD service, care-team messaging.

Sends a (simulated) secure message to the care team when clinical-alerts raises
an alert. A terminal downstream of clinical-alerts, so the service map shows the
alerting path fanning out to notification — the "who gets told" leaf.

DD_SERVICE/DD_TAGS (deployment:cloud) come from docker-compose.
"""
from __future__ import annotations

import logging
import random
import time

from fastapi import FastAPI

from metrics import statsd

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("notification-service")

app = FastAPI()


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.post("/notify")
def notify(body: dict):
    severity = body.get("severity", "low")
    # Simulate secure-message delivery latency to the clinician handset.
    time.sleep(random.uniform(0.05, 0.2))
    statsd.increment("care.notification.sent_total", tags=[f"severity:{severity}"])
    return {"delivered": True, "channel": "secure-message", "severity": severity}
