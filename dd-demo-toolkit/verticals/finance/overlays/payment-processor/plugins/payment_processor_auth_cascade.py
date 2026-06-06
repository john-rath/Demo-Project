"""
Payment Processor — Authorization Switch Latency Degradation cascade.

Drives the Chapter 2 incident narrative: authorization switch latency degrades
→ fraud switch false positive rate spikes (retry storm) → tokenization vault
utilization climbs → clearing queue backs up → settlement gateway errors.
Recovery: fraud switch thresholds auto-tuned, auth latency drops, clearing drains.

Disjoint from base finance and EY overlay plugins on all four axes:

  Axis         base payment_cascade         EY ey_eval_regression          THIS plugin
  -----------  ---------------------------  ----------------------------   ---------------------------------
  Spatial      us-east-1 / production /     Fleet-wide / all BUs /         ap-southeast-1 / production /
               trading devices              eval + pipeline devices         authorization_switch + fraud_switch
                                                                            + tokenization_vault + clearing_node
                                                                            + settlement_gateway devices
  Namespace    finserv.database_cluster.*   finserv.ai_data.*              finserv.authorization.*
               finserv.payment_gateway.*    finserv.llm_eval.*             finserv.tokenization.*
               finserv.fraud_detection.*                                   finserv.clearing.*
               finserv.cache_cluster.*
  Domain       payment-cascade              ai-eval-pipeline               payment-authorization
  Temporal     randint(60,80) ticks         randint(2,6) ticks             randint(110,130) ticks
  (initial)    ≈ 15–20 min                  ≈ 30–90 s                      ≈ 27–32 min
  Temporal     randint(60,120) ticks        randint(80,120) ticks          randint(150,180) ticks
  (inter-evt)                                                               (won't repeat in 1h demo)

Cascade narrative (matches notebooks.yaml → pp-auth-cascade-investigation):

  Phase 1 — ramp_up (6 ticks ≈ 1m30s):
      Authorization switch decision latency starts climbing from baseline (~65ms)
      toward degraded level (~340ms). Fraud switch FP rate just beginning to move.
      (signal_chain: 1-root-cause)

  Phase 2 — degraded (8 ticks ≈ 2m):
      Auth decline rate spikes as latency causes timeouts. Tokenization vault
      utilization climbs as auth retries drive extra token lookups.
      Clearing queue starts growing.
      (signal_chain: 2-leading-indicator)

  Phase 3 — cascading (10 ticks ≈ 2m30s):
      Settlement gateway errors appear. Clearing queue at maximum depth.
      Tokenization vault latency exceeds SLA threshold.
      (signal_chain: 3-symptom)

  Phase 4 — recovery (8 ticks ≈ 2m):
      Fraud switch thresholds auto-tuned (config rollback). Auth latency drops.
      Clearing queue drains. Settlement gateway errors fall to zero.
      (signal_chain: 5-recovery)

Spatial scope: ap-southeast-1 / production ONLY. All other (region, env) combos
are left at their normal engine-driven state so the per-region series breakout
on the dashboard shows the cascade clearly isolated to one region.
"""

import json
import logging
import os
import random
from typing import Any, List, Optional

from dd_demo_toolkit.simulator.plugins import IncidentPlugin

_CASCADE_STATE_DIR = "/cascade-state"
_PHASE_FILE = "/cascade-state/phase.json"

logger = logging.getLogger("payment_processor_auth_cascade")

# Synthetic Visa/MC/Amex/Discover prefixes — realistic format but not real PANs.
# The card.pan field in each log is picked up by Datadog's built-in
# "Credit Card Number" SDS rule and masked before storage.
_FAKE_PAN_PREFIXES = ["4532", "5425", "3714", "6011"]

_MERCHANTS = [
    "AMZN Mktp US*2K8F9", "UBER* Trip", "APPLE.COM/BILL",
    "NETFLIX.COM", "WHOLEFDS #10456", "DELTA AIR 006-123456789",
    "MARRIOTT INT 07823", "GOOGLE *YouTube Premium",
    "COSTCO WHSE #0432", "SHELL OIL 12345678",
]

