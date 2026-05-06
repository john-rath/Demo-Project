"""
BD Pyxis MedStation Inventory-Sync Cascade — sub-vertical: bd

This plugin drives the BitsSRE problem pattern for the BD overlay. It is
designed to be DIAGNOSABLE in isolation from the existing Floor 3 East
WiFi/pump cascade (`wifi_cascade.py`) by being explicitly disjoint along
four axes:

  1. **Spatial** — fires in `department=Pharmacy` on Floor 1 South, well
     away from the WiFi cascade's Floor 3 East ED/ICU footprint.
  2. **Metric namespace** — only mutates `hospital.pyxis.*` signals, never
     `hospital.device.signal_strength_dbm`, `hospital.network.*`, or any
     pump telemetry. There is zero metric overlap with `wifi_cascade.py`.
  3. **Incident-domain tag** — emits state into `engine.incident_state`
     under `bd_pyxis_outage` with `incident_domain=pharmacy-automation`
     (vs. the WiFi cascade's `network-to-device`). Bits AI SRE filtering
     by that tag will not pick up any WiFi-cascade signal.
  4. **Time delta** — initial idle is 90–130 ticks (vs. WiFi cascade's
     20–40), so on a fresh simulator start the WiFi cascade fires and
     fully recovers BEFORE the Pyxis cascade begins. After the Pyxis
     event ends, idle is 80–120 ticks before the next fire — this keeps
     the two stories temporally separated cycle after cycle.

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

    # Spatial scope — disjoint from WiFi cascade (Floor 3 East ED/ICU)
    INCIDENT_FLOOR = "1"
    INCIDENT_WING = "South"
    INCIDENT_DEPARTMENT = "Pharmacy"

    # Metric namespace — Pyxis-only, no overlap with WiFi cascade
    POLL_METRIC = "hospital.pyxis.inventory_poll_rate_per_min"
    SYNC_LAG_METRIC = "hospital.pyxis.sync_lag_to_inventory_ms"
    SYNC_AGE_METRIC = "hospital.pyxis.last_successful_sync_age_sec"
    SYNC_FAIL_METRIC = "hospital.pyxis.sync_failures_total"
    DISPENSE_LAT_METRIC = "hospital.pyxis.dispense_latency_ms"
    DISPENSE_FAIL_METRIC = "hospital.pyxis.dispense_failed_total"
    WITNESS_LAT_METRIC = "hospital.pyxis.witness_countersign_latency_ms"

    def __init__(self) -> None:
        # Initial idle deliberately longer than WiFi cascade's 20–40 so
        # the two stories don't co-occur on the first run.
        self._ticks_until_next = random.randint(90, 130)
        self._active_tick: Optional[int] = None
        self._incident_pyxis: List[Dict[str, Any]] = []

        logger.info(
            "BD Pyxis cascade initialized. First event in ~%d min "
            "(department=%s, floor=%s, wing=%s)",
            self._ticks_until_next * 15 // 60,
            self.INCIDENT_DEPARTMENT,
            self.INCIDENT_FLOOR,
            self.INCIDENT_WING,
        )

    def get_incident_name(self) -> str:
        return (
            "BD Pyxis Inventory-Sync Polling Storm → Dispense Latency Cascade "
            "(Pharmacy / Floor 1 South)"
        )

    def reset(self) -> None:
        self._ticks_until_next = random.randint(90, 130)
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
                    "Indexed %d Pyxis MedStation cabinets in %s on Floor %s %s",
                    len(self._incident_pyxis),
                    self.INCIDENT_DEPARTMENT,
                    self.INCIDENT_FLOOR,
                    self.INCIDENT_WING,
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
                    "floor": self.INCIDENT_FLOOR,
                    "wing": self.INCIDENT_WING,
                }

        if phase != "normal":
            self._apply_overrides(phase, phase_tick)
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
        """Return True iff `device` is a Pyxis cabinet at the incident site."""
        # DeviceProfile (dataclass) — attribute access
        dtype = getattr(device, "type", None) or (
            device.get("device_type") if isinstance(device, dict) else None
        )
        if dtype != "pyxis_medstation":
            return False
        loc = getattr(device, "location", None)
        if loc is None and isinstance(device, dict):
            loc = {
                "floor": device.get("floor"),
                "wing": device.get("wing"),
                "department": device.get("department"),
            }
        if not loc:
            return False
        return (
            loc.get("floor") == self.INCIDENT_FLOOR
            and loc.get("wing") == self.INCIDENT_WING
            and loc.get("department") == self.INCIDENT_DEPARTMENT
        )

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
            if phase == "drift_up":
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
