"""
WiFi-to-Device Cascade Incident Plugin for Healthcare Vertical

Simulates the Floor 3 East WiFi AP outage → infusion pump cascade failure incident.
This plugin demonstrates correlated failures where network infrastructure issues
cascade to device connectivity, which is captured by monitoring and investigable
through metrics.

The incident plays out in distinct phases:
  1. ramp_up (6 ticks / 1m30s): AP channel utilization climbing, pumps unaffected
  2. saturated (8 ticks / 2m): Channel maxed, pump signals START degrading (staggered)
  3. outage (6 ticks / 1m30s): Pumps offline, AP still saturated
  4. recovering (10 ticks / 2m30s): AP fixed, pumps reconnecting

The key insight: pumps stay healthy during ramp_up, but signal degrades AFTER
AP saturates. This temporal lag is what allows AI-driven RCA to discover the
causality: "AP saturation caused pump disconnects, not the reverse."
"""

import random
import logging
from typing import Any, Dict, List, Optional

from dd_demo_toolkit.simulator.plugins import IncidentPlugin

logger = logging.getLogger("wifi_cascade_incident")


def clamp(value: float, lo: float, hi: float) -> float:
    """Clamp a value to [lo, hi]."""
    return max(lo, min(hi, value))


def drift(value: float, magnitude: float = 1.0, bias: float = 0.0) -> float:
    """Apply Gaussian drift to a value."""
    return value + random.gauss(bias, magnitude)