_DECLINE_REASONS = [
    "AUTHORIZATION_TIMEOUT",
    "FRAUD_SCORE_EXCEEDED",
    "VELOCITY_LIMIT_REACHED",
    "ISSUER_TIMEOUT",
]


def _fake_pan() -> str:
    prefix = random.choice(_FAKE_PAN_PREFIXES)
    digits = "".join(str(random.randint(0, 9)) for _ in range(12))
    return f"{prefix}-{digits[:4]}-{digits[4:8]}-{digits[8:]}"


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _drift(value: float, magnitude: float = 1.0, bias: float = 0.0) -> float:
    return value + random.gauss(bias, magnitude)


class PaymentProcessorAuthCascade(IncidentPlugin):
    """
    Authorization switch latency → fraud switch retry storm → clearing backlog
    → settlement gateway errors cascade, isolated to ap-southeast-1 production.

    Targets authorization_switch (8 devices), fraud_switch (4 devices),
    tokenization_vault (6 devices), clearing_node (6 devices), and
    settlement_gateway (4 devices) ONLY in ap-southeast-1 / production.
    """

    # Phase durations (15s/tick)
    RAMP_UP_TICKS = 6         # 1m30s — auth latency climbing
    DEGRADED_TICKS = 8        # 2m    — auth decline spike + FP rate up
    CASCADING_TICKS = 10      # 2m30s — settlement errors + queue at max
    RECOVERY_TICKS = 8        # 2m    — config rollback, draining

    EVENT_TICKS = RAMP_UP_TICKS + DEGRADED_TICKS + CASCADING_TICKS + RECOVERY_TICKS

    # Spatial scope
    CASCADE_REGION = "ap-southeast-1"
    CASCADE_ENV = "production"

    # Device types this plugin drives
    TARGET_DEVICE_TYPES = {
        "authorization_switch",
        "fraud_switch",
        "tokenization_vault",
        "clearing_node",
        "settlement_gateway",
    }

    # Metric names — must match declarations in payment-processor.yaml
    AUTH_LATENCY = "finserv.authorization.decision_latency_ms"
    AUTH_DECLINE = "finserv.authorization.decline_rate_pct"
    AUTH_APPROVAL = "finserv.authorization.approval_rate_pct"
    AUTH_THROUGHPUT = "finserv.authorization.throughput_tps"
    FRAUD_LATENCY = "finserv.authorization.fraud_score_latency_ms"
    FRAUD_FP_RATE = "finserv.authorization.false_positive_rate_pct"
    TOKEN_LATENCY = "finserv.tokenization.request_latency_ms"
    TOKEN_UTIL = "finserv.tokenization.vault_utilization_pct"
    TOKEN_ERROR = "finserv.tokenization.error_rate"
    CLEAR_QUEUE = "finserv.clearing.queue_depth"
    CLEAR_LATENCY = "finserv.clearing.processing_latency_ms"
    SETTLE_LATENCY = "finserv.clearing.settlement_latency_ms"
    SETTLE_FAILURES = "finserv.clearing.settlement_failures_total"

    # Healthy baselines (midpoints of ranges in payment-processor.yaml)
    BASE_AUTH_LATENCY = 65.0
    BASE_AUTH_DECLINE = 1.5
    BASE_AUTH_APPROVAL = 98.5
    BASE_FRAUD_LATENCY = 28.0
    BASE_FRAUD_FP_RATE = 1.8
    BASE_TOKEN_LATENCY = 45.0
    BASE_TOKEN_UTIL = 58.0
    BASE_CLEAR_QUEUE = 2000.0
    BASE_CLEAR_LATENCY = 180.0
    BASE_SETTLE_LATENCY = 900.0
    BASE_SETTLE_FAILURES = 0.3

    # Cascade peaks
    PEAK_AUTH_LATENCY = 340.0
    PEAK_AUTH_DECLINE = 12.0
    PEAK_FRAUD_FP_RATE = 9.5
    PEAK_TOKEN_LATENCY = 185.0
    PEAK_TOKEN_UTIL = 87.0
    PEAK_CLEAR_QUEUE = 22000.0
    PEAK_CLEAR_LATENCY = 850.0
    PEAK_SETTLE_LATENCY = 4200.0
    PEAK_SETTLE_FAILURES = 8.0

    def __init__(self) -> None:
        self._ticks_until_next = random.randint(110, 130)
        self._active_tick: Optional[int] = None
        self._auth_switches: List[Any] = []
        self._fraud_switches: List[Any] = []
        self._token_vaults: List[Any] = []
        self._clearing_nodes: List[Any] = []
        self._settlement_gateways: List[Any] = []

        logger.info(
            "Payment processor auth cascade initialized. First event in ~%d min",
            self._ticks_until_next * 15 // 60,
        )

    def get_incident_name(self) -> str:
        return (
            "Payment Processor — Authorization Switch Latency → Fraud Switch Retry Storm "
            "→ Clearing Backlog → Settlement Gateway Errors (ap-southeast-1 production)"
        )

    def reset(self) -> None:
        self._ticks_until_next = random.randint(150, 180)
        self._active_tick = None
        self._auth_switches = []
        self._fraud_switches = []
        self._token_vaults = []
        self._clearing_nodes = []
        self._settlement_gateways = []

    # ------------------------------------------------------------------
    # Tick entry point
    # ------------------------------------------------------------------

    def on_tick(self, tick_count: int, fleet: List[Any], engine: Any) -> None:
        if not self._auth_switches and not self._fraud_switches:
            self._index_fleet(fleet)

        self._advance_clock()
        phase, phase_tick = self._current_phase()

        if hasattr(engine, "incident_state"):
            if phase == "normal":
                engine.incident_state.pop("payment_processor_auth_cascade", None)
            else:
                engine.incident_state["payment_processor_auth_cascade"] = {
                    "phase": phase,
                    "phase_tick": phase_tick,
                    "incident_domain": "payment-authorization",
                    "signal_chain_root": "auth-decision-latency",
                    "region": self.CASCADE_REGION,
                    "environment": self.CASCADE_ENV,
                }

        self._apply_overrides(phase, phase_tick)
        self._inject_tx_logs(phase, engine)
        if phase != "normal":
            logger.info(
                "PP AUTH CASCADE [%s t=%d] auth=%d fraud=%d vault=%d clear=%d settle=%d",
                phase, phase_tick,
                len(self._auth_switches), len(self._fraud_switches),
                len(self._token_vaults), len(self._clearing_nodes),
                len(self._settlement_gateways),
            )

    # ------------------------------------------------------------------
    # Declined-transaction log injection
    # ------------------------------------------------------------------

    def _inject_tx_logs(self, phase: str, engine: Any) -> None:
        """Write one declined-auth transaction log into incident_state each tick.

        Engine.py reads these inside the authorization-engine's active root span
        (_emit_incident_tx_logs), so LoggingHandler auto-injects trace_id/span_id.
        The card.pan field uses synthetic Visa-format data; Datadog's SDS
        Credit Card Number rule redacts it before the log record is indexed.
        """
        if not hasattr(engine, "incident_state"):
            return
        state = engine.incident_state.get("payment_processor_auth_cascade")
        if state is None:
            return
        if phase not in ("degraded", "cascading"):
            state.pop("tx_logs", None)
            return

        pan = _fake_pan()
        amount = round(random.uniform(12.50, 8750.00), 2)
        merchant = random.choice(_MERCHANTS)
        reason = random.choice(_DECLINE_REASONS)

        state["tx_logs"] = [
            {
                "service": "authorization-engine",
                "level": "warning",
                "message": (
                    "Authorization DECLINED pan=%s amount=%.2f merchant=%r "
                    "reason=%s region=%s"
                ) % (pan, amount, merchant, reason, self.CASCADE_REGION),
                "extra": {
                    "card.pan": pan,
                    "transaction.amount": amount,
                    "merchant.name": merchant,
                    "authorization.decline_reason": reason,
                    "authorization.decision": "DECLINED",
                    "region": self.CASCADE_REGION,
                    "device_type": "authorization_switch",
                    "environment": self.CASCADE_ENV,
                },
            }
        ]

    # ------------------------------------------------------------------
    # Fleet indexing — only ap-southeast-1 / production devices
    # ------------------------------------------------------------------

    def _index_fleet(self, fleet: List[Any]) -> None:
        for device in fleet:
            dtype = self._device_type(device)
            if dtype not in self.TARGET_DEVICE_TYPES:
                continue
            if not self._is_in_scope(device):
                continue
            if dtype == "authorization_switch":
                self._auth_switches.append(device)
            elif dtype == "fraud_switch":
                self._fraud_switches.append(device)
            elif dtype == "tokenization_vault":
                self._token_vaults.append(device)
            elif dtype == "clearing_node":
                self._clearing_nodes.append(device)
            elif dtype == "settlement_gateway":
                self._settlement_gateways.append(device)

        logger.info(
            "Indexed fleet for pp auth cascade: auth=%d fraud=%d vault=%d clear=%d settle=%d",
            len(self._auth_switches), len(self._fraud_switches),
            len(self._token_vaults), len(self._clearing_nodes),
            len(self._settlement_gateways),
        )

    def _is_in_scope(self, device: Any) -> bool:
        loc = getattr(device, "location", None) or (
            device.get("location") if isinstance(device, dict) else {}
        ) or {}
        return (
            loc.get("region") == self.CASCADE_REGION
            and loc.get("environment") == self.CASCADE_ENV
        )

    def _device_type(self, device: Any) -> Optional[str]:
        return getattr(device, "type", None) or (
            device.get("type") if isinstance(device, dict) else None
        )

    # ------------------------------------------------------------------
    # Phase / clock
    # ------------------------------------------------------------------

    def _current_phase(self) -> tuple:
        if self._active_tick is None:
            return ("normal", 0)
        t = self._active_tick
        a = self.RAMP_UP_TICKS
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
            return ("recovery", t - c)
        return ("normal", 0)

    def _advance_clock(self) -> None:
        if self._active_tick is not None:
            self._active_tick += 1
            if self._active_tick >= self.EVENT_TICKS:
                self._active_tick = None
                self._ticks_until_next = random.randint(150, 180)
                logger.info(
                    "PP auth cascade complete. Next event in ~%d min",
                    self._ticks_until_next * 15 // 60,
                )
        else:
            self._ticks_until_next -= 1
            if self._ticks_until_next <= 0:
                self._active_tick = 0
                logger.info(
                    "PP AUTH CASCADE STARTING (auth-decision-latency in %s/%s)",
                    self.CASCADE_REGION, self.CASCADE_ENV,
                )
        self._write_phase_file()

    def _write_phase_file(self) -> None:
        """Write current phase to the cascade-state shared volume.

        The authorization-db-worker reads this file to choose degraded query
        patterns in sync with the cascade. Silently no-ops when the volume
        is not mounted (local dev without the payment-processor profile).
        """
        if not os.path.isdir(_CASCADE_STATE_DIR):
            return
        phase, tick = self._current_phase()
        try:
            with open(_PHASE_FILE, "w") as f:
                json.dump({"phase": phase, "tick": tick}, f)
        except OSError:
            pass

    # ------------------------------------------------------------------
    # State helpers
    # ------------------------------------------------------------------

    def _set_state(self, device: Any, metric: str, value: float) -> None:
        state = getattr(device, "state", None)
        if state is None and isinstance(device, dict):
            state = device.setdefault("state", {})
        if state is not None:
            state[metric] = value

    def _interp(self, lo: float, hi: float, progress: float) -> float:
        return lo + (hi - lo) * progress

    # ------------------------------------------------------------------
    # Phase overrides
    # ------------------------------------------------------------------

    def _apply_overrides(self, phase: str, phase_tick: int) -> None:
        if phase == "normal":
            self._hold_baseline()
        elif phase == "ramp_up":
            self._phase_ramp_up(phase_tick)
        elif phase == "degraded":
            self._phase_degraded(phase_tick)
        elif phase == "cascading":
            self._phase_cascading(phase_tick)
        elif phase == "recovery":
            self._phase_recovery(phase_tick)

    def _hold_baseline(self) -> None:
        for d in self._auth_switches:
            self._set_state(d, self.AUTH_LATENCY,
                            _clamp(_drift(self.BASE_AUTH_LATENCY, 4), 20, 100))
            self._set_state(d, self.AUTH_DECLINE,
                            _clamp(_drift(self.BASE_AUTH_DECLINE, 0.1), 0.3, 2.5))
            self._set_state(d, self.AUTH_APPROVAL,
                            _clamp(_drift(self.BASE_AUTH_APPROVAL, 0.1), 97.0, 99.8))
            self._set_state(d, self.AUTH_THROUGHPUT,
                            _clamp(_drift(28000, 800), 12000, 45000))
        for d in self._fraud_switches:
            self._set_state(d, self.FRAUD_LATENCY,
                            _clamp(_drift(self.BASE_FRAUD_LATENCY, 2), 10, 48))
            self._set_state(d, self.FRAUD_FP_RATE,
                            _clamp(_drift(self.BASE_FRAUD_FP_RATE, 0.15), 0.5, 3.0))
        for d in self._token_vaults:
            self._set_state(d, self.TOKEN_LATENCY,
                            _clamp(_drift(self.BASE_TOKEN_LATENCY, 3), 15, 80))
            self._set_state(d, self.TOKEN_UTIL,
                            _clamp(_drift(self.BASE_TOKEN_UTIL, 2.5), 40, 70))
            self._set_state(d, self.TOKEN_ERROR,
                            _clamp(_drift(0.002, 0.0003), 0.0005, 0.004))
        for d in self._clearing_nodes:
            self._set_state(d, self.CLEAR_QUEUE,
                            _clamp(_drift(self.BASE_CLEAR_QUEUE, 150), 200, 5000))
            self._set_state(d, self.CLEAR_LATENCY,
                            _clamp(_drift(self.BASE_CLEAR_LATENCY, 12), 80, 350))
        for d in self._settlement_gateways:
            self._set_state(d, self.SETTLE_LATENCY,
                            _clamp(_drift(self.BASE_SETTLE_LATENCY, 45), 300, 1600))
            self._set_state(d, self.SETTLE_FAILURES,
                            _clamp(_drift(self.BASE_SETTLE_FAILURES, 0.2), 0, 2))

    def _phase_ramp_up(self, t: int) -> None:
        progress = (t + 1) / self.RAMP_UP_TICKS
        # Auth latency starts climbing; everything else near baseline
        for d in self._auth_switches:
            lat = self._interp(self.BASE_AUTH_LATENCY, self.PEAK_AUTH_LATENCY * 0.6, progress)
            self._set_state(d, self.AUTH_LATENCY, _clamp(_drift(lat, 8), 25, 250))
            self._set_state(d, self.AUTH_DECLINE,
                            _clamp(_drift(self.BASE_AUTH_DECLINE + progress * 1.5, 0.2), 0.5, 4.0))
            self._set_state(d, self.AUTH_APPROVAL,
                            _clamp(_drift(self.BASE_AUTH_APPROVAL - progress * 1.5, 0.15), 96.0, 99.5))
        # Fraud switch FP rate just starting to move
        for d in self._fraud_switches:
            fp = self._interp(self.BASE_FRAUD_FP_RATE, self.BASE_FRAUD_FP_RATE * 2.2, progress)
            self._set_state(d, self.FRAUD_FP_RATE, _clamp(_drift(fp, 0.3), 0.8, 5.5))
            self._set_state(d, self.FRAUD_LATENCY,
                            _clamp(_drift(self.BASE_FRAUD_LATENCY + progress * 8, 2), 12, 55))
        self._hold_tokenization_baseline()
        self._hold_clearing_baseline()

    def _phase_degraded(self, t: int) -> None:
        progress = (t + 1) / self.DEGRADED_TICKS
        # Auth fully degraded: high latency, high decline rate
        for d in self._auth_switches:
            lat = self._interp(self.PEAK_AUTH_LATENCY * 0.6, self.PEAK_AUTH_LATENCY, progress)
            self._set_state(d, self.AUTH_LATENCY, _clamp(_drift(lat, 20), 100, 450))
            dec = self._interp(3.0, self.PEAK_AUTH_DECLINE, progress)
            self._set_state(d, self.AUTH_DECLINE, _clamp(_drift(dec, 0.5), 1.5, 14))
            self._set_state(d, self.AUTH_APPROVAL,
                            _clamp(_drift(100 - dec, 0.5), 86, 98.5))
        # Fraud switch FP rate climbing
        for d in self._fraud_switches:
            fp = self._interp(self.BASE_FRAUD_FP_RATE * 2.2, self.PEAK_FRAUD_FP_RATE * 0.8, progress)
            self._set_state(d, self.FRAUD_FP_RATE, _clamp(_drift(fp, 0.5), 2.0, 9.0))
        # Tokenization vault utilization climbing
        for d in self._token_vaults:
            util = self._interp(self.BASE_TOKEN_UTIL, self.PEAK_TOKEN_UTIL * 0.75, progress)
            self._set_state(d, self.TOKEN_UTIL, _clamp(_drift(util, 3), 45, 82))
            lat = self._interp(self.BASE_TOKEN_LATENCY, self.PEAK_TOKEN_LATENCY * 0.6, progress)
            self._set_state(d, self.TOKEN_LATENCY, _clamp(_drift(lat, 6), 20, 140))
        # Clearing queue starting to grow
        for d in self._clearing_nodes:
            q = self._interp(self.BASE_CLEAR_QUEUE, self.PEAK_CLEAR_QUEUE * 0.4, progress)
            self._set_state(d, self.CLEAR_QUEUE, _clamp(_drift(q, 300), 800, 12000))
        self._hold_settlement_baseline()

    def _phase_cascading(self, t: int) -> None:
        progress = (t + 1) / self.CASCADING_TICKS
        # Auth stays fully degraded
        for d in self._auth_switches:
            self._set_state(d, self.AUTH_LATENCY,
                            _clamp(_drift(self.PEAK_AUTH_LATENCY, 25), 200, 500))
            self._set_state(d, self.AUTH_DECLINE,
                            _clamp(_drift(self.PEAK_AUTH_DECLINE, 0.8), 8, 16))
        # Fraud FP rate at peak
        for d in self._fraud_switches:
            self._set_state(d, self.FRAUD_FP_RATE,
                            _clamp(_drift(self.PEAK_FRAUD_FP_RATE, 0.6), 6.0, 12.0))
        # Tokenization at peak
        for d in self._token_vaults:
            self._set_state(d, self.TOKEN_UTIL,
                            _clamp(_drift(self.PEAK_TOKEN_UTIL, 3), 78, 95))
            self._set_state(d, self.TOKEN_LATENCY,
                            _clamp(_drift(self.PEAK_TOKEN_LATENCY, 12), 100, 260))
            self._set_state(d, self.TOKEN_ERROR,
                            _clamp(_drift(0.012, 0.002), 0.005, 0.025))
        # Clearing queue at max, latency climbing
        for d in self._clearing_nodes:
            q = self._interp(self.PEAK_CLEAR_QUEUE * 0.4, self.PEAK_CLEAR_QUEUE, progress)
            self._set_state(d, self.CLEAR_QUEUE, _clamp(_drift(q, 800), 6000, 26000))
            lat = self._interp(self.BASE_CLEAR_LATENCY, self.PEAK_CLEAR_LATENCY, progress)
            self._set_state(d, self.CLEAR_LATENCY, _clamp(_drift(lat, 30), 200, 1000))
        # Settlement gateway errors appearing
        for d in self._settlement_gateways:
            settle = self._interp(self.BASE_SETTLE_LATENCY, self.PEAK_SETTLE_LATENCY, progress)
            self._set_state(d, self.SETTLE_LATENCY, _clamp(_drift(settle, 150), 500, 5500))
            failures = self._interp(0, self.PEAK_SETTLE_FAILURES, progress)
            self._set_state(d, self.SETTLE_FAILURES, _clamp(_drift(failures, 0.5), 0, 12))

    def _phase_recovery(self, t: int) -> None:
        progress = (t + 1) / self.RECOVERY_TICKS
        # Auth recovers first (config rollback)
        for d in self._auth_switches:
            lat = self._interp(self.PEAK_AUTH_LATENCY, self.BASE_AUTH_LATENCY, progress)
            self._set_state(d, self.AUTH_LATENCY, _clamp(_drift(lat, 15), 20, 380))
            dec = self._interp(self.PEAK_AUTH_DECLINE, self.BASE_AUTH_DECLINE, progress)
            self._set_state(d, self.AUTH_DECLINE, _clamp(_drift(dec, 0.3), 0.5, 12))
        # Fraud FP rate drops quickly once retry storm ends
        for d in self._fraud_switches:
            fp = self._interp(self.PEAK_FRAUD_FP_RATE, self.BASE_FRAUD_FP_RATE, progress)
            self._set_state(d, self.FRAUD_FP_RATE, _clamp(_drift(fp, 0.4), 0.8, 9.0))
        # Tokenization recovers with auth
        for d in self._token_vaults:
            util = self._interp(self.PEAK_TOKEN_UTIL, self.BASE_TOKEN_UTIL, progress)
            self._set_state(d, self.TOKEN_UTIL, _clamp(_drift(util, 3), 40, 90))
            lat = self._interp(self.PEAK_TOKEN_LATENCY, self.BASE_TOKEN_LATENCY, progress)
            self._set_state(d, self.TOKEN_LATENCY, _clamp(_drift(lat, 8), 15, 200))
            self._set_state(d, self.TOKEN_ERROR,
                            _clamp(_drift(self.BASE_TOKEN_ERROR_RECOV(progress), 0.001), 0, 0.02))
        # Clearing drains — lags behind auth recovery
        for d in self._clearing_nodes:
            q = self._interp(self.PEAK_CLEAR_QUEUE, self.BASE_CLEAR_QUEUE * 1.5, progress)
            self._set_state(d, self.CLEAR_QUEUE, _clamp(_drift(q, 500), 1000, 22000))
            lat = self._interp(self.PEAK_CLEAR_LATENCY, self.BASE_CLEAR_LATENCY, progress)
            self._set_state(d, self.CLEAR_LATENCY, _clamp(_drift(lat, 25), 80, 850))
        # Settlement errors drop to zero
        for d in self._settlement_gateways:
            settle = self._interp(self.PEAK_SETTLE_LATENCY, self.BASE_SETTLE_LATENCY, progress)
            self._set_state(d, self.SETTLE_LATENCY, _clamp(_drift(settle, 80), 300, 4500))
            failures = self._interp(self.PEAK_SETTLE_FAILURES, 0, progress)
            self._set_state(d, self.SETTLE_FAILURES, _clamp(_drift(failures, 0.3), 0, 8))

    # Helper for token error rate recovery interpolation
    def BASE_TOKEN_ERROR_RECOV(self, progress: float) -> float:
        return self._interp(0.012, 0.002, progress)

    # ------------------------------------------------------------------
    # Baseline holders for devices not yet in cascade phase
    # ------------------------------------------------------------------

    def _hold_tokenization_baseline(self) -> None:
        for d in self._token_vaults:
            self._set_state(d, self.TOKEN_LATENCY,
                            _clamp(_drift(self.BASE_TOKEN_LATENCY, 3), 15, 80))
            self._set_state(d, self.TOKEN_UTIL,
                            _clamp(_drift(self.BASE_TOKEN_UTIL, 2.5), 40, 70))

    def _hold_clearing_baseline(self) -> None:
        for d in self._clearing_nodes:
            self._set_state(d, self.CLEAR_QUEUE,
                            _clamp(_drift(self.BASE_CLEAR_QUEUE, 150), 200, 5000))
            self._set_state(d, self.CLEAR_LATENCY,
                            _clamp(_drift(self.BASE_CLEAR_LATENCY, 12), 80, 350))

    def _hold_settlement_baseline(self) -> None:
        for d in self._settlement_gateways:
            self._set_state(d, self.SETTLE_LATENCY,
                            _clamp(_drift(self.BASE_SETTLE_LATENCY, 45), 300, 1600))
            self._set_state(d, self.SETTLE_FAILURES,
                            _clamp(_drift(self.BASE_SETTLE_FAILURES, 0.2), 0, 2))
