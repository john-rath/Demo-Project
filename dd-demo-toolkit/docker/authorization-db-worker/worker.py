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
        "deployment.environment.name": "demo",
        "team": "Payments",
        "vertical": os.environ.get("DD_DEMO_VERTICAL", "finance"),
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


# --- Healthcare tick functions (hospital patient/medication/device) ----------

DEPARTMENTS = ["ICU", "ED", "OR", "MedSurg", "Pharmacy"]
DRUG_CODES   = ["VANCOMYCIN", "HEPARIN", "INSULIN", "MORPHINE", "METOPROLOL", "AMOXICILLIN"]
ALERT_CODES  = ["OCCLUSION", "OFFLINE", "LOW_BATTERY", "SIGNAL_LOSS"]
DEVICE_TYPES = ["infusion_pump", "patient_monitor", "ventilator"]


def _random_mrn() -> str:
    return "MRN" + str(random.randint(1, 300)).zfill(7)


def _random_pump_id() -> str:
    return "BD-ALARIS-" + str(random.randint(1, 250)).zfill(4)


def run_healthcare_normal_tick(conn) -> None:
    with tracer.start_as_current_span(
        "db.healthcare.normal",
        attributes={"db.system": "postgresql", "db.name": DB_CONFIG["dbname"],
                    "db.operation": "SELECT", "cascade.phase": "normal"},
    ):
        with conn.cursor() as cur:
            cur.execute(
                "SELECT encounter_id, department, admitted_at "
                "FROM healthcare.patient_encounters WHERE patient_mrn = %s LIMIT 1",
                (_random_mrn(),),
            )
            row = cur.fetchone()
            if row:
                cur.execute(
                    "SELECT order_id, drug_code, status "
                    "FROM healthcare.medication_orders WHERE encounter_id = %s",
                    (row[0],),
                )
                cur.fetchall()
            cur.execute(
                "SELECT session_id, flow_rate_ml_hr, volume_infused_ml "
                "FROM healthcare.infusion_sessions WHERE pump_device_id = %s "
                "ORDER BY started_at DESC LIMIT 1",
                (_random_pump_id(),),
            )
            cur.fetchone()
            cur.execute(
                "INSERT INTO healthcare.device_alerts "
                "(device_id, device_type, bed_id, alert_code, severity, detected_at) "
                "VALUES (%s, %s, %s, %s, 'INFO', NOW())",
                (_random_pump_id(), random.choice(DEVICE_TYPES),
                 random.choice(DEPARTMENTS) + "-F1-Bed-01", random.choice(ALERT_CODES)),
            )
            conn.commit()


def run_healthcare_ramp_up_tick(conn) -> None:
    run_healthcare_normal_tick(conn)
    if random.random() < 0.3:
        with tracer.start_as_current_span(
            "db.healthcare.department_scan",
            attributes={"db.system": "postgresql", "db.operation": "SELECT",
                        "cascade.phase": "ramp_up"},
        ):
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT encounter_id, patient_mrn FROM healthcare.patient_encounters "
                    "WHERE department = %s ORDER BY admitted_at DESC",
                    (random.choice(DEPARTMENTS),),
                )
                cur.fetchall()
                conn.commit()


def run_healthcare_degraded_tick(conn) -> None:
    with tracer.start_as_current_span(
        "db.healthcare.degraded",
        attributes={"db.system": "postgresql", "db.operation": "SELECT",
                    "cascade.phase": "degraded"},
    ):
        with conn.cursor() as cur:
            # Full scan on department (no index) — slow fingerprint in DBM
            cur.execute(
                "SELECT pe.department, COUNT(mo.order_id), "
                "       SUM(CASE WHEN mo.status = 'PENDING' THEN 1 ELSE 0 END) "
                "FROM healthcare.patient_encounters pe "
                "LEFT JOIN healthcare.medication_orders mo ON pe.encounter_id = mo.encounter_id "
                "WHERE pe.department = %s "
                "GROUP BY pe.department",
                (random.choice(DEPARTMENTS),),
            )
            cur.fetchall()
            # Row-level lock on active infusion sessions
            cur.execute(
                "SELECT session_id, volume_infused_ml FROM healthcare.infusion_sessions "
                "WHERE status = 'ACTIVE' ORDER BY started_at DESC LIMIT 10 FOR UPDATE",
            )
            rows = cur.fetchall()
            time.sleep(random.uniform(0.05, 0.12))
            conn.commit()
            for _ in range(3):
                cur.execute(
                    "INSERT INTO healthcare.device_alerts "
                    "(device_id, device_type, bed_id, alert_code, severity, detected_at) "
                    "VALUES (%s, %s, %s, %s, 'CRITICAL', NOW())",
                    (_random_pump_id(), random.choice(DEVICE_TYPES),
                     random.choice(DEPARTMENTS) + "-F3-Bed-" + str(random.randint(1, 20)).zfill(2),
                     "SIGNAL_LOSS"),
                )
            conn.commit()


