"""
BD Pyxis MedStation Inventory-Sync Cascade — sub-vertical: bd

This plugin drives the BitsSRE problem pattern for the BD overlay. It is
designed to be DIAGNOSABLE in isolation from the existing Floor 3 East
WiFi/pump cascade (`wifi_cascade.py`) by being explicitly disjoint along
four axes:

  1. **Spatial** — affects every Pyxis cabinet in `department=Pharmacy`
     across all floors and wings. The Pharmacy department is disjoint
     from the WiFi cascade's ED/ICU footprint on Floor 3 East. We
     intentionally do NOT restrict to a single floor/wing because the
     plugin would only mutate ~1 of 18 cabinets in that case, leaving
     the cascade signal drowned out by gauss-random-walk drift on the
     17 unaffected cabinets — which AI RCA tools then misread as an
     ~10–15% volume anomaly instead of the 15× polling spike it
     actually is. Pharmacy-wide scope makes the cascade a clear
     fleet-level anomaly across `department:Pharmacy` aggregates.
  2. **Metric namespace** — only mutates `hospital.pyxis.*` signals, never
     `hospital.device.signal_strength_dbm`, `hospital.network.*`, or any
     pump telemetry. There is zero metric overlap with `wifi_cascade.py`.
  3. **Incident-domain tag** — emits state into `engine.incident_state`
     under `bd_pyxis_outage` with `incident_domain=pharmacy-automation`
     (vs. the WiFi cascade's `network-to-device`). Bits AI SRE filtering
     by that tag will not pick up any WiFi-cascade signal.
  4. **Time delta** — for SE demo cadence, initial idle is 2–6 ticks
     (~30–90 sec) so the cascade fires shortly after simulator start
     instead of forcing the SE to wait 22+ minutes. WiFi cascade idle
     is 20–40 ticks; the BD cascade's 30–50 inter-event idle plus its
     ~10-min active phase keeps it temporally distinct. If you need
     longer separation for an autonomous run, raise the idle bounds.

The plugin also HOLDS BASELINE VALUES during the 'normal' phase on
every Pyxis cabinet in Pharmacy. Without this, gauss random walk pulls
each cabinet's metrics toward its declared range midpoint (e.g.,
inventory_poll_rate_per_min midpoint = 122/min, which is close to the
cascade peak of ~190/min — making the spike invisible). Holding the
baseline at ~12/min keeps the noise floor low and the cascade
unambiguous.

Cascade narrative (see notebook `bd-pyxis-cascade-rca.yaml` for the
full BitsSRE walkthrough):

  Phase 1 — drift_up (8 ticks ≈ 2m):
      A vendor-pushed firmware config raises the inventory polling rate
      on Pyxis cabinets in Pharmacy from ~12/min toward 180/min. Only
      `hospital.pyxis.inventory_poll_rate_per_min` moves; everything
      else looks healthy. (signal_chain: 1-root-cause)

  Phase 2 — upstream_saturation (10 ticks ≈ 2m30s):
      `pyxis-inventory-api` thread pool saturates at the new poll rate.
      `hospital.pyxis.sync_lag_to_inventory_ms` climbs from a 100ms
      baseline into multi-second territory.
      `hospital.pyxis.last_successful_sync_age_sec` starts incrementing
      monotonically. `hospital.pyxis.sync_failures_total` ticks.
      (signal_chain: 2-leading-indicator)

  Phase 3 — dispense_impact (12 ticks ≈ 3m):
      Nurse-visible symptom. `hospital.pyxis.dispense_latency_ms`
      jumps from ~800ms baseline to 2.5–3.5s. Witness-countersign
      latency follows. A small fraction of dispenses fail outright
      (`hospital.pyxis.dispense_failed_total`).
      (signal_chain: 3-symptom)

  Phase 4 — recovery (10 ticks ≈ 2m30s):
      Self-heal workflow rate-limits the poll rate back to 60/min;
      sync lag drains; dispense latency normalizes.
      (signal_chain: 5-recovery)

The plugin only mutates *injected* `_incident_*` shadow keys on the
device dict. The base engine reads `hospital.pyxis.*` from `device.state`
which is populated by the normal drift logic — so we override by writing
the raw value into `device.state[<metric>]` for the duration of the
incident, then let drift resume.
"""

import random
import logging
from typing import Any, Dict, List, Optional

from dd_demo_toolkit.simulator.plugins import IncidentPlugin

logger = logging.getLogger("bd_pyxis_outage_incident")


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _drift(value: float, magnitude: float = 1.0, bias: float = 0.0) -> float:
    return value + random.gauss(bias, magnitude)