class WiFiCascadeIncident(IncidentPlugin):
    """
    Simulates a Floor 3 East WiFi AP outage cascading to infusion pump failures.

    This is the primary incident scenario for the healthcare vertical, demonstrating
    how network infrastructure issues manifest as device connectivity failures.
    """

    # Incident phase durations (in ticks, each tick = 15 seconds by default)
    RAMP_TICKS = 6          # 1m30s: AP starts getting congested
    SATURATED_TICKS = 8     # 2m: Channel saturated, pumps start degrading
    OUTAGE_TICKS = 6        # 1m30s: Pumps offline
    RECOVERY_TICKS = 10     # 2m30s: Recovery phase

    # Total event duration: 30 ticks = 7m30s of visible activity
    EVENT_TICKS = RAMP_TICKS + SATURATED_TICKS + OUTAGE_TICKS + RECOVERY_TICKS

    # Fixed incident location (Floor 3 East)
    INCIDENT_FLOOR = "3"
    INCIDENT_WING = "East"

    def __init__(self):
        """Initialize the incident plugin."""
        self._ticks_until_next = random.randint(20, 40)
        self._active_tick: Optional[int] = None
        self._incident_pumps: List[Dict[str, Any]] = []
        self._incident_aps: List[Dict[str, Any]] = []

        logger.info(
            f"WiFi Cascade Incident initialized. First incident in ~{self._ticks_until_next * 15 // 60} min"
        )

    def on_tick(self, tick_count: int, fleet: List[Dict[str, Any]], engine: Any) -> None:
        """Called on each simulator tick to apply incident overrides."""
        # Index incident devices on first tick
        if tick_count == 0 or not self._incident_pumps:
            self._incident_pumps = [
                d for d in fleet
                if d.get("device_type") == "infusion_pump"
                and d.get("floor") == self.INCIDENT_FLOOR
                and d.get("wing") == self.INCIDENT_WING
            ]
            self._incident_aps = [
                d for d in fleet
                if d.get("device_type") == "wireless_ap"
                and d.get("floor") == self.INCIDENT_FLOOR
                and d.get("wing") == self.INCIDENT_WING
            ]

            if self._incident_pumps or self._incident_aps:
                logger.info(
                    f"Indexed incident devices: {len(self._incident_pumps)} pumps, "
                    f"{len(self._incident_aps)} APs on Floor {self.INCIDENT_FLOOR} {self.INCIDENT_WING}"
                )

        # Advance incident clock and apply overrides
        self._advance_incident_clock()
        phase, phase_tick = self._get_incident_phase()

        if phase != "normal":
            self._apply_overrides(phase, phase_tick)
            logger.info(
                f"INCIDENT [{phase} t={phase_tick}] Floor {self.INCIDENT_FLOOR} {self.INCIDENT_WING}: "
                f"pumps_online={sum(1 for p in self._incident_pumps if p.get('is_online'))}/"
                f"{len(self._incident_pumps)}"
            )

    def get_incident_name(self) -> str:
        """Return human-readable name for this incident."""
        return "Floor 3 East WiFi AP Outage → Infusion Pump Cascade"

    def reset(self) -> None:
        """Reset plugin state."""
        self._ticks_until_next = random.randint(20, 40)
        self._active_tick = None
        self._incident_pumps = []
        self._incident_aps = []

    # === Private methods ===

    def _get_incident_phase(self) -> tuple:
        """Return (phase_name, tick_within_phase) for current state."""
        if self._active_tick is None:
            return ("normal", 0)

        t = self._active_tick
        r = self.RAMP_TICKS
        s = r + self.SATURATED_TICKS
        o = s + self.OUTAGE_TICKS
        e = o + self.RECOVERY_TICKS

        if t < r:
            return ("ramp_up", t)
        elif t < s:
            return ("saturated", t - r)
        elif t < o:
            return ("outage", t - s)
        elif t < e:
            return ("recovering", t - o)
        return ("normal", 0)

    def _advance_incident_clock(self) -> None:
        """Advance the incident state machine each tick."""
        if self._active_tick is not None:
            self._active_tick += 1
            if self._active_tick >= self.EVENT_TICKS:
                self._active_tick = None
                self._ticks_until_next = random.randint(50, 70)
                logger.info(
                    f"Incident complete. Next incident in ~{self._ticks_until_next * 15 // 60} min"
                )
        else:
            self._ticks_until_next -= 1
            if self._ticks_until_next <= 0:
                self._active_tick = 0
                logger.info(f"INCIDENT STARTING: Floor {self.INCIDENT_FLOOR} {self.INCIDENT_WING}")

    def _apply_overrides(self, phase: str, phase_tick: int) -> None:
        """Apply incident state overrides to devices."""
        # AP overrides
        for ap in self._incident_aps:
            if phase == "ramp_up":
                progress = phase_tick / self.RAMP_TICKS
                ap["_incident_channel_util"] = 35 + progress * 35
                ap["_incident_retransmit"] = 2.0 + progress * 4.0

            elif phase == "saturated":
                ap["_incident_channel_util"] = clamp(random.gauss(88, 5), 75, 99)
                ap["_incident_retransmit"] = clamp(random.gauss(12, 3), 6, 25)

            elif phase == "outage":
                ap["_incident_channel_util"] = clamp(random.gauss(92, 3), 80, 99)
                ap["_incident_retransmit"] = clamp(random.gauss(15, 3), 8, 30)

            elif phase == "recovering":
                progress = phase_tick / self.RECOVERY_TICKS
                ap["_incident_channel_util"] = 90 - progress * 55
                ap["_incident_retransmit"] = 15 - progress * 13

        # Pump overrides (KEY: staggered causality)
        for i, pump in enumerate(self._incident_pumps):
            if phase == "ramp_up":
                pump["signal_strength_dbm"] = clamp(drift(-45.0, 2.0), -55.0, -35.0)
                pump["is_online"] = True

            elif phase == "saturated":
                half = self.SATURATED_TICKS // 2
                if phase_tick < half:
                    pump["signal_strength_dbm"] = clamp(drift(-50.0, 3.0), -60.0, -40.0)
                else:
                    progress = (phase_tick - half) / max(1, self.SATURATED_TICKS - half)
                    pump["signal_strength_dbm"] = -55 - progress * 30
                pump["is_online"] = True

            elif phase == "outage":
                pump["signal_strength_dbm"] = clamp(random.gauss(-88, 3), -95, -80)
                pump["is_online"] = phase_tick < i

            elif phase == "recovering":
                progress = phase_tick / self.RECOVERY_TICKS
                pump["signal_strength_dbm"] = -88 + progress * 43
                pump["is_online"] = phase_tick > (len(self._incident_pumps) - 1 - i)