def run_healthcare_cascading_tick(held_conns: list, normal_pool: pool.ThreadedConnectionPool) -> None:
    while len(held_conns) < 18:
        try:
            c = psycopg2.connect(**DB_CONFIG)
            c.autocommit = False
            held_conns.append(c)
        except psycopg2.OperationalError:
            break
    if held_conns:
        conn = held_conns[len(held_conns) % max(1, len(held_conns) - 1)]
        with tracer.start_as_current_span(
            "db.healthcare.full_scan",
            attributes={"db.system": "postgresql", "db.operation": "SELECT",
                        "cascade.phase": "cascading", "db.connections.held": len(held_conns)},
        ):
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT pe.department, COUNT(iss.session_id), "
                        "       SUM(iss.volume_infused_ml) "
                        "FROM healthcare.infusion_sessions iss "
                        "JOIN healthcare.medication_orders mo ON iss.order_id = mo.order_id "
                        "JOIN healthcare.patient_encounters pe ON mo.encounter_id = pe.encounter_id "
                        "GROUP BY pe.department ORDER BY SUM(iss.volume_infused_ml) DESC"
                    )
                    cur.fetchall()
                    conn.rollback()
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass


# --- Hospitality tick functions (hotel reservations/IoT/revenue) -------------

BRAND_TYPES  = ["Luxury Collection", "Premium Resort", "Full Service",
                "Upscale Select", "Select Service", "Extended Stay"]
ISSUE_TYPES  = ["ROOM_CONTROL", "LOCK", "THERMOSTAT", "ENTERTAINMENT"]


def _random_property_id() -> str:
    return "PROP-" + str(random.randint(1, 60)).zfill(4)


def _random_gateway_id() -> str:
    return "GW-" + str(random.randint(1, 400)).zfill(6)


def run_hospitality_normal_tick(conn) -> None:
    with tracer.start_as_current_span(
        "db.hospitality.normal",
        attributes={"db.system": "postgresql", "db.operation": "SELECT",
                    "cascade.phase": "normal"},
    ):
        with conn.cursor() as cur:
            prop = _random_property_id()
            cur.execute(
                "SELECT reservation_id, room_number, status "
                "FROM hospitality.guest_reservations "
                "WHERE property_id = %s AND status = 'CHECKED_IN' LIMIT 5",
                (prop,),
            )
            cur.fetchall()
            cur.execute(
                "SELECT gateway_id, connected_devices, online_status "
                "FROM hospitality.room_iot_gateways WHERE property_id = %s",
                (prop,),
            )
            cur.fetchall()
            cur.execute(
                "SELECT revpar_usd, occupancy_pct FROM hospitality.daily_revenue_snapshot "
                "WHERE property_id = %s ORDER BY snapshot_date DESC LIMIT 1",
                (prop,),
            )
            cur.fetchone()
            conn.commit()


def run_hospitality_ramp_up_tick(conn) -> None:
    run_hospitality_normal_tick(conn)
    if random.random() < 0.3:
        with tracer.start_as_current_span(
            "db.hospitality.brand_scan",
            attributes={"db.system": "postgresql", "db.operation": "SELECT",
                        "cascade.phase": "ramp_up"},
        ):
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT p.property_id, COUNT(gr.reservation_id), "
                    "       SUM(gr.nightly_rate_usd) "
                    "FROM hospitality.guest_reservations gr "
                    "JOIN hospitality.properties p ON gr.property_id = p.property_id "
                    "WHERE p.brand_type = %s "
                    "GROUP BY p.property_id",
                    (random.choice(BRAND_TYPES),),
                )
                cur.fetchall()
                conn.commit()