class BDPyxisInventorySyncCascade(IncidentPlugin):
    """
    BD Pyxis MedStation inventory-sync polling-storm cascade.

    Targets Pyxis cabinets in `department=Pharmacy` on Floor 1 South,
    chosen specifically to be disjoint from the WiFi cascade's
    Floor 3 East footprint.
    """

    # Phase durations (15s/tick by default → ~10min full cascade)
    DRIFT_UP_TICKS = 8           # 2m: poll rate climbing
    UPSTREAM_SATURATION_TICKS = 10  # 2m30s: sync lag growing
    DISPENSE_IMPACT_TICKS = 12   # 3m: nurse-visible latency
    RECOVERY_TICKS = 10          # 2m30s: workflow rate-limit recovers

    EVENT_TICKS = (
        DRIFT_UP_TICKS
        + UPSTREAM_SATURATION_TICKS
        + DISPENSE_IMPACT_TICKS
        + RECOVERY_TICKS
    )

    # Spatial scope — disjoint from WiFi cascade by DEPARTMENT.
    # Originally restricted to Floor 1 South Pharmacy, which only
    # captured ~1 of 18 Pyxis cabinets and let the cascade signal get
    # drowned out by random-walk drift on the unaffected 17. Now hits
    # ALL Pyxis cabinets in Pharmacy (all floors / all wings) so
    # department-aggregated views show the full 15× poll-rate spike
    # rather than a 10–15% blip. Bifurcation from WiFi cascade is
    # preserved via department + metric namespace + incident_domain
    # tag + temporal offset.
    INCIDENT_DEPARTMENT = "Pharmacy"

    # Baseline values held during the "normal" phase so the cabinets
    # don't random-walk back toward each metric's midpoint between
    # cascades. Without this, the cascade spike is invisible against
    # a noisy baseline. Compare to metric ranges in bd.yaml — these
    # baselines anchor the LOWER end of each range so the cascade
    # peak (set during active phases) reads as a clear anomaly.
    BASELINE_POLL_PER_MIN = 12.0
    BASELINE_SYNC_LAG_MS = 120.0
    BASELINE_SYNC_AGE_SEC = 8.0
    BASELINE_DISPENSE_LAT_MS = 800.0
    BASELINE_WITNESS_LAT_MS = 1500.0

    # Metric namespace — Pyxis-only, no overlap with WiFi cascade
    POLL_METRIC = "hospital.pyxis.inventory_poll_rate_per_min"
    SYNC_LAG_METRIC = "hospital.pyxis.sync_lag_to_inventory_ms"
    SYNC_AGE_METRIC = "hospital.pyxis.last_successful_sync_age_sec"
    SYNC_FAIL_METRIC = "hospital.pyxis.sync_failures_total"
    DISPENSE_LAT_METRIC = "hospital.pyxis.dispense_latency_ms"
    DISPENSE_FAIL_METRIC = "hospital.pyxis.dispense_failed_total"
    WITNESS_LAT_METRIC = "hospital.pyxis.witness_countersign_latency_ms"

    def __init__(self) -> None:
        # Demo-friendly initial idle: cascade fires within ~1 minute of
        # simulator start so SE demos don't have to wait 20+ min. WiFi
        # cascade's 20–40 idle keeps the two stories temporally
        # separated on a fresh start because the WiFi cascade fires
        # AFTER the BD cascade completes its first event (BD active
        # phases ~10min total, then 30–50 ticks idle before next event).
        self._ticks_until_next = random.randint(2, 6)
        self._active_tick: Optional[int] = None
        self._incident_pyxis: List[Dict[str, Any]] = []

        logger.info(
            "BD Pyxis cascade initialized. First event in ~%d sec "
            "(department=%s, all floors/wings)",
            self._ticks_until_next * 15,
            self.INCIDENT_DEPARTMENT,
        )

    def get_incident_name(self) -> str:
        return (
            "BD Pyxis Inventory-Sync Polling Storm → Dispense Latency "
            "Cascade (Pharmacy department, fleet-wide)"
        )

    def reset(self) -> None:
        self._ticks_until_next = random.randint(2, 6)
        self._active_tick = None
        self._incident_pyxis = []

    # ------------------------------------------------------------------
    # Tick entry point
    # ------------------------------------------------------------------

    def on_tick(
        self,
        tick_count: int,
        fleet: List[Dict[str, Any]],
        engine: Any,
    ) -> None:
        # Lazy-index target Pyxis cabinets. The engine builds DeviceProfile
        # objects, but `fleet` is iterated by attribute access in the base
        # engine — both forms (dict-like and object) appear in plugin code,
        # so we look up via getattr-then-dict fallback.
        if not self._incident_pyxis:
            for d in fleet:
                if self._matches_target(d):
                    self._incident_pyxis.append(d)
            if self._incident_pyxis:
                logger.info(
                    "Indexed %d Pyxis MedStation cabinets in %s "
                    "(fleet-wide, all floors/wings)",
                    len(self._incident_pyxis),
                    self.INCIDENT_DEPARTMENT,
                )

        self._advance_clock()
        phase, phase_tick = self._current_phase()

        # Publish phase to the engine's shared incident_state so other
        # subsystems (and BitsSRE-style queries that read it via tags)
        # can see the active narrative.
        if hasattr(engine, "incident_state"):
            if phase == "normal":
                engine.incident_state.pop("bd_pyxis_outage", None)
            else:
                engine.incident_state["bd_pyxis_outage"] = {
                    "phase": phase,
                    "phase_tick": phase_tick,
                    "incident_domain": "pharmacy-automation",
                    "signal_chain_root": "pyxis-poll-storm",
                    "department": self.INCIDENT_DEPARTMENT,
                }

        # Apply overrides EVERY tick — including during 'normal' phase.
        # During normal we hold metrics at clean baselines so the
        # subsequent cascade spike reads as an obvious anomaly to AI
        # RCA tools. Without this, the engine's gauss random walk pulls
        # values toward each metric's range midpoint and the
        # cascade-vs-baseline delta vanishes into noise.
        self._apply_overrides(phase, phase_tick)
        if phase != "normal":
            logger.info(
                "PYXIS CASCADE [%s t=%d] cabinets=%d (department=%s)",
                phase,
                phase_tick,
                len(self._incident_pyxis),
                self.INCIDENT_DEPARTMENT,
            )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _matches_target(self, device: Any) -> bool:
        """Return True iff `device` is a Pyxis cabinet in the Pharmacy
        department. We deliberately do NOT filter by floor/wing — the
        polling-storm cascade affects the entire pharmacy fleet
        because the firmware config refresh is global to BD's
        Pharmogistics integration, not site-specific."""
        # DeviceProfile (dataclass) — attribute access
        dtype = getattr(device, "type", None) or (
            device.get("device_type") if isinstance(device, dict) else None
        )
        if dtype != "pyxis_medstation":
            return False
        loc = getattr(device, "location", None)
        if loc is None and isinstance(device, dict):
            loc = {
                "department": device.get("department"),
            }
        if not loc:
            return False
        return loc.get("department") == self.INCIDENT_DEPARTMENT

    def _current_phase(self) -> tuple:
        if self._active_tick is None:
            return ("normal", 0)
        t = self._active_tick
        a = self.DRIFT_UP_TICKS
        b = a + self.UPSTREAM_SATURATION_TICKS
        c = b + self.DISPENSE_IMPACT_TICKS
        d = c + self.RECOVERY_TICKS
        if t < a:
            return ("drift_up", t)
        if t < b:
            return ("upstream_saturation", t - a)
        if t < c:
            return ("dispense_impact", t - b)
        if t < d:
            return ("recovery", t - c)
        return ("normal", 0)

    def _advance_clock(self) -> None:
        if self._active_tick is not None:
            self._active_tick += 1
            if self._active_tick >= self.EVENT_TICKS:
                self._active_tick = None
                self._ticks_until_next = random.randint(80, 120)
                logger.info(
                    "Pyxis cascade complete. Next event in ~%d min",
                    self._ticks_until_next * 15 // 60,
                )
        else:
            self._ticks_until_next -= 1
            if self._ticks_until_next <= 0:
                self._active_tick = 0
                logger.info(
                    "PYXIS CASCADE STARTING in %s on Floor %s %s",
                    self.INCIDENT_DEPARTMENT,
                    self.INCIDENT_FLOOR,
                    self.INCIDENT_WING,
                )

    def _set_state(self, device: Any, metric: str, value: float) -> None:
        """Write metric value into device.state, supporting both shapes."""
        state = getattr(device, "state", None)
        if state is None and isinstance(device, dict):
            state = device.setdefault("state", {})
        if state is not None:
            state[metric] = value

    def _apply_overrides(self, phase: str, phase_tick: int) -> None:
        for pyxis in self._incident_pyxis:
            if phase == "normal":
                # Hold clean baselines on every Pyxis cabinet in
                # Pharmacy so the next cascade reads as a clear
                # anomaly. Without this, the engine's gauss random
                # walk drifts each cabinet toward its metric range
                # midpoint (which for poll_rate is 122 — almost the
                # cascade peak — so the spike disappears into noise).
                self._set_state(
                    pyxis, self.POLL_METRIC,
                    _clamp(_drift(self.BASELINE_POLL_PER_MIN, 1.5), 6, 22),
                )
                self._set_state(
                    pyxis, self.SYNC_LAG_METRIC,
                    _clamp(_drift(self.BASELINE_SYNC_LAG_MS, 25), 60, 250),
                )
                self._set_state(
                    pyxis, self.SYNC_AGE_METRIC,
                    _clamp(_drift(self.BASELINE_SYNC_AGE_SEC, 2), 0, 25),
                )
                self._set_state(
                    pyxis, self.DISPENSE_LAT_METRIC,
                    _clamp(_drift(self.BASELINE_DISPENSE_LAT_MS, 60), 500, 1100),
                )
                self._set_state(
                    pyxis, self.WITNESS_LAT_METRIC,
                    _clamp(_drift(self.BASELINE_WITNESS_LAT_MS, 200), 800, 2500),
                )

            elif phase == "drift_up":
                progress = phase_tick / self.DRIFT_UP_TICKS
                # Poll rate ramps from ~12/min baseline up to ~180/min
                self._set_state(
                    pyxis, self.POLL_METRIC,
                    _clamp(_drift(12 + progress * 168, 4.0), 8, 220),
                )
                # Sync lag still mostly normal
                self._set_state(
                    pyxis, self.SYNC_LAG_METRIC,
                    _clamp(_drift(120, 30), 60, 400),
                )
                self._set_state(
                    pyxis, self.SYNC_AGE_METRIC,
                    _clamp(_drift(8, 2), 0, 30),
                )
                # Dispense latency normal
                self._set_state(
                    pyxis, self.DISPENSE_LAT_METRIC,
                    _clamp(_drift(800, 80), 500, 1300),
                )

            elif phase == "upstream_saturation":
                progress = phase_tick / self.UPSTREAM_SATURATION_TICKS
                # Poll rate sustained near max
                self._set_state(
                    pyxis, self.POLL_METRIC,
                    _clamp(_drift(190, 10), 160, 230),
                )
                # Sync lag CLIMBS — root-cause-adjacent leading indicator
                self._set_state(
                    pyxis, self.SYNC_LAG_METRIC,
                    _clamp(_drift(800 + progress * 5500, 250), 600, 7000),
                )
                # Last-successful sync age increments monotonically
                self._set_state(
                    pyxis, self.SYNC_AGE_METRIC,
                    _clamp(_drift(20 + progress * 180, 8), 10, 280),
                )
                # Sync failures occasional
                if random.random() < 0.3:
                    self._set_state(
                        pyxis, self.SYNC_FAIL_METRIC,
                        random.randint(1, 2),
                    )
                # Dispense latency still mostly normal — that's the
                # diagnostic gap that lets BitsSRE root-cause UPSTREAM
                # of the user complaint.
                self._set_state(
                    pyxis, self.DISPENSE_LAT_METRIC,
                    _clamp(_drift(1100 + progress * 400, 120), 800, 1800),
                )

            elif phase == "dispense_impact":
                # Sustained saturation; dispense and witness latency spike
                self._set_state(
                    pyxis, self.POLL_METRIC,
                    _clamp(_drift(195, 8), 170, 230),
                )
                self._set_state(
                    pyxis, self.SYNC_LAG_METRIC,
                    _clamp(_drift(6500, 500), 4500, 8000),
                )
                self._set_state(
                    pyxis, self.SYNC_AGE_METRIC,
                    _clamp(_drift(280 + phase_tick * 8, 15), 200, 600),
                )
                self._set_state(
                    pyxis, self.DISPENSE_LAT_METRIC,
                    _clamp(_drift(2900, 350), 2000, 3500),
                )
                self._set_state(
                    pyxis, self.WITNESS_LAT_METRIC,
                    _clamp(_drift(11000, 1200), 6000, 15000),
                )
                # Some dispenses fail outright
                if random.random() < 0.4:
                    self._set_state(
                        pyxis, self.DISPENSE_FAIL_METRIC,
                        random.randint(1, 2),
                    )

            elif phase == "recovery":
                progress = phase_tick / self.RECOVERY_TICKS
                # Workflow rate-limit kicks in: poll rate drops back
                self._set_state(
                    pyxis, self.POLL_METRIC,
                    _clamp(_drift(195 - progress * 135, 8), 50, 220),
                )
                self._set_state(
                    pyxis, self.SYNC_LAG_METRIC,
                    _clamp(_drift(6500 - progress * 6300, 300), 100, 7000),
                )
                self._set_state(
                    pyxis, self.SYNC_AGE_METRIC,
                    _clamp(_drift(300 - progress * 290, 10), 5, 320),
                )
                self._set_state(
                    pyxis, self.DISPENSE_LAT_METRIC,
                    _clamp(_drift(2900 - progress * 2100, 200), 700, 3200),
                )
                self._set_state(
                    pyxis, self.WITNESS_LAT_METRIC,
                    _clamp(_drift(11000 - progress * 9500, 800), 1500, 14000),
                )
