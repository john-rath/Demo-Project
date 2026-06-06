"""
Authorization Engine DB workload generator.

Runs a continuous SQL workload against the authorization-engine Postgres
database. Reads /cascade-state/phase.json (written by the cascade plugin
on the shared volume) and adjusts query patterns to match the cascade phase:

  normal/recovery  — fast indexed lookups, no contention
  ramp_up          — occasional full-scan merchant queries mixed in
  degraded         — sequential scans + row-lock contention
  cascading        — connection pool exhaustion + full scans (slow query signal)

The slow queries during degraded/cascading phases show up in Datadog DBM's
"Top Queries" view, demonstrating that DB wait time is amplifying auth latency.
"""

import json
import logging
import os
import random
import string
import time
from contextlib import contextmanager

import psycopg2
from psycopg2 import pool
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("authorization-db-worker")

# OTel tracer — exports to otel-collector when OTEL_EXPORTER_OTLP_ENDPOINT is set.
# Falls back to a no-op provider when the env var is absent (local dev without compose).
def _init_tracer() -> trace.Tracer:
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    if not endpoint:
        return trace.get_tracer("authorization-db")
    resource = Resource.create({
        "service.name": os.environ.get("OTEL_SERVICE_NAME", "authorization-db"),
        "service.version": "1.0.0",
        "env": "demo",
        "team": "Payments",
        "vertical": "finance",
        "dd-demo-toolkit": "true",
    })
    provider = TracerProvider(resource=resource)
    exporter = OTLPSpanExporter(endpoint=endpoint)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    return trace.get_tracer("authorization-db")

tracer = _init_tracer()

DB_CONFIG = {
    "host": os.environ.get("DB_HOST", "authorization-db"),
    "port": int(os.environ.get("DB_PORT", "5432")),
    "user": os.environ.get("DB_USER", "auth"),
    "password": os.environ.get("DB_PASSWORD", "auth"),
    "dbname": os.environ.get("DB_NAME", "authorization_engine"),
}

PHASE_FILE = "/cascade-state/phase.json"
TICK_INTERVAL = float(os.environ.get("WORKER_INTERVAL_SEC", "1.0"))

REGIONS = ["ap-southeast-1", "us-east-1", "eu-west-1"]
STATUSES = ["APPROVED"] * 85 + ["DECLINED"] * 12 + ["TIMEOUT"] * 3
MERCHANT_IDS = [f"MERCH_{i:05d}" for i in range(1, 201)]

_normal_pool: pool.ThreadedConnectionPool | None = None
_held_conns: list = []  # connections held open during cascading phase


def make_pool(minconn: int = 2, maxconn: int = 10) -> pool.ThreadedConnectionPool:
    return pool.ThreadedConnectionPool(minconn, maxconn, **DB_CONFIG)


def read_phase() -> tuple[str, int]:
    try:
        with open(PHASE_FILE) as f:
            data = json.load(f)
        return data.get("phase", "normal"), data.get("tick", 0)
    except (FileNotFoundError, json.JSONDecodeError):
        return "normal", 0


def random_card_token() -> str:
    return "card_" + str(random.randint(1, 500)).zfill(6)


def random_tx_row() -> tuple:
    return (
        random_card_token(),
        random.choice(MERCHANT_IDS),
        random.randint(100, 875000),
        random.choice(REGIONS),
        random.choice(STATUSES),
    )


# --- Query helpers ----------------------------------------------------------

def run_normal_tick(conn) -> None:
    """Fast indexed lookups + inserts — baseline DBM profile."""
    with tracer.start_as_current_span(
        "db.auth_transaction.normal",
        attributes={
            "db.system": "postgresql",
            "db.name": DB_CONFIG["dbname"],
            "db.operation": "SELECT",
            "peer.service": "authorization-db",
            "cascade.phase": "normal",
        },
    ):
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, status, created_at FROM auth_transactions "
                "WHERE card_token = %s ORDER BY created_at DESC LIMIT 1",
                (random_card_token(),),
            )
            cur.fetchone()
            cur.execute(
                "SELECT score, risk_level FROM fraud_scores "
                "WHERE card_token = %s ORDER BY evaluated_at DESC LIMIT 1",
                (random_card_token(),),
            )
            cur.fetchone()
            cur.execute(
                "SELECT daily_limit_cents, current_day_total_cents "
                "FROM card_limits WHERE card_token = %s",
                (random_card_token(),),
            )
            cur.fetchone()
            cur.execute(
                "INSERT INTO auth_transactions "
                "(card_token, merchant_id, amount_cents, region, status) "
                "VALUES (%s, %s, %s, %s, %s)",
                random_tx_row(),
            )
            conn.commit()


def run_ramp_up_tick(conn) -> None:
    """Mixed: mostly indexed, occasional unindexed merchant scan."""
    run_normal_tick(conn)
    if random.random() < 0.3:
        with tracer.start_as_current_span(
            "db.auth_transaction.merchant_scan",
            attributes={
                "db.system": "postgresql",
                "db.name": DB_CONFIG["dbname"],
                "db.operation": "SELECT",
                "db.statement": "SELECT COUNT(*) FROM auth_transactions WHERE merchant_id = ? AND status = 'DECLINED'",
                "peer.service": "authorization-db",
                "cascade.phase": "ramp_up",
            },
        ):
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM auth_transactions "
                    "WHERE merchant_id = %s AND status = 'DECLINED'",
                    (random.choice(MERCHANT_IDS),),
                )
                cur.fetchone()
                conn.commit()