def run_hospitality_degraded_tick(conn) -> None:
    with tracer.start_as_current_span(
        "db.hospitality.degraded",
        attributes={"db.system": "postgresql", "db.operation": "SELECT",
                    "cascade.phase": "degraded"},
    ):
        with conn.cursor() as cur:
            # Full scan: brand_type join with no index — slow fingerprint in DBM
            cur.execute(
                "SELECT p.brand_type, COUNT(gr.reservation_id), "
                "       AVG(gr.nightly_rate_usd) "
                "FROM hospitality.guest_reservations gr "
                "JOIN hospitality.properties p ON gr.property_id = p.property_id "
                "WHERE p.brand_type = %s "
                "GROUP BY p.brand_type",
                (random.choice(BRAND_TYPES),),
            )
            cur.fetchall()
            # Row lock on gateway records (concurrent IoT alarm storm inserts)
            cur.execute(
                "SELECT gateway_id FROM hospitality.room_iot_gateways "
                "WHERE online_status = false LIMIT 10 FOR UPDATE",
            )
            rows = cur.fetchall()
            time.sleep(random.uniform(0.05, 0.12))
            conn.commit()
            # IoT alarm storm: rapid service request inserts
            reservation_id = random.randint(1, 600)
            for _ in range(4):
                cur.execute(
                    "INSERT INTO hospitality.guest_service_requests "
                    "(reservation_id, issue_type, severity, status, created_at) "
                    "VALUES (%s, %s, 'HIGH', 'OPEN', NOW())",
                    (reservation_id, random.choice(ISSUE_TYPES)),
                )
            conn.commit()


def run_hospitality_cascading_tick(held_conns: list, normal_pool: pool.ThreadedConnectionPool) -> None:
    while len(held_conns) < 18:
        try:
            c = psycopg2.connect(**DB_CONFIG)
            c.autocommit = False
            held_conns.append(c)
        except psycopg2.OperationalError:
            break
    if held_conns:
        conn = held_conns[len(held_conns) % max(1, len(held_conns) - 1)]
        with tracer.start_as_current_span(
            "db.hospitality.full_scan",
            attributes={"db.system": "postgresql", "db.operation": "SELECT",
                        "cascade.phase": "cascading", "db.connections.held": len(held_conns)},
        ):
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT p.brand_type, p.region, COUNT(gr.reservation_id), "
                        "       SUM(gr.nightly_rate_usd) "
                        "FROM hospitality.guest_reservations gr "
                        "JOIN hospitality.properties p ON gr.property_id = p.property_id "
                        "GROUP BY p.brand_type, p.region "
                        "ORDER BY SUM(gr.nightly_rate_usd) DESC"
                    )
                    cur.fetchall()
                    conn.rollback()
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass


# --- Insurance tick functions (claims surge / catastrophe scenario) ----------

LOSS_TYPES   = ["Wind", "Flood", "Theft", "Collision", "Fire"]
PAY_METHODS  = ["ACH", "CHECK", "WIRE"]
REINSURERS   = ["MUNICH_RE", "SWISS_RE", "BERKSHIRE", "LLOYD_LONDON"]


def _random_policy_id() -> str:
    return "POL-" + str(random.randint(1, 500)).zfill(8)


def _random_claim_id() -> int:
    return random.randint(1, 400)


def run_insurance_normal_tick(conn) -> None:
    with tracer.start_as_current_span(
        "db.insurance.normal",
        attributes={"db.system": "postgresql", "db.operation": "SELECT",
                    "cascade.phase": "normal"},
    ):
        with conn.cursor() as cur:
            cur.execute(
                "SELECT policy_id, line_of_business, status "
                "FROM insurance.policies WHERE policy_id = %s",
                (_random_policy_id(),),
            )
            row = cur.fetchone()
            claim_id = _random_claim_id()
            cur.execute(
                "SELECT claim_id, estimated_reserve_usd, paid_amount_usd, status "
                "FROM insurance.claims WHERE claim_id = %s",
                (claim_id,),
            )
            cur.fetchone()
            cur.execute(
                "SELECT doc_id, document_type, ocr_confidence "
                "FROM insurance.claim_documents WHERE claim_id = %s",
                (claim_id,),
            )
            cur.fetchall()
            cur.execute(
                "SELECT payment_id, status FROM insurance.claims_payments "
                "WHERE claim_id = %s ORDER BY created_at DESC LIMIT 1",
                (claim_id,),
            )
            cur.fetchone()
            conn.commit()


