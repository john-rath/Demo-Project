"""
Feature pipeline — middle node of the EY DSM topology.

Consumes raw risk events from `risk-feature-events-raw`, enriches them
with derived risk features (concentration %, covenant breach flag,
synthetic data-quality signal), and emits to `risk-eval-jobs`. dd-trace
auto-instruments both consume and produce calls, so DSM sees this as a
hop in the pathway: ingester → feature-pipeline → eval-consumer.

The synthetic `null_rate_pct` field is the upstream signal that drives
Scott's "data hand in glove" narrative — when the null-rate spikes here,
the downstream LLM eval F1 score regresses ~30s later.

Env:
  KAFKA_BOOTSTRAP            kafka:9092
  KAFKA_TOPIC_RAW            risk-feature-events-raw
  KAFKA_TOPIC_ENRICHED       risk-eval-jobs
  DATA_QUALITY_FAULT_PCT     0.02   (fraction of events with elevated null_rate)
"""

import ddtrace.auto  # noqa: F401, isort:skip

import json
import logging
import os
import random
import signal
import time

from confluent_kafka import Consumer, KafkaException, Producer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("risk-feature-pipeline")

BOOTSTRAP = os.environ.get("KAFKA_BOOTSTRAP", "kafka:9092")
RAW_TOPIC = os.environ.get("KAFKA_TOPIC_RAW", "risk-feature-events-raw")
ENRICHED_TOPIC = os.environ.get("KAFKA_TOPIC_ENRICHED", "risk-eval-jobs")
GROUP_ID = os.environ.get("KAFKA_GROUP_ID", "risk-feature-pipeline")
FAULT_PCT = float(os.environ.get("DATA_QUALITY_FAULT_PCT", "0.02"))
TIER1_LIMIT = 5_000_000_000.0  # arbitrary "tier-1 limit" for concentration math


_running = True


def _stop(signum, _frame):
    global _running
    log.info("signal %s received; stopping", signum)
    _running = False


def _enrich(event: dict) -> dict:
    concentration_pct = round(event["exposure_usd"] / TIER1_LIMIT * 100, 2)
    # Data-quality signal: most events are clean (~0.3-1.5% null rate);
    # a small fraction are degraded (5-12%). Spikes here are what the
    # downstream LLM eval F1 regression alert eventually picks up on.
    if random.random() < FAULT_PCT:
        null_rate_pct = round(random.uniform(5.0, 12.0), 2)
    else:
        null_rate_pct = round(random.uniform(0.3, 1.5), 2)

    event["concentration_pct"] = concentration_pct
    event["covenant_breach"] = concentration_pct > 35.0
    event["null_rate_pct"] = null_rate_pct
    event["enriched_at_ms"] = int(time.time() * 1000)
    return event


def main() -> None:
    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    consumer = Consumer(
        {
            "bootstrap.servers": BOOTSTRAP,
            "group.id": GROUP_ID,
            "auto.offset.reset": "latest",
            "enable.auto.commit": True,
        }
    )
    producer = Producer(
        {
            "bootstrap.servers": BOOTSTRAP,
            "client.id": "risk-feature-pipeline",
            "acks": "1",
            "compression.type": "lz4",
        }
    )

    consumer.subscribe([RAW_TOPIC])
    log.info(
        "feature-pipeline started: bootstrap=%s read=%s write=%s group=%s",
        BOOTSTRAP, RAW_TOPIC, ENRICHED_TOPIC, GROUP_ID,
    )

    try:
        while _running:
            msg = consumer.poll(1.0)
            if msg is None:
                continue
            if msg.error():
                raise KafkaException(msg.error())
            try:
                event = json.loads(msg.value())
            except json.JSONDecodeError:
                log.warning("dropping un-parseable event")
                continue
            enriched = _enrich(event)
            producer.produce(
                ENRICHED_TOPIC,
                key=msg.key(),
                value=json.dumps(enriched).encode("utf-8"),
            )
            producer.poll(0)
    finally:
        consumer.close()
        producer.flush(10)
        log.info("feature-pipeline flushed; exiting")


if __name__ == "__main__":
    main()
