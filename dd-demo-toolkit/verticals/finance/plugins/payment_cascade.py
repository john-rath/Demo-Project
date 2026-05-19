"""
Finance — Payment Processing Cascade.

DB Replication Lag → Payment Timeouts → Fraud Detection Backlog →
Cache Eviction Storm. Rewritten 2026-05-19 to follow the BD-plugin
pattern (DeviceProfile attribute access + `device.state[<real_metric>]`
writes). The pre-rewrite version assumed devices were dicts and wrote
to ad-hoc `_incident_*` keys that didn't match any registered
instrument — it logged errors every tick and produced no signal.

Disjoint from the EY overlay cascade along the four axes:

  1. Spatial — only us-east-1 / production database_cluster,
     payment_gateway, fraud_detection_node, cache_cluster devices.
     EY plugin targets feature_pipeline_node + llm_eval_node fleet-wide;
     zero device overlap.
  2. Metric namespace — only `finserv.database_cluster.*`,
     `finserv.payment_gateway.*`, `finserv.fraud_detection_node.*`,
     `finserv.cache_cluster.*`. EY uses `finserv.ai_data.*` and
     `finserv.llm_eval.*` — zero metric overlap.
  3. Incident-domain tag — emits `incident_domain=payment-cascade` into
     engine.incident_state. EY uses `ai-eval-pipeline`.
  4. Time delta — first cascade fires ~15-20 min after simulator start
     (random.randint(60, 80) ticks * 15s). EY fires at ~75s. The two
     stories are temporally separated.

Cascade narrative (matches the existing finance dashboards / monitors):

  Phase 1 — ramp_up (8 ticks ≈ 2m):
      DB replication lag climbs from 85ms baseline toward 350ms.
      Connection pool tightens. Nothing downstream visible yet.

  Phase 2 — degraded (10 ticks ≈ 2m30s):
      Payment gateway processing latency climbs 285→700ms, decline rate
      rises, timeouts emerge. Fraud detection model latency starts to
      grow as transactions queue against the slow DB. Cache hit rate
      degrades (more cold reads).

  Phase 3 — cascading (12 ticks ≈ 3m):
      Full multi-service degradation. DB still bad. Payments 800-1200ms
      and visibly timing out. Fraud detection latency 200ms+, fewer
      transactions scored. Cache hit rate collapses to ~63%.

  Phase 4 — recovering (10 ticks ≈ 2m30s):
      DB failover completes — lag drains first, then payments normalize,
      then fraud detection catches up, then cache warms back up.
"""

import logging
import random
from typing import Any, List, Optional

from dd_demo_toolkit.simulator.plugins import IncidentPlugin

logger = logging.getLogger("payment_cascade_incident")


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _drift(value: float, magnitude: float = 1.0, bias: float = 0.0) -> float:
    return value + random.gauss(bias, magnitude)


