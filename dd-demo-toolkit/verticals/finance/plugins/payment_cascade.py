"""
Payment Processing Cascade Incident Plugin for Finance Vertical

Simulates a realistic incident cascade: Database replication lag in primary region
→ Payment processing timeouts → Fraud detection backlog → Online banking errors.

This demonstrates how infrastructure issues in one layer (database) cascade through
the entire stack, affecting multiple business-critical services. The incident phases
are temporally realistic and allow Datadog AI RCA to discover causality.

The incident plays out in distinct phases:
  1. ramp_up (8 ticks / 2m): DB replication lag climbing slowly
  2. degraded (10 ticks / 2.5m): Payment gateway timeouts increasing, fraud detection latency rising
  3. cascading (8 ticks / 2m): Online banking errors spiking, multiple services failing
  4. recovering (12 ticks / 3m): DB catches up, services recover in reverse order

This is the primary incident scenario for the finance vertical, demonstrating how
critical infrastructure health directly impacts customer-facing services and SLA compliance.
"""

import random
import logging
from typing import Any, Dict, List, Optional

from dd_demo_toolkit.simulator.plugins import IncidentPlugin

logger = logging.getLogger("payment_cascade_incident")


def clamp(value: float, lo: float, hi: float) -> float:
    """Clamp a value to [lo, hi]."""
    return max(lo, min(hi, value))


def drift(value: float, magnitude: float = 1.0, bias: float = 0.0) -> float:
    """Apply Gaussian drift to a value."""
    return value + random.gauss(bias, magnitude)


