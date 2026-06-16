"""care-event-consumer — async worker draining the care-events Redis Stream.

Reads events the router published, and for each one calls the cloud
care-experience-platform /ingest (which fans out to RTLS + patient-context +
clinical-alerts). This is the async processing tier — it shows up in the
service map as the consumer between the edge and the cloud platform.

Not an HTTP service: a long-running consumer loop. Auto-instrumented by
dd-trace-py; each processed message starts a trace into the platform fan-out.
DD_SERVICE/DD_TAGS (deployment:cloud) come from docker-compose.
"""
from __future__ import annotations

import json
import logging
import os
import socket
import time

import requests

from db import CARE_EVENTS_GROUP, CARE_EVENTS_STREAM, ensure_consumer_group, get_redis
from metrics import statsd

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("care-event-consumer")

PLATFORM_URL = os.getenv("PLATFORM_URL", "http://care-experience-platform:8080")
CONSUMER_NAME = os.getenv("HOSTNAME") or socket.gethostname()
SESSION = requests.Session()


def _process(event: dict) -> None:
    statsd.increment("care.consumer.processed_total")
    try:
        SESSION.post(f"{PLATFORM_URL}/ingest", json=event, timeout=12)
    except requests.RequestException as e:
        statsd.increment("care.consumer.process_errors_total")
        log.warning("platform ingest failed: %s", e)


def main():
    # Wait for Redis + create the consumer group (idempotent).
    for _ in range(30):
        try:
            ensure_consumer_group()
            break
        except Exception as e:
            log.info("waiting for redis: %s", e)
            time.sleep(2)
    r = get_redis()
    log.info("consuming %s as %s/%s", CARE_EVENTS_STREAM, CARE_EVENTS_GROUP, CONSUMER_NAME)
    while True:
        try:
            resp = r.xreadgroup(
                CARE_EVENTS_GROUP, CONSUMER_NAME,
                {CARE_EVENTS_STREAM: ">"}, count=10, block=5000,
            )
        except Exception as e:
            log.warning("xreadgroup error: %s", e)
            time.sleep(2)
            continue
        if not resp:
            continue
        for _stream, messages in resp:
            for msg_id, fields in messages:
                try:
                    event = json.loads(fields.get("payload", "{}"))
                    _process(event)
                finally:
                    r.xack(CARE_EVENTS_STREAM, CARE_EVENTS_GROUP, msg_id)


if __name__ == "__main__":
    main()
