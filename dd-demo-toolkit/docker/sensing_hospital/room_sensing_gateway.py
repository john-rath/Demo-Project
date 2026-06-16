"""room-sensing-gateway — ON-PREM aggregator.

Stands in for the on-prem gateway that aggregates room environmental/occupancy
sensors and forwards room-state events to the care-event-router. A second
on-prem event source alongside the device fleet, so the edge tier isn't a
single producer.

Not an HTTP service: a periodic producer loop. Auto-instrumented by dd-trace-py.
DD_SERVICE/DD_TAGS (deployment:on-prem) come from docker-compose.
"""
from __future__ import annotations

import logging
import os
import random
import time

import requests

from metrics import statsd

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("room-sensing-gateway")

ROUTER_URL = os.getenv("ROUTER_URL", "http://care-event-router:8080")
INTERVAL_SEC = float(os.getenv("ROOM_INTERVAL_SEC", "6"))
ROOMS = [f"rtls_badge-{i:03d}" for i in range(5)]
SESSION = requests.Session()


def main():
    log.info("room-sensing-gateway forwarding room state every %.0fs", INTERVAL_SEC)
    while True:
        device_id = random.choice(ROOMS)
        event = {
            "device_id": device_id,
            "device_type": "room_sensing_gateway",
            "event": "room_state",
            "occupancy": random.randint(0, 1),
            "temp_c": round(random.uniform(20.5, 23.5), 1),
            "ts": time.time(),
        }
        try:
            SESSION.post(f"{ROUTER_URL}/events", json=event, timeout=10)
            statsd.increment("care.room_gateway.forwarded_total")
        except requests.RequestException as e:
            statsd.increment("care.room_gateway.forward_errors_total")
            log.debug("forward failed: %s", e)
        time.sleep(INTERVAL_SEC)


if __name__ == "__main__":
    main()
