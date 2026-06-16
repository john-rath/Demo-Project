"""Shared Postgres + Redis helpers for the Sensing Hospital data-backed services.

Kept deliberately small and env-configured (12-factor) so the same code runs
against the local compose Postgres/Redis today and against RDS/ElastiCache when
this moves to k8s on AWS — no host assumptions. dd-trace-py auto-instruments
psycopg2 and redis, so DB and cache calls show up as spans in the service map.
"""
from __future__ import annotations

import os
import time
from contextlib import contextmanager

import psycopg2
import psycopg2.pool
import redis

_PG_POOL: psycopg2.pool.SimpleConnectionPool | None = None


def _pg_dsn() -> dict:
    return {
        "host": os.getenv("DB_HOST", "sensing-postgres"),
        "port": int(os.getenv("DB_PORT", "5432")),
        "dbname": os.getenv("DB_NAME", "sensing"),
        "user": os.getenv("DB_USER", "sensing"),
        "password": os.getenv("DB_PASSWORD", "sensing"),
    }


def init_pg_pool(retries: int = 30, delay: float = 2.0) -> None:
    """Create the connection pool, waiting for Postgres to accept connections
    (compose `depends_on` only waits for container start, not readiness)."""
    global _PG_POOL
    last_err: Exception | None = None
    for _ in range(retries):
        try:
            _PG_POOL = psycopg2.pool.SimpleConnectionPool(1, 8, **_pg_dsn())
            return
        except psycopg2.Error as e:  # not ready yet
            last_err = e
            time.sleep(delay)
    raise RuntimeError(f"Postgres not reachable after {retries} tries: {last_err}")


@contextmanager
def pg_cursor():
    if _PG_POOL is None:
        init_pg_pool()
    conn = _PG_POOL.getconn()
    try:
        with conn.cursor() as cur:
            yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        _PG_POOL.putconn(conn)


_REDIS: redis.Redis | None = None


def get_redis() -> redis.Redis:
    global _REDIS
    if _REDIS is None:
        _REDIS = redis.Redis(
            host=os.getenv("REDIS_HOST", "sensing-redis"),
            port=int(os.getenv("REDIS_PORT", "6379")),
            decode_responses=True,
        )
    return _REDIS


# --- Redis Stream helpers (the async event bus) ----------------------------
CARE_EVENTS_STREAM = os.getenv("CARE_EVENTS_STREAM", "care-events")
CARE_EVENTS_GROUP = os.getenv("CARE_EVENTS_GROUP", "care-consumers")


def stream_publish(fields: dict) -> str:
    """XADD an event onto the care-events stream. Returns the message id."""
    # Redis stream values must be flat strings; JSON-encode the payload.
    import json
    return get_redis().xadd(CARE_EVENTS_STREAM, {"payload": json.dumps(fields)})


def ensure_consumer_group() -> None:
    """Create the consumer group (idempotent), making the stream if needed."""
    r = get_redis()
    try:
        r.xgroup_create(CARE_EVENTS_STREAM, CARE_EVENTS_GROUP, id="0", mkstream=True)
    except redis.ResponseError as e:
        if "BUSYGROUP" not in str(e):
            raise