class PaymentCascadeIncident(IncidentPlugin):
    """
    Simulates a database replication lag cascade affecting payment processing.

    This is the primary incident scenario for the finance vertical. It demonstrates:
    - How infrastructure issues in databases cascade through the application stack
    - Temporal causality: lag appears first, then timeouts, then errors
    - Multiple service dependencies affected: payments, fraud, online banking
    - Real-world incident progression and recovery
    """

    # Incident phase durations (in ticks, each tick = 15 seconds by default)
    RAMP_TICKS = 8          # 2m: DB replication lag starts climbing
    DEGRADED_TICKS = 10     # 2.5m: Payment timeouts and fraud detection lag
    CASCADING_TICKS = 8     # 2m: Online banking errors spike
    RECOVERY_TICKS = 12     # 3m: DB catches up, services recover

    # Total event duration: 38 ticks = 9m30s of visible activity
    EVENT_TICKS = RAMP_TICKS + DEGRADED_TICKS + CASCADING_TICKS + RECOVERY_TICKS

    # Fixed incident location (us-east-1 production)
    INCIDENT_REGION = "us-east-1"
    INCIDENT_ENV = "production"

    def __init__(self):
        """Initialize the incident plugin."""
        self._ticks_until_next = random.randint(40, 80)
        self._active_tick: Optional[int] = None
        self._incident_dbs: List[Dict[str, Any]] = []
        self._incident_payment_gws: List[Dict[str, Any]] = []
        self._incident_fraud_nodes: List[Dict[str, Any]] = []
        self._incident_caches: List[Dict[str, Any]] = []

        logger.info(
            f"Payment Cascade Incident initialized. First incident in ~{self._ticks_until_next * 15 // 60} min"
        )

    def on_tick(self, tick_count: int, fleet: List[Dict[str, Any]], engine: Any) -> None:
        """Called on each simulator tick to apply incident overrides."""
        # Index incident devices on first tick
        if tick_count == 0 or not self._incident_dbs:
            self._incident_dbs = [
                d for d in fleet
                if d.get("device_type") == "database_cluster"
                and d.get("region") == self.INCIDENT_REGION
                and d.get("environment") == self.INCIDENT_ENV
            ]
            self._incident_payment_gws = [
                d for d in fleet
                if d.get("device_type") == "payment_gateway"
                and d.get("region") == self.INCIDENT_REGION
                and d.get("environment") == self.INCIDENT_ENV
            ]
            self._incident_fraud_nodes = [
                d for d in fleet
                if d.get("device_type") == "fraud_detection_node"
                and d.get("region") == self.INCIDENT_REGION
                and d.get("environment") == self.INCIDENT_ENV
            ]
            self._incident_caches = [
                d for d in fleet
                if d.get("device_type") == "cache_cluster"
                and d.get("region") == self.INCIDENT_REGION
                and d.get("environment") == self.INCIDENT_ENV
            ]

            if self._incident_dbs:
                logger.info(
                    f"Indexed incident devices in {self.INCIDENT_REGION}/{self.INCIDENT_ENV}: "
                    f"{len(self._incident_dbs)} DBs, "
                    f"{len(self._incident_payment_gws)} payment gateways, "
                    f"{len(self._incident_fraud_nodes)} fraud nodes, "
                    f"{len(self._incident_caches)} caches"
                )

        # Advance incident clock and apply overrides
        self._advance_incident_clock()
        phase, phase_tick = self._get_incident_phase()

        if phase != "normal":
            self._apply_overrides(phase, phase_tick)
            logger.info(
                f"INCIDENT [{phase} t={phase_tick}] {self.INCIDENT_REGION}/{self.INCIDENT_ENV}: "
                f"db_lag={self._incident_dbs[0].get('_incident_replication_lag', 0):.0f}ms, "
                f"payment_errors={sum(1 for p in self._incident_payment_gws if p.get('_incident_error_rate', 0) > 0.05)}/{len(self._incident_payment_gws)}, "
                f"fraud_latency={self._incident_fraud_nodes[0].get('_incident_model_latency', 0):.0f}ms"
                if self._incident_dbs and self._incident_payment_gws and self._incident_fraud_nodes
                else f"INCIDENT [{phase} t={phase_tick}] {self.INCIDENT_REGION}/{self.INCIDENT_ENV}"
            )

    def get_incident_name(self) -> str:
        """Return human-readable name for this incident."""
        return "Payment Processing Cascade: DB Replication Lag → Payment Timeouts → Online Banking Errors"

    def reset(self) -> None:
        """Reset plugin state."""
        self._ticks_until_next = random.randint(40, 80)
        self._active_tick = None
        self._incident_dbs = []
        self._incident_payment_gws = []
        self._incident_fraud_nodes = []
        self._incident_caches = []

    # === Private methods ===

    def _get_incident_phase(self) -> tuple:
        """Return (phase_name, tick_within_phase) for current state."""
        if self._active_tick is None:
            return ("normal", 0)

        t = self._active_tick
        r = self.RAMP_TICKS
        d = r + self.DEGRADED_TICKS
        c = d + self.CASCADING_TICKS
        e = c + self.RECOVERY_TICKS

        if t < r:
            return ("ramp_up", t)
        elif t < d:
            return ("degraded", t - r)
        elif t < c:
            return ("cascading", t - d)
        elif t < e:
            return ("recovering", t - c)
        return ("normal", 0)

    def _advance_incident_clock(self) -> None:
        """Advance the incident state machine each tick."""
        if self._active_tick is not None:
            self._active_tick += 1
            if self._active_tick >= self.EVENT_TICKS:
                self._active_tick = None
                self._ticks_until_next = random.randint(60, 120)
                logger.info(
                    f"Incident complete. Next incident in ~{self._ticks_until_next * 15 // 60} min"
                )
        else:
            self._ticks_until_next -= 1
            if self._ticks_until_next <= 0:
                self._active_tick = 0
                logger.info(f"INCIDENT STARTING: {self.INCIDENT_REGION}/{self.INCIDENT_ENV}")

    def _apply_overrides(self, phase: str, phase_tick: int) -> None:
        """Apply incident state overrides to devices."""
        # === PHASE 1: RAMP UP (DB lag climbing) ===
        if phase == "ramp_up":
            progress = phase_tick / self.RAMP_TICKS

            # DB replication lag climbs: 85ms → 350ms
            for db in self._incident_dbs:
                db["_incident_replication_lag"] = 85 + progress * 265
                db["_incident_queries_per_sec"] = clamp(drift(185000, 20000), 160000, 220000)
                db["_incident_connection_pool_pct"] = clamp(drift(68 + progress * 12, 3), 65, 85)

            # Payment gateways unaffected yet
            for pg in self._incident_payment_gws:
                pg["_incident_timeout_rate"] = clamp(progress * 0.5, 0, 0.05)
                pg["_incident_error_rate"] = clamp(progress * 0.01, 0, 0.015)

            # Fraud detection starts slowing (cache hits degrading)
            for cache in self._incident_caches:
                cache["_incident_hit_rate"] = clamp(92.5 - progress * 5, 87.5, 95)

        # === PHASE 2: DEGRADED (Payment timeout cascade) ===
        elif phase == "degraded":
            progress = phase_tick / self.DEGRADED_TICKS

            # DB lag plateaus high
            for db in self._incident_dbs:
                db["_incident_replication_lag"] = clamp(random.gauss(320, 40), 250, 400)
                db["_incident_queries_per_sec"] = clamp(drift(160000, 35000), 120000, 200000)
                db["_incident_deadlocks"] = int(clamp(progress * 8, 0, 8))

            # Payment gateways hit: timeout rate and error rate rising
            for pg in self._incident_payment_gws:
                pg["_incident_timeout_rate"] = clamp(0.05 + progress * 0.15, 0.05, 0.25)
                pg["_incident_error_rate"] = clamp(0.015 + progress * 0.035, 0.01, 0.06)
                pg["_incident_processing_ms"] = clamp(285 + progress * 450, 285, 800)

            # Fraud detection model latency rising (backlog from slow DB queries)
            for fraud in self._incident_fraud_nodes:
                fraud["_incident_model_latency"] = clamp(78 + progress * 95, 78, 180)
                fraud["_incident_transactions_scored"] = clamp(drift(12500 - progress * 3500, 2000), 8000, 15000)

            # Cache hit rate degrading further
            for cache in self._incident_caches:
                cache["_incident_hit_rate"] = clamp(87.5 - progress * 8, 78, 90)

        # === PHASE 3: CASCADING (Multiple services failing) ===
        elif phase == "cascading":
            progress = phase_tick / self.CASCADING_TICKS

            # DB still lagged
            for db in self._incident_dbs:
                db["_incident_replication_lag"] = clamp(random.gauss(340, 50), 280, 420)
                db["_incident_connection_pool_pct"] = clamp(85 + progress * 10, 80, 96)

            # Payment gateways remain degraded
            for pg in self._incident_payment_gws:
                pg["_incident_timeout_rate"] = clamp(0.20 + progress * 0.08, 0.15, 0.35)
                pg["_incident_error_rate"] = clamp(0.05 + progress * 0.03, 0.04, 0.09)
                pg["_incident_processing_ms"] = clamp(800 + progress * 400, 700, 1200)

            # Fraud detection severely impacted
            for fraud in self._incident_fraud_nodes:
                fraud["_incident_model_latency"] = clamp(random.gauss(200, 45), 140, 280)
                fraud["_incident_transactions_scored"] = clamp(8000 - progress * 3000, 4000, 10000)
                fraud["_incident_false_positive_rate"] = clamp(1.2 + progress * 0.8, 1.0, 2.2)

            # Cache hit rate critically low
            for cache in self._incident_caches:
                cache["_incident_hit_rate"] = clamp(78 - progress * 15, 60, 80)
                cache["_incident_eviction_rate"] = clamp(45 + progress * 30, 40, 80)

        # === PHASE 4: RECOVERING (Services recover in reverse order) ===
        elif phase == "recovering":
            progress = phase_tick / self.RECOVERY_TICKS

            # DB replication catches up first: 340ms → 85ms
            for db in self._incident_dbs:
                db["_incident_replication_lag"] = 340 - progress * 255
                db["_incident_queries_per_sec"] = 140000 + progress * 45000
                db["_incident_connection_pool_pct"] = 93 - progress * 25
                db["_incident_deadlocks"] = int(8 - progress * 8)

            # Payment gateways recover slower (need successful transactions to rebuild)
            if progress < 0.5:
                # First half: still degraded
                for pg in self._incident_payment_gws:
                    pg["_incident_timeout_rate"] = 0.25 - progress * 0.15
                    pg["_incident_error_rate"] = 0.08 - progress * 0.05
                    pg["_incident_processing_ms"] = 1100 - progress * 400
            else:
                # Second half: recovering quickly
                recovery_progress = (progress - 0.5) / 0.5
                for pg in self._incident_payment_gws:
                    pg["_incident_timeout_rate"] = clamp(0.10 - recovery_progress * 0.10, 0, 0.15)
                    pg["_incident_error_rate"] = clamp(0.03 - recovery_progress * 0.025, 0, 0.05)
                    pg["_incident_processing_ms"] = clamp(700 - recovery_progress * 415, 285, 750)

            # Fraud detection recovers with DB and payment latency
            for fraud in self._incident_fraud_nodes:
                fraud["_incident_model_latency"] = 200 - progress * 125
                fraud["_incident_transactions_scored"] = 5000 + progress * 7500
                fraud["_incident_false_positive_rate"] = 2.0 - progress * 0.8

            # Cache hit rate recovers as eviction rates normalize
            for cache in self._incident_caches:
                cache["_incident_hit_rate"] = 63 + progress * 30
                cache["_incident_eviction_rate"] = 75 - progress * 30
