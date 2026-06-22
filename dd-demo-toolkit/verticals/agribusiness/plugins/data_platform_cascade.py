"""
Agribusiness — Cross-Cutting Data-Platform Cascade (flagship: Bunge).

THE demo centerpiece and the AIOps proof point. Models the unique cross-cutting
risk from the briefing: the GCP Bunge Data Platform feeds every revenue app, so
when its CME market-data feed goes stale, FRM pricing degrades and every app
prices off bad data at once — a SILENT failure with no infra alert today.

The cascade drives only two device tiers — the data-platform pipelines (root)
and the FRM pricing engines (symptom). Site / network / SAP / Oracle / host
namespaces are deliberately left to their normal random-walk so the RCA
notebook's "rule out the network / DB / SAP" step is honest and the AI can
isolate the data platform as the leading indicator.

Narrative (matches the agribusiness dashboards, monitors, and the
"Data-Platform Cascade RCA" notebook):

  Phase 1 ramp_up (8 ticks ~2m):
      The CME pricing feed ages — agri.dataplatform.pricing_feed_age_sec drifts
      from ~6s (fresh) toward ~120s; pipeline freshness slips; throughput dips.
      FRM pricing NOT yet visibly impacted — the dangerous, silent window.
  Phase 2 degraded (10 ticks ~2.5m):
      Feed stale (150-260s); pipeline error rate climbs. FRM reacts —
      stale-quote % climbs 0.5% -> ~7%, quote latency and error rate grow.
  Phase 3 outage (12 ticks ~3m):
      Peak. Feed 300-500s stale, stale-quote % ~8-13%. Every app consuming the
      feed (Bunge Mobile, myBunge, FRM, BungeAg, BungeServices — see the APM
      dependency graph) is quoting on bad data.
  Phase 4 recovering (10 ticks ~2.5m):
      Feed refreshes — pricing_feed_age drops first, then stale-quote %, then
      pricing latency / error rate normalize.

4-axis disjointness (currently the only agribusiness plugin; documented so
future plugins stay disjoint per CLAUDE.md §9.3):
  1. Spatial    — production environment, the (global) data-platform + FRM
                  pricing fleet. A future plugin should pick a different
                  environment/region or device set.
  2. Namespace  — only agri.dataplatform.* and agri.pricing.*. agri.site.*,
                  agri.network.*, agri.sap*.*, agri.db.*, agri.host.* are
                  untouched (keeps the "rule out" RCA step clean).
  3. Incident-domain — engine.incident_state key 'data_platform_cascade',
                  incident_domain=data-platform-freshness (matches the monitors).
  4. Temporal   — first fires ~4-6 min after start; re-fires ~10-18 min apart.
"""

import logging
import random
from typing import Any, List, Optional

from dd_demo_toolkit.simulator.plugins import IncidentPlugin

logger = logging.getLogger("data_platform_cascade")


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _drift(value: float, magnitude: float = 1.0, bias: float = 0.0) -> float:
    return value + random.gauss(bias, magnitude)


