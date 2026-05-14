"""
WiFi-to-Device Cascade Incident Plugin for Healthcare Vertical

Simulates the Floor 3 East WiFi AP outage → infusion pump cascade failure
incident. This plugin demonstrates correlated failures where network
infrastructure issues cascade to device connectivity, which is captured
by monitoring and investigable through metrics.

The incident plays out in distinct phases:
  1. ramp_up (6 ticks / 1m30s):    AP channel utilization climbing, pumps unaffected
  2. saturated (8 ticks / 2m):     Channel maxed, pump signals START degrading (staggered)
  3. outage (6 ticks / 1m30s):     Pumps offline, AP still saturated
  4. recovering (10 ticks / 2m30s):AP fixed, pumps reconnecting

The key insight: pumps stay healthy during ramp_up, but signal degrades AFTER
AP saturates. This temporal lag is what allows AI-driven RCA to discover the
causality: "AP saturation caused pump disconnects, not the reverse."

Implementation note: the engine passes DeviceProfile dataclass instances
(not dicts) into on_tick. We use getattr-with-dict-fallback accessors so
the same code works regardless of which shape the engine uses, mirroring
the pattern used by the BD and Quest overlay plugins. All metric mutations
go through `device.state[<metric_name>]` -- that is what the engine reads
when publishing to OTel.
"""

import random
import logging
from typing import Any, Dict, List, Optional

from dd_demo_toolkit.simulator.plugins import IncidentPlugin

logger = logging.getLogger("wifi_cascade_incident")


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def drift(value: float, magnitude: float = 1.0, bias: float = 0.0) -> float:
    return value + random.gauss(bias, magnitude)