def run_insurance_ramp_up_tick(conn) -> None:
    run_insurance_normal_tick(conn)
    if random.random() < 0.3:
        with tracer.start_as_current_span(
            "db.insurance.loss_type_scan",
            attributes={"db.system": "postgresql", "db.operation": "SELECT",
                        "cascade.phase": "ramp_up"},
        ):
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT loss_type, COUNT(*), SUM(estimated_reserve_usd) "
                    "FROM insurance.claims WHERE loss_type = %s "
                    "GROUP BY loss_type",
                    (random.choice(LOSS_TYPES),),
                )
                cur.fetchall()
                conn.commit()


def run_insurance_degraded_tick(conn) -> None:
    with tracer.start_as_current_span(
        "db.insurance.degraded",
        attributes={"db.system": "postgresql", "db.operation": "SELECT",
                    "cascade.phase": "degraded"},
    ):
        with conn.cursor() as cur:
            # Full scan on loss_type (no index) — slow fingerprint in DBM
            cur.execute(
                "SELECT loss_type, region, COUNT(*), SUM(estimated_reserve_usd) "
                "FROM insurance.claims c "
                "JOIN insurance.policies p ON c.policy_id = p.policy_id "
                "WHERE c.loss_type = %s "
                "GROUP BY loss_type, region",
                (random.choice(LOSS_TYPES),),
            )
            cur.fetchall()
            # Row lock on pending payments (payment-processor hold pattern)
            cur.execute(
                "SELECT payment_id, amount_usd FROM insurance.claims_payments "
                "WHERE status = 'PENDING' ORDER BY created_at DESC LIMIT 5 FOR UPDATE",
            )
            rows = cur.fetchall()
            time.sleep(random.uniform(0.05, 0.12))
            conn.commit()
            # Surge inserts: rapid FNOL during catastrophe event
            for _ in range(3):
                cur.execute(
                    "INSERT INTO insurance.claims "
                    "(policy_id, loss_date, loss_type, reported_at, estimated_reserve_usd, status) "
                    "VALUES (%s, CURRENT_DATE, %s, NOW(), %s, 'FNOL_RECEIVED')",
                    (_random_policy_id(), random.choice(LOSS_TYPES),
                     round(1000 + random.random() * 49000, 2)),
                )
            conn.commit()


def run_insurance_cascading_tick(held_conns: list, normal_pool: pool.ThreadedConnectionPool) -> None:
    while len(held_conns) < 18:
        try:
            c = psycopg2.connect(**DB_CONFIG)
            c.autocommit = False
            held_conns.append(c)
        except psycopg2.OperationalError:
            break
    if held_conns:
        conn = held_conns[len(held_conns) % max(1, len(held_conns) - 1)]
        with tracer.start_as_current_span(
            "db.insurance.full_scan",
            attributes={"db.system": "postgresql", "db.operation": "SELECT",
                        "cascade.phase": "cascading", "db.connections.held": len(held_conns)},
        ):
            try:
                with conn.cursor() as cur:
                    # Cross-join analytics: reserve exposure by loss type + reinsurer
                    cur.execute(
                        "SELECT c.loss_type, rs.reinsurer_id, "
                        "       COUNT(*), SUM(c.estimated_reserve_usd), "
                        "       SUM(rs.share_amount_usd) "
                        "FROM insurance.claims c "
                        "LEFT JOIN insurance.reinsurance_share rs ON c.claim_id = rs.claim_id "
                        "GROUP BY c.loss_type, rs.reinsurer_id "
                        "ORDER BY SUM(c.estimated_reserve_usd) DESC"
                    )
                    cur.fetchall()
                    conn.rollback()
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass


