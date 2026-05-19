"""
Risk Eval Agent (DSM terminal node).

Final consumer in the EY data pipeline. Reads enriched events from
`risk-eval-jobs`, simulates the LLM-eval decision (PASS / WATCH), and
logs the result for the agent.

Service name `risk-eval-agent` is shared with the LangGraph service
declared in `verticals/finance/overlays/ey.yaml` and the EY services
catalog. Datadog merges both telemetry sources under the same service
entity, so the demo narrative is: open `risk-eval-agent` in the Service
Catalog and see DSM pipeline lineage AND LLM Obs eval scoring on the
same service page.

Env:
  KAFKA_BOOTSTRAP          kafka:9092
  KAFKA_TOPIC_ENRICHED     risk-eval-jobs
  DD_SERVICE               risk-eval-agent
  DD_DATA_STREAMS_ENABLED  true
"""

import ddtrace.auto  # noqa: F401, isort:skip

import json
import logging
import os
import random
import signal
import time

from confluent_kafka import Consumer, KafkaException

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("risk-eval-agent")

BOOTSTRAP = os.environ.get("KAFKA_BOOTSTRAP", "kafka:9092")
TOPIC = os.environ.get("KAFKA_TOPIC_ENRICHED", "risk-eval-jobs")
GROUP_ID = os.environ.get("KAFKA_GROUP_ID", "risk-eval-agent")


_running = True


def _stop(signum, _frame):
    global _running
    log.info("signal %s received; stopping", signum)
    _running = False


def _evaluate(event: dict) -> dict:
    # Simulate the LLM-eval decision: high-concentration or breach
    # events land on the Watch List. F1 score floats in a healthy band
    # by default; degraded data quality (null_rate_pct > 5) drags F1
    # down to mirror Scott's "bad data → bad eval" narrative.
    null_rate = event.get("null_rate_pct", 1.0)
    if null_rate > 5.0:
        f1 = round(random.uniform(0.55, 0.74), 3)
    else:
        f1 = round(random.uniform(0.82, 0.93), 3)
    decision = "WATCH" if event.get("covenant_breach") else "PASS"
    return {
        "event_id": event["event_id"],
        "counterparty": event["counterparty"],
        "client_engagement": event.get("client_engagement"),
        "concentration_pct": event.get("concentration_pct"),
        "f1": f1,
        "null_rate_pct": null_rate,
        "decision": decision,
        "evaluated_at_ms": int(time.time() * 1000),
    }


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
    consumer.subscribe([TOPIC])
    log.info(
        "eval-agent started: bootstrap=%s topic=%s group=%s",
        BOOTSTRAP, TOPIC, GROUP_ID,
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
            # Simulate eval work — tiny sleep so latency is visible in APM
            time.sleep(random.uniform(0.02, 0.12))
            decision = _evaluate(event)
            log.info("evaluated: %s", json.dumps(decision))
    finally:
        consumer.close()
        log.info("eval-agent stopped")


if __name__ == "__main__":
    main()
