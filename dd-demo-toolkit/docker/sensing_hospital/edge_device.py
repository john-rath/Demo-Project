"""edge-device fleet generator — the mock IoT fleet.

Reads fleet_config.yaml, selects the scenario for MOCK_SCENARIO (defaults to
DD_DEMO_VERTICAL), and simulates every configured device: each one POSTs a
JSON event to the care-event-router on its interval. One container drives the
whole fleet via background threads — scale the event volume by editing
fleet_config.yaml, not by editing compose.

Auto-instrumented by dd-trace-py; the requests calls show up as the client
edge of the distributed trace, so device-perceived latency is a real span.
DD_SERVICE/DD_TAGS come from docker-compose.
"""
from __future__ import annotations

import logging
import os
import random
import threading
import time
from pathlib import Path

import requests
import yaml

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("edge-device")

ROUTER_URL = os.getenv("ROUTER_URL", "http://care-event-router:8080")
SCENARIO = os.getenv("MOCK_SCENARIO") or os.getenv("DD_DEMO_VERTICAL") or "healthcare"
CONFIG_PATH = Path(os.getenv("FLEET_CONFIG", "/app/fleet_config.yaml"))
SESSION = requests.Session()


def _load_scenario() -> dict:
    cfg = yaml.safe_load(CONFIG_PATH.read_text())
    scenarios = cfg.get("scenarios", {})
    if SCENARIO not in scenarios:
        log.warning("scenario %r not in fleet_config; falling back to healthcare", SCENARIO)
        return scenarios.get("healthcare", {})
    return scenarios[SCENARIO]


def _device_loop(device_id: str, dtype: str, event: str, interval: float, location: dict):
    # Jitter the start so the fleet doesn't post in lockstep.
    time.sleep(random.uniform(0, interval))
    while True:
        payload = {
            "device_id": device_id,
            "device_type": dtype,
            "event": event,
            "location": location,
            "ts": time.time(),
        }
        try:
            SESSION.post(f"{ROUTER_URL}/events", json=payload, timeout=12)
        except requests.RequestException as e:
            log.debug("post failed for %s: %s", device_id, e)
        time.sleep(interval)


def main():
    scenario = _load_scenario()
    location = scenario.get("location", {})
    log.info("starting edge fleet for scenario=%s (%s)", SCENARIO, scenario.get("display_name", ""))
    threads = []
    for dt in scenario.get("device_types", []):
        for i in range(int(dt.get("count", 1))):
            device_id = f"{dt['type']}-{i:03d}"
            t = threading.Thread(
                target=_device_loop,
                args=(device_id, dt["type"], dt.get("event", "heartbeat"),
                      float(dt.get("interval_sec", 5)), location),
                daemon=True,
            )
            t.start()
            threads.append(t)
    log.info("edge fleet running: %d devices", len(threads))
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