# --- Manufacturing tick functions (SCADA telemetry / RUL batch) --------------

METRIC_TYPES  = ["cycle_time_ms", "servo_error_count", "temperature_c", "vibration_g"]
DEVICE_TYPES_MFG = ["robot_arm", "cnc_machine", "conveyor", "press", "vision_system"]
PLANTS        = ["Plant-A-Detroit", "Plant-B-Austin", "Plant-C-Munich"]


def _random_device_id() -> str:
    lines  = ["ASSY-1-DET","ASSY-2-DET","WELD-1-DET","WELD-2-DET","PAINT-1-DET",
              "ASSY-1-AUS","WELD-1-AUS","PAINT-1-AUS","ASSY-1-MUN","WELD-1-MUN"]
    dtypes = ["robot_arm","cnc_machine","conveyor","press","vision_system"]
    i = random.randint(1, 80)
    return lines[i % 10] + "-" + dtypes[i % 5] + "-" + str(i).zfill(3)


def run_manufacturing_normal_tick(conn) -> None:
    with tracer.start_as_current_span(
        "db.manufacturing.normal",
        attributes={"db.system": "postgresql", "db.operation": "SELECT",
                    "cascade.phase": "normal"},
    ):
        with conn.cursor() as cur:
            device_id = _random_device_id()
            cur.execute(
                "SELECT metric_type, metric_value, recorded_at "
                "FROM manufacturing.telemetry_snapshots "
                "WHERE device_id = %s ORDER BY recorded_at DESC LIMIT 5",
                (device_id,),
            )
            cur.fetchall()
            cur.execute(
                "SELECT predicted_rul_hours, confidence_pct "
                "FROM manufacturing.rul_predictions "
                "WHERE device_id = %s ORDER BY predicted_at DESC LIMIT 1",
                (device_id,),
            )
            cur.fetchone()
            cur.execute(
                "INSERT INTO manufacturing.telemetry_snapshots "
                "(device_id, metric_type, metric_value, recorded_at) "
                "VALUES (%s, %s, %s, NOW())",
                (device_id, random.choice(METRIC_TYPES),
                 round(random.uniform(0.1, 60000), 2)),
            )
            conn.commit()


def run_manufacturing_ramp_up_tick(conn) -> None:
    run_manufacturing_normal_tick(conn)
    if random.random() < 0.3:
        with tracer.start_as_current_span(
            "db.manufacturing.analytical_scan",
            attributes={"db.system": "postgresql", "db.operation": "SELECT",
                        "cascade.phase": "ramp_up"},
        ):
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT device_id, AVG(metric_value), MAX(metric_value) "
                    "FROM manufacturing.telemetry_snapshots "
                    "WHERE metric_type = %s "
                    "GROUP BY device_id ORDER BY AVG(metric_value) DESC",
                    (random.choice(METRIC_TYPES),),
                )
                cur.fetchall()
                conn.commit()


def run_manufacturing_degraded_tick(conn) -> None:
    with tracer.start_as_current_span(
        "db.manufacturing.degraded",
        attributes={"db.system": "postgresql", "db.operation": "SELECT",
                    "cascade.phase": "degraded"},
    ):
        with conn.cursor() as cur:
            # RUL batch analytical scan — no index on metric_type, forces full scan
            cur.execute(
                "SELECT ed.device_type, ed.manufacturer, "
                "       AVG(ts.metric_value), MAX(ts.metric_value), COUNT(*) "
                "FROM manufacturing.telemetry_snapshots ts "
                "JOIN manufacturing.equipment_devices ed ON ts.device_id = ed.device_id "
                "WHERE ts.metric_type = %s "
                "GROUP BY ed.device_type, ed.manufacturer "
                "ORDER BY AVG(ts.metric_value) DESC",
                (random.choice(METRIC_TYPES),),
            )
            cur.fetchall()
            # Row lock on equipment device record during servo fault processing
            cur.execute(
                "SELECT device_id, model FROM manufacturing.equipment_devices "
                "WHERE device_type = %s LIMIT 5 FOR UPDATE",
                (random.choice(DEVICE_TYPES_MFG),),
            )
            rows = cur.fetchall()
            time.sleep(random.uniform(0.05, 0.15))
            conn.commit()
            # High-frequency SCADA inserts — back-pressure on writer during analytic lock
            for _ in range(5):
                cur.execute(
                    "INSERT INTO manufacturing.telemetry_snapshots "
                    "(device_id, metric_type, metric_value, recorded_at) "
                    "VALUES (%s, %s, %s, NOW())",
                    (_random_device_id(), random.choice(METRIC_TYPES),
                     round(random.uniform(0.1, 60000), 2)),
                )
            conn.commit()