class DataPlatformCascade(IncidentPlugin):
    """Cross-cutting: stale CME feed in the GCP data platform -> FRM mispricing."""

    RAMP_TICKS = 8
    DEGRADED_TICKS = 10
    OUTAGE_TICKS = 12
    RECOVERY_TICKS = 10
    EVENT_TICKS = RAMP_TICKS + DEGRADED_TICKS + OUTAGE_TICKS + RECOVERY_TICKS

    INCIDENT_ENV = "production"

    # Real metric names registered by the engine (verticals/agribusiness/config.yaml).
    DP_FEED_AGE = "agri.dataplatform.pricing_feed_age_sec"
    DP_FRESHNESS = "agri.dataplatform.pipeline_freshness_sec"
    DP_ERR = "agri.dataplatform.pipeline_error_rate"
    DP_RECORDS = "agri.dataplatform.records_processed_per_sec"

    PR_STALE = "agri.pricing.stale_quote_pct"
    PR_LAT = "agri.pricing.quote_latency_ms"
    PR_ERR = "agri.pricing.error_rate"

    # Clean baselines held during 'normal' so the cascade reads as an obvious
    # anomaly (mirrors the finance/BD plugin pattern).
    BASE_FEED_AGE = 6.0
    BASE_FRESHNESS = 60.0
    BASE_DP_ERR = 0.003
    BASE_RECORDS = 3500.0
    BASE_STALE = 0.5
    BASE_LAT = 80.0
    BASE_PR_ERR = 0.003

    def __init__(self) -> None:
        self._ticks_until_next = random.randint(16, 24)  # ~4-6 min
        self._active_tick: Optional[int] = None
        self._pipelines: List[Any] = []
        self._pricers: List[Any] = []
        logger.info(
            "Data-Platform Cascade initialized. First incident in ~%d min",
            self._ticks_until_next * 15 // 60,
        )

    def get_incident_name(self) -> str:
        return ("Cross-Cutting Data-Platform Cascade: Stale CME Feed -> FRM "
                "Mispricing -> Revenue-App Impact")

    def reset(self) -> None:
        self._ticks_until_next = random.randint(16, 24)
        self._active_tick = None
        self._pipelines = []
        self._pricers = []

    # ------------------------------------------------------------------ tick
    def on_tick(self, tick_count: int, fleet: List[Any], engine: Any) -> None:
        if not self._pipelines and not self._pricers:
            for d in fleet:
                if self._device_location(d, "environment") != self.INCIDENT_ENV:
                    continue
                dtype = self._device_type(d)
                if dtype == "data_pipeline":
                    self._pipelines.append(d)
                elif dtype == "pricing_engine":
                    self._pricers.append(d)
            if self._pipelines or self._pricers:
                logger.info("Indexed %d data pipelines, %d pricing engines (production)",
                            len(self._pipelines), len(self._pricers))

        self._advance_clock()
        phase, phase_tick = self._current_phase()

        if hasattr(engine, "incident_state"):
            if phase == "normal":
                engine.incident_state.pop("data_platform_cascade", None)
            else:
                engine.incident_state["data_platform_cascade"] = {
                    "phase": phase,
                    "phase_tick": phase_tick,
                    "incident_domain": "data-platform-freshness",
                    "signal_chain_root": "stale-cme-pricing-feed",
                    "environment": self.INCIDENT_ENV,
                }

        self._apply(phase, phase_tick)
        if phase != "normal":
            logger.info("DATA-PLATFORM CASCADE [%s t=%d] pipelines=%d pricers=%d",
                        phase, phase_tick, len(self._pipelines), len(self._pricers))

    # -------------------------------------------------- device shape helpers
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

    def _set(self, device: Any, metric: str, value: float) -> None:
        state = getattr(device, "state", None)
        if state is None and isinstance(device, dict):
            state = device.setdefault("state", {})
        if state is not None:
            state[metric] = value

    # ----------------------------------------------------------- phase/clock
    def _current_phase(self) -> tuple:
        if self._active_tick is None:
            return ("normal", 0)
        t = self._active_tick
        a = self.RAMP_TICKS
        b = a + self.DEGRADED_TICKS
        c = b + self.OUTAGE_TICKS
        d = c + self.RECOVERY_TICKS
        if t < a:
            return ("ramp_up", t)
        if t < b:
            return ("degraded", t - a)
        if t < c:
            return ("outage", t - b)
        if t < d:
            return ("recovering", t - c)
        return ("normal", 0)

    def _advance_clock(self) -> None:
        if self._active_tick is not None:
            self._active_tick += 1
            if self._active_tick >= self.EVENT_TICKS:
                self._active_tick = None
                self._ticks_until_next = random.randint(40, 72)
                logger.info("Data-platform cascade complete. Next in ~%d min",
                            self._ticks_until_next * 15 // 60)
        else:
            self._ticks_until_next -= 1
            if self._ticks_until_next <= 0:
                self._active_tick = 0
                logger.info("DATA-PLATFORM CASCADE STARTING (stale CME feed)")

    def _interp(self, lo: float, hi: float, progress: float) -> float:
        return lo + (hi - lo) * progress

    # -------------------------------------------------------------- overrides
    def _apply(self, phase: str, t: int) -> None:
        if phase == "normal":
            self._hold_pipelines_baseline()
            self._hold_pricers_baseline()
        elif phase == "ramp_up":
            self._phase_ramp_up(t)
        elif phase == "degraded":
            self._phase_degraded(t)
        elif phase == "outage":
            self._phase_outage(t)
        elif phase == "recovering":
            self._phase_recovering(t)

    def _hold_pipelines_baseline(self) -> None:
        for p in self._pipelines:
            self._set(p, self.DP_FEED_AGE, _clamp(_drift(self.BASE_FEED_AGE, 1.5), 2, 28))
            self._set(p, self.DP_FRESHNESS, _clamp(_drift(self.BASE_FRESHNESS, 10), 30, 300))
            self._set(p, self.DP_ERR, _clamp(_drift(self.BASE_DP_ERR, 0.001), 0.001, 0.02))
            self._set(p, self.DP_RECORDS, _clamp(_drift(self.BASE_RECORDS, 250), 500, 5000))

    def _hold_pricers_baseline(self) -> None:
        for p in self._pricers:
            self._set(p, self.PR_STALE, _clamp(_drift(self.BASE_STALE, 0.15), 0.1, 1.8))
            self._set(p, self.PR_LAT, _clamp(_drift(self.BASE_LAT, 8), 40, 210))
            self._set(p, self.PR_ERR, _clamp(_drift(self.BASE_PR_ERR, 0.001), 0.001, 0.009))

    def _phase_ramp_up(self, t: int) -> None:
        # Only the data platform moves; pricing stays clean (silent window).
        progress = (t + 1) / self.RAMP_TICKS
        for p in self._pipelines:
            self._set(p, self.DP_FEED_AGE,
                      _clamp(_drift(self._interp(self.BASE_FEED_AGE, 120.0, progress), 6), 4, 160))
            self._set(p, self.DP_FRESHNESS,
                      _clamp(_drift(self._interp(self.BASE_FRESHNESS, 150.0, progress), 12), 40, 220))
            self._set(p, self.DP_ERR, _clamp(_drift(self.BASE_DP_ERR, 0.0015), 0.001, 0.02))
            self._set(p, self.DP_RECORDS,
                      _clamp(_drift(self._interp(self.BASE_RECORDS, 2800, progress), 250), 1500, 5000))
        self._hold_pricers_baseline()

    def _phase_degraded(self, t: int) -> None:
        progress = (t + 1) / self.DEGRADED_TICKS
        for p in self._pipelines:
            self._set(p, self.DP_FEED_AGE, _clamp(_drift(self._interp(150, 260, progress), 25), 120, 340))
            self._set(p, self.DP_FRESHNESS, _clamp(_drift(self._interp(150, 230, progress), 18), 120, 300))
            self._set(p, self.DP_ERR, _clamp(_drift(self._interp(0.01, 0.035, progress), 0.004), 0.006, 0.06))
            self._set(p, self.DP_RECORDS, _clamp(_drift(self._interp(2800, 1800, progress), 220), 1000, 3500))
        for p in self._pricers:
            self._set(p, self.PR_STALE, _clamp(_drift(self._interp(self.BASE_STALE, 7.0, progress), 0.6), 0.4, 10))
            self._set(p, self.PR_LAT, _clamp(_drift(self._interp(self.BASE_LAT, 210.0, progress), 18), 60, 320))
            self._set(p, self.PR_ERR, _clamp(_drift(self._interp(self.BASE_PR_ERR, 0.02, progress), 0.003), 0.002, 0.05))

    def _phase_outage(self, t: int) -> None:
        progress = (t + 1) / self.OUTAGE_TICKS
        for p in self._pipelines:
            self._set(p, self.DP_FEED_AGE, _clamp(_drift(self._interp(300, 480, progress), 40), 260, 620))
            self._set(p, self.DP_FRESHNESS, _clamp(_drift(self._interp(230, 285, progress), 18), 200, 320))
            self._set(p, self.DP_ERR, _clamp(_drift(self._interp(0.035, 0.06, progress), 0.006), 0.02, 0.09))
            self._set(p, self.DP_RECORDS, _clamp(_drift(self._interp(1800, 900, progress), 200), 400, 2400))
        for p in self._pricers:
            self._set(p, self.PR_STALE, _clamp(_drift(self._interp(7.0, 12.5, progress), 0.8), 5, 16))
            self._set(p, self.PR_LAT, _clamp(_drift(self._interp(210, 360, progress), 25), 160, 460))
            self._set(p, self.PR_ERR, _clamp(_drift(self._interp(0.02, 0.045, progress), 0.004), 0.015, 0.07))

    def _phase_recovering(self, t: int) -> None:
        progress = (t + 1) / self.RECOVERY_TICKS
        # Feed refreshes first; pricing trails.
        for p in self._pipelines:
            self._set(p, self.DP_FEED_AGE, _clamp(_drift(self._interp(420, self.BASE_FEED_AGE, progress), 25), 4, 520))
            self._set(p, self.DP_FRESHNESS, _clamp(_drift(self._interp(280, self.BASE_FRESHNESS, progress), 18), 40, 300))
            self._set(p, self.DP_ERR, _clamp(_drift(self._interp(0.055, self.BASE_DP_ERR, progress), 0.005), 0.001, 0.07))
            self._set(p, self.DP_RECORDS, _clamp(_drift(self._interp(1000, self.BASE_RECORDS, progress), 250), 500, 5000))
        for p in self._pricers:
            self._set(p, self.PR_STALE, _clamp(_drift(self._interp(12.0, self.BASE_STALE, progress), 0.8), 0.3, 14))
            self._set(p, self.PR_LAT, _clamp(_drift(self._interp(340, self.BASE_LAT, progress), 20), 50, 400))
            self._set(p, self.PR_ERR, _clamp(_drift(self._interp(0.04, self.BASE_PR_ERR, progress), 0.004), 0.002, 0.06))