class PaymentProcessingCascade(IncidentPlugin):
    """
    Finance payment-processing cascade in us-east-1 / production.
    """

    # Phase durations (15s/tick → ~10 min full cascade)
    RAMP_TICKS = 8           # 2m  — DB lag climbing
    DEGRADED_TICKS = 10      # 2m30 — payment latency emerges
    CASCADING_TICKS = 12     # 3m  — fraud + cache impacted
    RECOVERY_TICKS = 10      # 2m30 — services recover in reverse

    EVENT_TICKS = RAMP_TICKS + DEGRADED_TICKS + CASCADING_TICKS + RECOVERY_TICKS

    # Spatial scope — us-east-1 production only.
    INCIDENT_REGION = "us-east-1"
    INCIDENT_ENV = "production"

    # --- Real metric names registered by the engine for each device
    # type (from verticals/finance/config.yaml). Writes go to
    # device.state[<name>], which the engine reads when emitting metrics.
    DB_LAG_METRIC = "finserv.database_cluster.replication_lag_ms"
    DB_CONN_POOL_METRIC = "finserv.database_cluster.connection_pool_pct"
    DB_QUERIES_METRIC = "finserv.database_cluster.queries_per_sec"
    DB_DEADLOCKS_METRIC = "finserv.database_cluster.deadlocks_total"

    PG_PROCESSING_METRIC = "finserv.payment_gateway.avg_processing_ms"
    PG_TIMEOUT_METRIC = "finserv.payment_gateway.timeout_rate_pct"
    PG_DECLINE_METRIC = "finserv.payment_gateway.decline_rate_pct"
    PG_TPS_METRIC = "finserv.payment_gateway.transactions_per_sec"

    FRAUD_LAT_METRIC = "finserv.fraud_detection_node.model_latency_ms"
    FRAUD_SCORED_METRIC = "finserv.fraud_detection_node.transactions_scored_per_sec"
    FRAUD_FP_METRIC = "finserv.fraud_detection_node.false_positive_rate_pct"
    FRAUD_ALERTS_METRIC = "finserv.fraud_detection_node.alerts_generated"

    CACHE_HIT_METRIC = "finserv.cache_cluster.hit_rate_pct"
    CACHE_EVICT_METRIC = "finserv.cache_cluster.eviction_rate"
    CACHE_MEM_METRIC = "finserv.cache_cluster.memory_used_pct"

    # Clean baselines held during the 'normal' phase so the cascade
    # spike reads as an obvious anomaly. Mirrors the BD plugin pattern —
    # without this, the engine's gauss random walk drifts every metric
    # toward its declared range midpoint, which for some of these is
    # close to the cascade peak (replication_lag midpoint = ~55ms;
    # cascade peak ~350ms; tolerable, but holding the floor low keeps
    # the delta clean).
    BASELINE_DB_LAG_MS = 85.0
    BASELINE_DB_CONN_PCT = 68.0
    BASELINE_PG_PROCESSING_MS = 285.0
    BASELINE_PG_TIMEOUT_PCT = 0.05
    BASELINE_PG_DECLINE_PCT = 2.1
    BASELINE_FRAUD_LAT_MS = 78.0
    BASELINE_FRAUD_FP_PCT = 1.2
    BASELINE_CACHE_HIT_PCT = 92.5
    BASELINE_CACHE_EVICT = 45.0

    def __init__(self) -> None:
        # First cascade fires ~15-20 min after start so it's temporally
        # separated from the EY overlay cascade (fires at ~75s).
        self._ticks_until_next = random.randint(60, 80)
        self._active_tick: Optional[int] = None
        self._dbs: List[Any] = []
        self._payments: List[Any] = []
        self._fraud: List[Any] = []
        self._caches: List[Any] = []

        logger.info(
            "Payment Cascade Incident initialized. First incident in ~%d min",
            self._ticks_until_next * 15 // 60,
        )

    def get_incident_name(self) -> str:
        return (
            "Payment Processing Cascade: DB Replication Lag → Payment "
            "Timeouts → Fraud Detection Backlog → Cache Eviction Storm"
        )

    def reset(self) -> None:
        self._ticks_until_next = random.randint(60, 80)
        self._active_tick = None
        self._dbs = []
        self._payments = []
        self._fraud = []
        self._caches = []

    # ------------------------------------------------------------------
    # Tick entry point
    # ------------------------------------------------------------------

    def on_tick(
        self,
        tick_count: int,
        fleet: List[Any],
        engine: Any,
    ) -> None:
        if not self._dbs and not self._payments and not self._fraud and not self._caches:
            for d in fleet:
                if not self._is_in_scope(d):
                    continue
                dtype = self._device_type(d)
                if dtype == "database_cluster":
                    self._dbs.append(d)
                elif dtype == "payment_gateway":
                    self._payments.append(d)
                elif dtype == "fraud_detection_node":
                    self._fraud.append(d)
                elif dtype == "cache_cluster":
                    self._caches.append(d)
            if self._dbs or self._payments or self._fraud or self._caches:
                logger.info(
                    "Indexed devices in %s/%s: %d DBs, %d payment gws, "
                    "%d fraud nodes, %d caches",
                    self.INCIDENT_REGION, self.INCIDENT_ENV,
                    len(self._dbs), len(self._payments),
                    len(self._fraud), len(self._caches),
                )

        self._advance_clock()
        phase, phase_tick = self._current_phase()

        if hasattr(engine, "incident_state"):
            if phase == "normal":
                engine.incident_state.pop("payment_cascade", None)
            else:
                engine.incident_state["payment_cascade"] = {
                    "phase": phase,
                    "phase_tick": phase_tick,
                    "incident_domain": "payment-cascade",
                    "signal_chain_root": "db-replication-lag",
                    "region": self.INCIDENT_REGION,
                    "environment": self.INCIDENT_ENV,
                }

        self._apply_overrides(phase, phase_tick)
        if phase != "normal":
            logger.info(
                "PAYMENT CASCADE [%s t=%d] %s/%s: dbs=%d payments=%d fraud=%d caches=%d",
                phase, phase_tick,
                self.INCIDENT_REGION, self.INCIDENT_ENV,
                len(self._dbs), len(self._payments),
                len(self._fraud), len(self._caches),
            )

    # ------------------------------------------------------------------
    # Device shape helpers — work for both DeviceProfile dataclass and
    # legacy dict device representations.
    # ------------------------------------------------------------------

    def _device_type(self, device: Any) -> Optional[str]:
        return getattr(device, "type", None) or (
            device.get("device_type") if isinstance(device, dict) else None
        )

    def _device_location(self, device: Any, key: str) -> Optional[str]:
        loc = getattr(device, "location", None)
        if loc is None and isinstance(device, dict):
            loc = device
        if not isinstance(loc, dict):
            return None
        return loc.get(key)

    def _is_in_scope(self, device: Any) -> bool:
        return (
            self._device_location(device, "region") == self.INCIDENT_REGION
            and self._device_location(device, "environment") == self.INCIDENT_ENV
        )

    def _set_state(self, device: Any, metric: str, value: float) -> None:
        state = getattr(device, "state", None)
        if state is None and isinstance(device, dict):
            state = device.setdefault("state", {})
        if state is not None:
            state[metric] = value

    # ------------------------------------------------------------------
    # Phase / clock
    # ------------------------------------------------------------------

    def _current_phase(self) -> tuple:
        if self._active_tick is None:
            return ("normal", 0)
        t = self._active_tick
        a = self.RAMP_TICKS
        b = a + self.DEGRADED_TICKS
        c = b + self.CASCADING_TICKS
        d = c + self.RECOVERY_TICKS
        if t < a:
            return ("ramp_up", t)
        if t < b:
            return ("degraded", t - a)
        if t < c:
            return ("cascading", t - b)
        if t < d:
            return ("recovering", t - c)
        return ("normal", 0)

    def _advance_clock(self) -> None:
        if self._active_tick is not None:
            self._active_tick += 1
            if self._active_tick >= self.EVENT_TICKS:
                self._active_tick = None
                self._ticks_until_next = random.randint(60, 120)
                logger.info(
                    "Payment cascade complete. Next event in ~%d min",
                    self._ticks_until_next * 15 // 60,
                )
        else:
            self._ticks_until_next -= 1
            if self._ticks_until_next <= 0:
                self._active_tick = 0
                logger.info(
                    "PAYMENT CASCADE STARTING in %s/%s",
                    self.INCIDENT_REGION, self.INCIDENT_ENV,
                )

    # ------------------------------------------------------------------
    # Phase overrides
    # ------------------------------------------------------------------

    def _interp(self, lo: float, hi: float, progress: float) -> float:
        return lo + (hi - lo) * progress

    def _apply_overrides(self, phase: str, phase_tick: int) -> None:
        if phase == "normal":
            self._hold_baseline()
            return
        if phase == "ramp_up":
            self._phase_ramp_up(phase_tick)
        elif phase == "degraded":
            self._phase_degraded(phase_tick)
        elif phase == "cascading":
            self._phase_cascading(phase_tick)
        elif phase == "recovering":
            self._phase_recovering(phase_tick)

    def _hold_baseline(self) -> None:
        for db in self._dbs:
            self._set_state(db, self.DB_LAG_METRIC,
                            _clamp(_drift(self.BASELINE_DB_LAG_MS, 8), 40, 130))
            self._set_state(db, self.DB_CONN_POOL_METRIC,
                            _clamp(_drift(self.BASELINE_DB_CONN_PCT, 3), 55, 80))
            self._set_state(db, self.DB_QUERIES_METRIC,
                            _clamp(_drift(185000, 12000), 150000, 215000))
            self._set_state(db, self.DB_DEADLOCKS_METRIC, 0)
        for pg in self._payments:
            self._set_state(pg, self.PG_PROCESSING_METRIC,
                            _clamp(_drift(self.BASELINE_PG_PROCESSING_MS, 25), 240, 340))
            self._set_state(pg, self.PG_TIMEOUT_METRIC,
                            _clamp(_drift(self.BASELINE_PG_TIMEOUT_PCT, 0.015), 0.02, 0.10))
            self._set_state(pg, self.PG_DECLINE_METRIC,
                            _clamp(_drift(self.BASELINE_PG_DECLINE_PCT, 0.25), 1.6, 2.6))
            self._set_state(pg, self.PG_TPS_METRIC,
                            _clamp(_drift(8500, 800), 6500, 10500))
        for fraud in self._fraud:
            self._set_state(fraud, self.FRAUD_LAT_METRIC,
                            _clamp(_drift(self.BASELINE_FRAUD_LAT_MS, 8), 60, 105))
            self._set_state(fraud, self.FRAUD_FP_METRIC,
                            _clamp(_drift(self.BASELINE_FRAUD_FP_PCT, 0.15), 0.8, 1.6))
            self._set_state(fraud, self.FRAUD_SCORED_METRIC,
                            _clamp(_drift(12500, 1200), 10000, 15000))
            self._set_state(fraud, self.FRAUD_ALERTS_METRIC,
                            _clamp(_drift(18, 4), 8, 28))
        for cache in self._caches:
            self._set_state(cache, self.CACHE_HIT_METRIC,
                            _clamp(_drift(self.BASELINE_CACHE_HIT_PCT, 1.0), 89, 95))
            self._set_state(cache, self.CACHE_EVICT_METRIC,
                            _clamp(_drift(self.BASELINE_CACHE_EVICT, 6), 30, 60))
            self._set_state(cache, self.CACHE_MEM_METRIC,
                            _clamp(_drift(78, 4), 70, 86))

    def _phase_ramp_up(self, t: int) -> None:
        progress = (t + 1) / self.RAMP_TICKS
        # DB lag climbs from 85ms toward 350ms; connection pool tightens.
        for db in self._dbs:
            lag_target = self._interp(self.BASELINE_DB_LAG_MS, 350.0, progress)
            self._set_state(db, self.DB_LAG_METRIC,
                            _clamp(_drift(lag_target, 12), 60, 420))
            self._set_state(db, self.DB_CONN_POOL_METRIC,
                            _clamp(_drift(self._interp(self.BASELINE_DB_CONN_PCT, 80.0, progress), 3), 60, 90))
            self._set_state(db, self.DB_QUERIES_METRIC,
                            _clamp(_drift(185000, 20000), 150000, 220000))
        # Other services not yet impacted in phase 1.
        self._hold_baseline_payments()
        self._hold_baseline_fraud()
        self._hold_baseline_caches()

    def _phase_degraded(self, t: int) -> None:
        progress = (t + 1) / self.DEGRADED_TICKS
        # DB plateaus high
        for db in self._dbs:
            self._set_state(db, self.DB_LAG_METRIC,
                            _clamp(_drift(320, 35), 250, 400))
            self._set_state(db, self.DB_CONN_POOL_METRIC,
                            _clamp(_drift(82, 4), 70, 92))
            self._set_state(db, self.DB_QUERIES_METRIC,
                            _clamp(_drift(160000, 30000), 120000, 200000))
            self._set_state(db, self.DB_DEADLOCKS_METRIC,
                            int(_clamp(progress * 8, 0, 8)))
        # Payment gateways start hurting
        for pg in self._payments:
            self._set_state(pg, self.PG_PROCESSING_METRIC,
                            _clamp(_drift(self._interp(self.BASELINE_PG_PROCESSING_MS, 700.0, progress), 50), 260, 850))
            self._set_state(pg, self.PG_TIMEOUT_METRIC,
                            _clamp(_drift(self._interp(0.05, 0.20, progress), 0.02), 0.04, 0.30))
            self._set_state(pg, self.PG_DECLINE_METRIC,
                            _clamp(_drift(self._interp(self.BASELINE_PG_DECLINE_PCT, 4.5, progress), 0.3), 1.6, 5.5))
            self._set_state(pg, self.PG_TPS_METRIC,
                            _clamp(_drift(self._interp(8500, 6500, progress), 700), 5500, 9500))
        # Fraud detection backlogs starting
        for fraud in self._fraud:
            self._set_state(fraud, self.FRAUD_LAT_METRIC,
                            _clamp(_drift(self._interp(self.BASELINE_FRAUD_LAT_MS, 170.0, progress), 12), 70, 200))
            self._set_state(fraud, self.FRAUD_SCORED_METRIC,
                            _clamp(_drift(self._interp(12500, 9000, progress), 1500), 7000, 14000))
        # Cache hit rate degrading
        for cache in self._caches:
            self._set_state(cache, self.CACHE_HIT_METRIC,
                            _clamp(_drift(self._interp(self.BASELINE_CACHE_HIT_PCT, 84.0, progress), 1.5), 80, 94))
            self._set_state(cache, self.CACHE_EVICT_METRIC,
                            _clamp(_drift(self._interp(self.BASELINE_CACHE_EVICT, 65.0, progress), 6), 35, 80))

    def _phase_cascading(self, t: int) -> None:
        progress = (t + 1) / self.CASCADING_TICKS
        for db in self._dbs:
            self._set_state(db, self.DB_LAG_METRIC,
                            _clamp(_drift(340, 45), 270, 430))
            self._set_state(db, self.DB_CONN_POOL_METRIC,
                            _clamp(_drift(self._interp(85, 95, progress), 3), 80, 97))
            self._set_state(db, self.DB_DEADLOCKS_METRIC,
                            int(_clamp(_drift(8 + progress * 4, 1.5), 4, 14)))
        for pg in self._payments:
            self._set_state(pg, self.PG_PROCESSING_METRIC,
                            _clamp(_drift(self._interp(800, 1100, progress), 70), 650, 1300))
            self._set_state(pg, self.PG_TIMEOUT_METRIC,
                            _clamp(_drift(self._interp(0.20, 0.30, progress), 0.025), 0.15, 0.38))
            self._set_state(pg, self.PG_DECLINE_METRIC,
                            _clamp(_drift(self._interp(4.5, 6.5, progress), 0.4), 3.5, 7.5))
            self._set_state(pg, self.PG_TPS_METRIC,
                            _clamp(_drift(self._interp(6500, 4500, progress), 600), 3800, 8000))
        for fraud in self._fraud:
            self._set_state(fraud, self.FRAUD_LAT_METRIC,
                            _clamp(_drift(self._interp(170, 220, progress), 15), 140, 280))
            self._set_state(fraud, self.FRAUD_SCORED_METRIC,
                            _clamp(_drift(self._interp(9000, 6500, progress), 1200), 4500, 11000))
            self._set_state(fraud, self.FRAUD_FP_METRIC,
                            _clamp(_drift(self._interp(self.BASELINE_FRAUD_FP_PCT, 2.0, progress), 0.2), 1.0, 2.5))
            self._set_state(fraud, self.FRAUD_ALERTS_METRIC,
                            _clamp(_drift(self._interp(18, 40, progress), 5), 12, 55))
        for cache in self._caches:
            self._set_state(cache, self.CACHE_HIT_METRIC,
                            _clamp(_drift(self._interp(84, 63, progress), 2), 58, 86))
            self._set_state(cache, self.CACHE_EVICT_METRIC,
                            _clamp(_drift(self._interp(65, 95, progress), 8), 50, 110))
            self._set_state(cache, self.CACHE_MEM_METRIC,
                            _clamp(_drift(self._interp(78, 88, progress), 3), 70, 92))

    def _phase_recovering(self, t: int) -> None:
        progress = (t + 1) / self.RECOVERY_TICKS
        # DB recovers first (failover completes)
        for db in self._dbs:
            self._set_state(db, self.DB_LAG_METRIC,
                            _clamp(_drift(self._interp(340, self.BASELINE_DB_LAG_MS, progress), 18), 50, 380))
            self._set_state(db, self.DB_CONN_POOL_METRIC,
                            _clamp(_drift(self._interp(92, self.BASELINE_DB_CONN_PCT, progress), 4), 60, 95))
            self._set_state(db, self.DB_DEADLOCKS_METRIC,
                            int(_clamp(_drift(max(0, 10 - progress * 10), 1), 0, 12)))
        # Payments recover next (slightly behind DB)
        for pg in self._payments:
            self._set_state(pg, self.PG_PROCESSING_METRIC,
                            _clamp(_drift(self._interp(1000, self.BASELINE_PG_PROCESSING_MS, progress), 50), 260, 1100))
            self._set_state(pg, self.PG_TIMEOUT_METRIC,
                            _clamp(_drift(self._interp(0.28, self.BASELINE_PG_TIMEOUT_PCT, progress), 0.02), 0.03, 0.32))
            self._set_state(pg, self.PG_DECLINE_METRIC,
                            _clamp(_drift(self._interp(6, self.BASELINE_PG_DECLINE_PCT, progress), 0.3), 1.7, 6.5))
            self._set_state(pg, self.PG_TPS_METRIC,
                            _clamp(_drift(self._interp(5000, 8500, progress), 700), 4200, 9500))
        # Fraud detection works through backlog
        for fraud in self._fraud:
            self._set_state(fraud, self.FRAUD_LAT_METRIC,
                            _clamp(_drift(self._interp(210, self.BASELINE_FRAUD_LAT_MS, progress), 15), 70, 240))
            self._set_state(fraud, self.FRAUD_SCORED_METRIC,
                            _clamp(_drift(self._interp(7000, 12500, progress), 1200), 6000, 14000))
            self._set_state(fraud, self.FRAUD_FP_METRIC,
                            _clamp(_drift(self._interp(1.9, self.BASELINE_FRAUD_FP_PCT, progress), 0.2), 1.0, 2.2))
        # Cache warms back up
        for cache in self._caches:
            self._set_state(cache, self.CACHE_HIT_METRIC,
                            _clamp(_drift(self._interp(63, self.BASELINE_CACHE_HIT_PCT, progress), 2), 60, 95))
            self._set_state(cache, self.CACHE_EVICT_METRIC,
                            _clamp(_drift(self._interp(95, self.BASELINE_CACHE_EVICT, progress), 6), 35, 105))
            self._set_state(cache, self.CACHE_MEM_METRIC,
                            _clamp(_drift(self._interp(86, 78, progress), 3), 70, 90))

    # ------------------------------------------------------------------
    # Per-bucket baseline holders used during phase 1 when only DB is
    # impacted. Keeps the upstream-only window clean so AI RCA tools
    # can isolate replication lag as the leading indicator.
    # ------------------------------------------------------------------

    def _hold_baseline_payments(self) -> None:
        for pg in self._payments:
            self._set_state(pg, self.PG_PROCESSING_METRIC,
                            _clamp(_drift(self.BASELINE_PG_PROCESSING_MS, 25), 240, 340))
            self._set_state(pg, self.PG_TIMEOUT_METRIC,
                            _clamp(_drift(self.BASELINE_PG_TIMEOUT_PCT, 0.015), 0.02, 0.10))
            self._set_state(pg, self.PG_DECLINE_METRIC,
                            _clamp(_drift(self.BASELINE_PG_DECLINE_PCT, 0.25), 1.6, 2.6))
            self._set_state(pg, self.PG_TPS_METRIC,
                            _clamp(_drift(8500, 800), 6500, 10500))

    def _hold_baseline_fraud(self) -> None:
        for fraud in self._fraud:
            self._set_state(fraud, self.FRAUD_LAT_METRIC,
                            _clamp(_drift(self.BASELINE_FRAUD_LAT_MS, 8), 60, 105))
            self._set_state(fraud, self.FRAUD_FP_METRIC,
                            _clamp(_drift(self.BASELINE_FRAUD_FP_PCT, 0.15), 0.8, 1.6))
            self._set_state(fraud, self.FRAUD_SCORED_METRIC,
                            _clamp(_drift(12500, 1200), 10000, 15000))

    def _hold_baseline_caches(self) -> None:
        for cache in self._caches:
            self._set_state(cache, self.CACHE_HIT_METRIC,
                            _clamp(_drift(self.BASELINE_CACHE_HIT_PCT, 1.0), 89, 95))
            self._set_state(cache, self.CACHE_EVICT_METRIC,
                            _clamp(_drift(self.BASELINE_CACHE_EVICT, 6), 30, 60))