def run_manufacturing_cascading_tick(held_conns: list, normal_pool: pool.ThreadedConnectionPool) -> None:
    while len(held_conns) < 18:
        try:
            c = psycopg2.connect(**DB_CONFIG)
            c.autocommit = False
            held_conns.append(c)
        except psycopg2.OperationalError:
            break
    if held_conns:
        conn = held_conns[len(held_conns) % max(1, len(held_conns) - 1)]
        with tracer.start_as_current_span(
            "db.manufacturing.full_scan",
            attributes={"db.system": "postgresql", "db.operation": "SELECT",
                        "cascade.phase": "cascading", "db.connections.held": len(held_conns)},
        ):
            try:
                with conn.cursor() as cur:
                    # Full analytical RUL batch across all devices — most expensive query
                    cur.execute(
                        "SELECT ed.device_type, ed.manufacturer, ed.line_id, "
                        "       AVG(ts.metric_value) AS avg_val, "
                        "       MAX(rul.predicted_rul_hours) AS rul_hours "
                        "FROM manufacturing.telemetry_snapshots ts "
                        "JOIN manufacturing.equipment_devices ed ON ts.device_id = ed.device_id "
                        "LEFT JOIN manufacturing.rul_predictions rul ON ts.device_id = rul.device_id "
                        "GROUP BY ed.device_type, ed.manufacturer, ed.line_id "
                        "ORDER BY AVG(ts.metric_value) DESC"
                    )
                    cur.fetchall()
                    conn.rollback()
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass


# --- Vertical dispatch table ------------------------------------------------

_TICK_TABLE: dict[str, dict] = {
    "finance": {
        "normal":    run_normal_tick,
        "ramp_up":   run_ramp_up_tick,
        "degraded":  run_degraded_tick,
        "cascading": run_cascading_tick,
        "recovery":  run_normal_tick,
    },
    "healthcare": {
        "normal":    run_healthcare_normal_tick,
        "ramp_up":   run_healthcare_ramp_up_tick,
        "degraded":  run_healthcare_degraded_tick,
        "cascading": run_healthcare_cascading_tick,
        "recovery":  run_healthcare_normal_tick,
    },
    "hospitality": {
        "normal":    run_hospitality_normal_tick,
        "ramp_up":   run_hospitality_ramp_up_tick,
        "degraded":  run_hospitality_degraded_tick,
        "cascading": run_hospitality_cascading_tick,
        "recovery":  run_hospitality_normal_tick,
    },
    "insurance": {
        "normal":    run_insurance_normal_tick,
        "ramp_up":   run_insurance_ramp_up_tick,
        "degraded":  run_insurance_degraded_tick,
        "cascading": run_insurance_cascading_tick,
        "recovery":  run_insurance_normal_tick,
    },
    "manufacturing": {
        "normal":    run_manufacturing_normal_tick,
        "ramp_up":   run_manufacturing_ramp_up_tick,
        "degraded":  run_manufacturing_degraded_tick,
        "cascading": run_manufacturing_cascading_tick,
        "recovery":  run_manufacturing_normal_tick,
    },
}


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

    dispatch = _TICK_TABLE.get(VERTICAL, _TICK_TABLE["finance"])
    logger.info("vertical=%s — DBM workload active", VERTICAL)

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
                dispatch["cascading"](_held_conns, _normal_pool)
            else:
                conn = _normal_pool.getconn()
                try:
                    tick_fn = dispatch.get(phase, dispatch["normal"])
                    tick_fn(conn)
                finally:
                    _normal_pool.putconn(conn)
        except Exception as e:
            logger.warning("tick error (phase=%s): %s", phase, e)

        time.sleep(TICK_INTERVAL)


if __name__ == "__main__":
    main()