def run_degraded_tick(conn) -> None:
    """Sequential scans + row-level lock contention."""
    with tracer.start_as_current_span(
        "db.auth_transaction.degraded",
        attributes={
            "db.system": "postgresql",
            "db.name": DB_CONFIG["dbname"],
            "db.operation": "SELECT",
            "db.statement": "SELECT card_token, SUM(amount_cents) FROM auth_transactions WHERE merchant_id = ? GROUP BY card_token",
            "peer.service": "authorization-db",
            "cascade.phase": "degraded",
        },
    ):
        with conn.cursor() as cur:
            # Sequential scan on merchant_id (no index) — shows as slow fingerprint in DBM
            cur.execute(
                "SELECT card_token, SUM(amount_cents) FROM auth_transactions "
                "WHERE merchant_id = %s GROUP BY card_token",
                (random.choice(MERCHANT_IDS),),
            )
            cur.fetchall()
            # Row lock: SELECT FOR UPDATE then release slowly
            cur.execute(
                "SELECT id FROM auth_transactions "
                "WHERE card_token = %s ORDER BY created_at DESC LIMIT 5 FOR UPDATE",
                (random_card_token(),),
            )
            rows = cur.fetchall()
            time.sleep(random.uniform(0.05, 0.15))  # hold lock briefly
            conn.commit()

            # Insert to keep table growing (fills cache, slows scans)
            for _ in range(3):
                cur.execute(
                    "INSERT INTO auth_transactions "
                    "(card_token, merchant_id, amount_cents, region, status) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    random_tx_row(),
                )
            conn.commit()


def run_cascading_tick(held_conns: list, normal_pool: pool.ThreadedConnectionPool) -> None:
    """Connection pool exhaustion: hold connections open, run full scans."""
    global _held_conns

    # Hold up to 18 connections open (out of max_connections=20 on the DB)
    # so the connection pool appears exhausted in DBM
    while len(held_conns) < 18:
        try:
            c = psycopg2.connect(**DB_CONFIG)
            c.autocommit = False
            held_conns.append(c)
        except psycopg2.OperationalError:
            break  # already at max; that's the point

    # Run a slow full-table scan on each held connection (rotates through them)
    if held_conns:
        conn = held_conns[len(held_conns) % max(1, len(held_conns) - 1)]
        with tracer.start_as_current_span(
            "db.auth_transaction.full_scan",
            attributes={
                "db.system": "postgresql",
                "db.name": DB_CONFIG["dbname"],
                "db.operation": "SELECT",
                "db.statement": "SELECT card_token, COUNT(*), SUM(amount_cents) FROM auth_transactions GROUP BY card_token ORDER BY SUM(amount_cents) DESC",
                "peer.service": "authorization-db",
                "cascade.phase": "cascading",
                "db.connections.held": len(held_conns),
            },
        ):
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT card_token, COUNT(*), SUM(amount_cents) "
                        "FROM auth_transactions GROUP BY card_token ORDER BY SUM(amount_cents) DESC"
                    )
                    cur.fetchall()
                    conn.rollback()
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass


def release_held_connections(held_conns: list) -> None:
    for c in held_conns:
        try:
            c.close()
        except Exception:
            pass
    held_conns.clear()


# --- Main loop --------------------------------------------------------------

def wait_for_db() -> pool.ThreadedConnectionPool:
    while True:
        try:
            p = make_pool()
            conn = p.getconn()
            conn.cursor().execute("SELECT 1")
            p.putconn(conn)
            logger.info("Connected to authorization-engine DB at %s", DB_CONFIG["host"])
            return p
        except Exception as e:
            logger.info("Waiting for DB: %s", e)
            time.sleep(3)


def main() -> None:
    global _normal_pool, _held_conns
    _normal_pool = wait_for_db()
    _held_conns = []
    prev_phase = "normal"

    while True:
        phase, tick = read_phase()

        if prev_phase != phase:
            logger.info("phase=%s tick=%d", phase, tick)
            if phase not in ("degraded", "cascading") and _held_conns:
                logger.info("Releasing %d held connections (recovery)", len(_held_conns))
                release_held_connections(_held_conns)

        prev_phase = phase

        try:
            if phase == "cascading":
                run_cascading_tick(_held_conns, _normal_pool)
            else:
                conn = _normal_pool.getconn()
                try:
                    if phase in ("normal", "recovery"):
                        run_normal_tick(conn)
                    elif phase == "ramp_up":
                        run_ramp_up_tick(conn)
                    elif phase == "degraded":
                        run_degraded_tick(conn)
                finally:
                    _normal_pool.putconn(conn)
        except Exception as e:
            logger.warning("tick error (phase=%s): %s", phase, e)

        time.sleep(TICK_INTERVAL)


if __name__ == "__main__":
    main()