class WiFiCascadeIncident(IncidentPlugin):
    """
    Simulates a Floor 3 East WiFi AP outage cascading to infusion pump failures.

    This is the primary incident scenario for the healthcare vertical,
    demonstrating how network infrastructure issues manifest as device
    connectivity failures.
    """

    RAMP_TICKS = 6          # 1m30s: AP starts getting congested
    SATURATED_TICKS = 8     # 2m:    Channel saturated, pumps start degrading
    OUTAGE_TICKS = 6        # 1m30s: Pumps offline
    RECOVERY_TICKS = 10     # 2m30s: Recovery phase
    EVENT_TICKS = RAMP_TICKS + SATURATED_TICKS + OUTAGE_TICKS + RECOVERY_TICKS

    INCIDENT_FLOOR = "3"
    INCIDENT_WING = "East"

    # Metric names the engine publishes for the affected device types.
    AP_CHANNEL_UTIL_METRIC = "hospital.network.channel_utilization_pct"
    AP_RETRANSMIT_METRIC = "hospital.network.retransmit_pct"
    DEVICE_SIGNAL_METRIC = "hospital.device.signal_strength_dbm"
    DEVICE_ONLINE_METRIC = "hospital.device.online"

    def __init__(self):
        self._ticks_until_next = random.randint(20, 40)
        self._active_tick: Optional[int] = None
        self._incident_pumps: List[Any] = []
        self._incident_aps: List[Any] = []

        logger.info(
            "WiFi Cascade Incident initialized. First incident in ~%d min",
            self._ticks_until_next * 15 // 60,
        )

    def on_tick(self, tick_count: int, fleet: List[Any], engine: Any) -> None:
        if not self._incident_pumps and not self._incident_aps:
            for d in fleet:
                if self._matches_target(d, "infusion_pump"):
                    self._incident_pumps.append(d)
                elif self._matches_target(d, "wireless_ap"):
                    self._incident_aps.append(d)
            if self._incident_pumps or self._incident_aps:
                logger.info(
                    "Indexed incident devices: %d pumps, %d APs on Floor %s %s",
                    len(self._incident_pumps), len(self._incident_aps),
                    self.INCIDENT_FLOOR, self.INCIDENT_WING,
                )

        self._advance_incident_clock()
        phase, phase_tick = self._get_incident_phase()

        if phase != "normal":
            self._apply_overrides(phase, phase_tick)
            online_count = sum(
                1 for p in self._incident_pumps
                if self._get_state(p, self.DEVICE_ONLINE_METRIC) >= 0.5
            )
            logger.info(
                "INCIDENT [%s t=%d] Floor %s %s: pumps_online=%d/%d",
                phase, phase_tick,
                self.INCIDENT_FLOOR, self.INCIDENT_WING,
                online_count, len(self._incident_pumps),
            )

    def get_incident_name(self) -> str:
        return "Floor 3 East WiFi AP Outage → Infusion Pump Cascade"

    def reset(self) -> None:
        self._ticks_until_next = random.randint(20, 40)
        self._active_tick = None
        self._incident_pumps = []
        self._incident_aps = []

    # === Private helpers (getattr/dict-fallback for DeviceProfile compat) ===

    def _device_type(self, device: Any) -> Optional[str]:
        return getattr(device, "type", None) or (
            device.get("device_type") if isinstance(device, dict) else None
        )

    def _device_location(self, device: Any) -> Dict[str, str]:
        loc = getattr(device, "location", None)
        if loc is None and isinstance(device, dict):
            loc = {
                "floor": device.get("floor"),
                "wing": device.get("wing"),
                "department": device.get("department"),
            }
        return loc or {}

    def _matches_target(self, device: Any, expected_type: str) -> bool:
        if self._device_type(device) != expected_type:
            return False
        loc = self._device_location(device)
        return (
            loc.get("floor") == self.INCIDENT_FLOOR
            and loc.get("wing") == self.INCIDENT_WING
        )

    def _set_state(self, device: Any, metric: str, value: float) -> None:
        state = getattr(device, "state", None)
        if state is None and isinstance(device, dict):
            state = device.setdefault("state", {})
        if state is not None:
            state[metric] = value

    def _get_state(self, device: Any, metric: str, default: float = 0.0) -> float:
        state = getattr(device, "state", None)
        if state is None and isinstance(device, dict):
            state = device.get("state") or {}
        if state is None:
            return default
        return state.get(metric, default)

    # === Phase machine ===

    def _get_incident_phase(self) -> tuple:
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
        if self._active_tick is not None:
            self._active_tick += 1
            if self._active_tick >= self.EVENT_TICKS:
                self._active_tick = None
                self._ticks_until_next = random.randint(50, 70)
                logger.info(
                    "Incident complete. Next incident in ~%d min",
                    self._ticks_until_next * 15 // 60,
                )
        else:
            self._ticks_until_next -= 1
            if self._ticks_until_next <= 0:
                self._active_tick = 0
                logger.info(
                    "INCIDENT STARTING: Floor %s %s",
                    self.INCIDENT_FLOOR, self.INCIDENT_WING,
                )

    # === Overrides — writes go into device.state[<metric>] so the engine
    # === actually publishes the mutated values.

    def _apply_overrides(self, phase: str, phase_tick: int) -> None:
        # ----- APs -----
        for ap in self._incident_aps:
            if phase == "ramp_up":
                progress = phase_tick / self.RAMP_TICKS
                self._set_state(ap, self.AP_CHANNEL_UTIL_METRIC,
                                35 + progress * 35)
                self._set_state(ap, self.AP_RETRANSMIT_METRIC,
                                2.0 + progress * 4.0)

            elif phase == "saturated":
                self._set_state(ap, self.AP_CHANNEL_UTIL_METRIC,
                                clamp(random.gauss(88, 5), 75, 99))
                self._set_state(ap, self.AP_RETRANSMIT_METRIC,
                                clamp(random.gauss(12, 3), 6, 25))

            elif phase == "outage":
                self._set_state(ap, self.AP_CHANNEL_UTIL_METRIC,
                                clamp(random.gauss(92, 3), 80, 99))
                self._set_state(ap, self.AP_RETRANSMIT_METRIC,
                                clamp(random.gauss(15, 3), 8, 30))

            elif phase == "recovering":
                progress = phase_tick / self.RECOVERY_TICKS
                self._set_state(ap, self.AP_CHANNEL_UTIL_METRIC,
                                90 - progress * 55)
                self._set_state(ap, self.AP_RETRANSMIT_METRIC,
                                15 - progress * 13)

        # ----- Pumps (KEY: staggered causality) -----
        for i, pump in enumerate(self._incident_pumps):
            if phase == "ramp_up":
                self._set_state(pump, self.DEVICE_SIGNAL_METRIC,
                                clamp(drift(-45.0, 2.0), -55.0, -35.0))
                self._set_state(pump, self.DEVICE_ONLINE_METRIC, 1.0)

            elif phase == "saturated":
                half = self.SATURATED_TICKS // 2
                if phase_tick < half:
                    sig = clamp(drift(-50.0, 3.0), -60.0, -40.0)
                else:
                    progress = (phase_tick - half) / max(1, self.SATURATED_TICKS - half)
                    sig = -55 - progress * 30
                self._set_state(pump, self.DEVICE_SIGNAL_METRIC, sig)
                self._set_state(pump, self.DEVICE_ONLINE_METRIC, 1.0)

            elif phase == "outage":
                self._set_state(pump, self.DEVICE_SIGNAL_METRIC,
                                clamp(random.gauss(-88, 3), -95, -80))
                self._set_state(pump, self.DEVICE_ONLINE_METRIC,
                                1.0 if phase_tick < i else 0.0)

            elif phase == "recovering":
                progress = phase_tick / self.RECOVERY_TICKS
                self._set_state(pump, self.DEVICE_SIGNAL_METRIC,
                                -88 + progress * 43)
                self._set_state(pump, self.DEVICE_ONLINE_METRIC,
                                1.0 if phase_tick > (len(self._incident_pumps) - 1 - i) else 0.0)
