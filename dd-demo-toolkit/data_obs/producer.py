"""
Risk-data ingester — entry point for the EY Data Streams Monitoring demo.

Emits synthetic risk-feature events onto the `risk-feature-events-raw`
Kafka topic at a configurable cadence. dd-trace-py's Kafka
auto-instrumentation (enabled via `ddtrace.auto`) injects DSM pathway
checkpoints into the message headers, so every produce shows up as the
head of a pipeline pathway in Datadog Data Streams Monitoring.

The event payloads name counterparties from the FINANCE_SCENARIOS in
`dd_demo_toolkit/simulator/llm_obs.py` (ACME Capital, Globex Industries,
…). That way the DSM pipeline view and the LLM Obs trace view can be
linked back to the same counterparty when narrating the demo.

Env:
  KAFKA_BOOTSTRAP          kafka:9092
  KAFKA_TOPIC_RAW          risk-feature-events-raw
  PRODUCER_INTERVAL_SEC    1.5
  DD_SERVICE               risk-data-ingester  (set in docker-compose)
  DD_ENV / DD_VERSION      demo / 1.0.0
  DD_DATA_STREAMS_ENABLED  true
"""

# IMPORTANT: ddtrace.auto must be the very first import — it patches
# confluent-kafka at module-load time. DSM pathway propagation depends
# on the patched Producer.
import ddtrace.auto  # noqa: F401, isort:skip

import json
import logging
import os
import random
import signal
import time
import uuid

from confluent_kafka import Producer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("risk-data-ingester")

# Mirrors the FINANCE_SCENARIOS counterparties so DSM events and LLM Obs
# traces share counterparty names — narration links the two product UIs.
COUNTERPARTIES = [
    "ACME Capital",
    "Globex Industries",
    "Initech Holdings",
    "Stark Enterprises",
    "Wayne Financial",
    "Soylent Corp",
]

CLIENT_ENGAGEMENTS = [
    "acme_audit_2026q2",
    "globex_compliance_2026q2",
    "initech_audit_2026q2",
    "stark_ma_diligence_2026q2",
    "wayne_model_validation_2026q2",
    "soylent_credit_review_2026q2",
]

TOPIC = os.environ.get("KAFKA_TOPIC_RAW", "risk-feature-events-raw")
BOOTSTRAP = os.environ.get("KAFKA_BOOTSTRAP", "kafka:9092")
INTERVAL = float(os.environ.get("PRODUCER_INTERVAL_SEC", "1.5"))


_running = True


def _stop(signum, _frame):
    global _running
    log.info("signal %s received; stopping", signum)
    _running = False


def _delivery(err, msg):
    if err is not None:
        log.warning("delivery failed: %s", err)


def _build_event() -> dict:
    counterparty = random.choice(COUNTERPARTIES)
    return {
        "event_id": str(uuid.uuid4()),
        "counterparty": counterparty,
        "client_engagement": random.choice(CLIENT_ENGAGEMENTS),
        "exposure_usd": round(random.uniform(50_000_000, 2_500_000_000), 2),
        "vol_30d": round(random.uniform(0.08, 0.18), 4),
        "var_99_usd": round(random.uniform(10_000_000, 60_000_000), 2),
        "doc_types": random.sample(
            ["10K", "swap_master", "internal_credit_memo", "trade_blotter"],
            k=random.randint(2, 4),
        ),
        "received_at_ms": int(time.time() * 1000),
    }


def main() -> None:
    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    producer = Producer(
        {
            "bootstrap.servers": BOOTSTRAP,
            "client.id": "risk-data-ingester",
            "acks": "1",
            "compression.type": "lz4",
        }
    )

    log.info(
        "producer started: bootstrap=%s topic=%s interval=%.2fs",
        BOOTSTRAP, TOPIC, INTERVAL,
    )

    while _running:
        event = _build_event()
        key = event["counterparty"].encode("utf-8")
        value = json.dumps(event).encode("utf-8")
        producer.produce(TOPIC, key=key, value=value, on_delivery=_delivery)
        producer.poll(0)
        time.sleep(INTERVAL)

    producer.flush(10)
    log.info("producer flushed; exiting")


if __name__ == "__main__":
    main()
