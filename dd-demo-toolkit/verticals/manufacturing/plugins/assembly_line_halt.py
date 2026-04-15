"""
Assembly Line Halt Incident Plugin for Manufacturing Vertical

Simulates the Assembly-1 robot arm servo degradation → cycle time increase → conveyor jam
→ assembly line halt incident. This plugin demonstrates correlated failures where equipment
degradation cascades to production line stoppage, which is captured by monitoring and
investigable through metrics.

The incident plays out in distinct phases:
  1. ramp_up (6 ticks / 1m30s): Robot arm vibration increasing, servo errors climbing
  2. degrading (8 ticks / 2m): Cycle time increasing, quality defects rising, conveyor backing up
  3. halted (6 ticks / 1m30s): Robot arm stops, conveyor jams, line halted
  4. recovering (10 ticks / 2m30s): Maintenance intervention, equipment restarting

The key insight: robot arm stays operational during ramp_up, but cycle time and defects
INCREASE AFTER vibration rises. This temporal lag is what allows AI-driven RCA to discover
the causality: "Robot servo degradation caused line stoppage, not the reverse."
"""

import random
import logging
from typing import Any, Dict, List, Optional

from dd_demo_toolkit.simulator.plugins import IncidentPlugin

logger = logging.getLogger("assembly_line_halt_incident")


def clamp(value: float, lo: float, hi: float) -> float:
    """Clamp a value to [lo, hi]."""
    return max(lo, min(hi, value))


def drift(value: float, magnitude: float = 1.0, bias: float = 0.0) -> float:
    """Apply Gaussian drift to a value."""
    return value + random.gauss(bias, magnitude)


class AssemblyLineHaltIncident(IncidentPlugin):
    """
    Simulates an Assembly-1 robot arm servo degradation cascading to line halt.

    This is the primary incident scenario for the manufacturing vertical, demonstrating
    how equipment degradation manifests as cycle time increase and production loss.
    """

    # Incident phase durations (in ticks, each tick = 15 seconds by default)
    RAMP_TICKS = 6          # 1m30s: Robot vibration starts increasing
    DEGRADING_TICKS = 8     # 2m: Cycle time increasing, defects rising
    HALTED_TICKS = 6        # 1m30s: Robot and line halted
    RECOVERY_TICKS = 10     # 2m30s: Recovery phase

    # Total event duration: 30 ticks = 7m30s of visible activity
    EVENT_TICKS = RAMP_TICKS + DEGRADING_TICKS + HALTED_TICKS + RECOVERY_TICKS

    # Fixed incident location (Assembly-1 in Plant-A-Detroit)
    INCIDENT_PLANT = "Plant-A-Detroit"
    INCIDENT_LINE = "Assembly-1"

    def __init__(self):
        """Initialize the incident plugin."""
        self._ticks_until_next = random.randint(20, 40)
        self._active_tick: Optional[int] = None
        self._incident_robots: List[Dict[str, Any]] = []
        self._incident_plcs: List[Dict[str, Any]] = []
        self._incident_conveyors: List[Dict[str, Any]] = []
        self._incident_vision: List[Dict[str, Any]] = []

        logger.info(
            f"Assembly Line Halt Incident initialized. First incident in ~{self._ticks_until_next * 15 // 60} min"
        )

    def on_tick(self, tick_count: int, fleet: List[Dict[str, Any]], engine: Any) -> None:
        """Called on each simulator tick to apply incident overrides."""
        # Index incident devices on first tick
        if tick_count == 0 or not self._incident_robots:
            self._incident_robots = [
                d for d in fleet
                if d.get("device_type") == "robot_arm"
                and d.get("plant") == self.INCIDENT_PLANT
                and d.get("line") == self.INCIDENT_LINE
            ]
            self._incident_plcs = [
                d for d in fleet
                if d.get("device_type") == "plc_controller"
                and d.get("plant") == self.INCIDENT_PLANT
                and d.get("line") == self.INCIDENT_LINE
            ]
            self._incident_conveyors = [
                d for d in fleet
                if d.get("device_type") == "conveyor_system"
                and d.get("plant") == self.INCIDENT_PLANT
                and d.get("line") == self.INCIDENT_LINE
            ]
            self._incident_vision = [
                d for d in fleet
                if d.get("device_type") == "vision_inspector"
                and d.get("plant") == self.INCIDENT_PLANT
                and d.get("line") == self.INCIDENT_LINE
            ]

            if self._incident_robots or self._incident_plcs:
                logger.info(
                    f"Indexed incident devices: {len(self._incident_robots)} robots, "
                    f"{len(self._incident_plcs)} PLCs, {len(self._incident_conveyors)} conveyors, "
                    f"{len(self._incident_vision)} vision on {self.INCIDENT_LINE} at {self.INCIDENT_PLANT}"
                )

        # Advance incident clock and apply overrides
        self._advance_incident_clock()
        phase, phase_tick = self._get_incident_phase()

        if phase != "normal":
            self._apply_overrides(phase, phase_tick)
            logger.info(
                f"INCIDENT [{phase} t={phase_tick}] {self.INCIDENT_LINE} at {self.INCIDENT_PLANT}: "
                f"robots_running={sum(1 for r in self._incident_robots if r.get('is_online'))}/"
                f"{len(self._incident_robots)}, "
                f"conveyor_jams={sum(r.get('_incident_jam_count', 0) for r in self._incident_conveyors)}"
            )

    def get_incident_name(self) -> str:
        """Return human-readable name for this incident."""
        return "Robot Arm Servo Degradation → Assembly Line Halt"

    def reset(self) -> None:
        """Reset plugin state."""
        self._ticks_until_next = random.randint(20, 40)
        self._active_tick = None
        self._incident_robots = []
        self._incident_plcs = []
        self._incident_conveyors = []
        self._incident_vision = []

    # === Private methods ===

    def _get_incident_phase(self) -> tuple:
        """Return (phase_name, tick_within_phase) for current state."""
        if self._active_tick is None:
            return ("normal", 0)

        t = self._active_tick
        r = self.RAMP_TICKS
        d = r + self.DEGRADING_TICKS
        h = d + self.HALTED_TICKS
        e = h + self.RECOVERY_TICKS

        if t < r:
            return ("ramp_up", t)
        elif t < d:
            return ("degrading", t - r)
        elif t < h:
            return ("halted", t - d)
        elif t < e:
            return ("recovering", t - h)
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
                logger.info(f"INCIDENT STARTING: {self.INCIDENT_LINE} at {self.INCIDENT_PLANT}")

    def _apply_overrides(self, phase: str, phase_tick: int) -> None:
        """Apply incident state overrides to devices."""
        # === Robot Arm Overrides ===
        for robot in self._incident_robots:
            if phase == "ramp_up":
                # Vibration and servo errors climbing
                progress = phase_tick / self.RAMP_TICKS
                robot["_incident_vibration"] = 2.0 + progress * 3.0
                robot["servo_error_count"] = int(2 + progress * 10)
                robot["joint_temperature_c"] = clamp(drift(65.0 + progress * 5.0, 1.0), 60.0, 80.0)
                robot["cycle_time_ms"] = clamp(drift(5000.0 + progress * 200.0, 50.0), 5000.0, 5500.0)
                robot["is_online"] = True

            elif phase == "degrading":
                # Cycle time spiking, servo errors high, defects increasing
                progress = phase_tick / self.DEGRADING_TICKS
                robot["_incident_vibration"] = clamp(random.gauss(5.5, 0.5), 4.5, 6.5)
                robot["servo_error_count"] = int(clamp(random.gauss(15 + progress * 10, 2), 10, 25))
                robot["joint_temperature_c"] = clamp(drift(70.0 + progress * 8.0, 2.0), 65.0, 85.0)
                robot["cycle_time_ms"] = clamp(drift(5200.0 + progress * 300.0, 100.0), 5200.0, 5800.0)
                robot["is_online"] = True

            elif phase == "halted":
                # Robot stops or is in limp mode
                robot["_incident_vibration"] = 0.1
                robot["servo_error_count"] = int(clamp(random.gauss(20, 3), 15, 25))
                robot["joint_temperature_c"] = clamp(drift(55.0, 2.0), 50.0, 65.0)
                robot["cycle_time_ms"] = 999999  # Stopped
                robot["is_online"] = phase_tick > 2  # Offline after 2 ticks

            elif phase == "recovering":
                # Recovery: servo errors drop, cycle time normalizes
                progress = phase_tick / self.RECOVERY_TICKS
                robot["_incident_vibration"] = 5.0 - progress * 4.5
                robot["servo_error_count"] = int(clamp(20 - progress * 18, 2, 20))
                robot["joint_temperature_c"] = 55.0 + progress * 10.0
                robot["cycle_time_ms"] = clamp(5400.0 - progress * 400.0, 5000.0, 5400.0)
                robot["is_online"] = True

        # === PLC Overrides (synchronization issues) ===
        for plc in self._incident_plcs:
            if phase == "ramp_up":
                progress = phase_tick / self.RAMP_TICKS
                plc["cycle_time_ms"] = clamp(drift(300.0 + progress * 20.0, 5.0), 300.0, 330.0)
                plc["program_errors"] = int(progress * 2)

            elif phase == "degrading":
                # PLC detects slow-running robot, cycle time increases
                progress = phase_tick / self.DEGRADING_TICKS
                plc["cycle_time_ms"] = clamp(drift(320.0 + progress * 40.0, 10.0), 320.0, 380.0)
                plc["program_errors"] = int(2 + progress * 5)

            elif phase == "halted":
                # PLC timeouts, line stopped
                plc["cycle_time_ms"] = clamp(random.gauss(400, 50), 350, 500)
                plc["program_errors"] = int(clamp(random.gauss(8, 2), 5, 15))

            elif phase == "recovering":
                # Recovery
                progress = phase_tick / self.RECOVERY_TICKS
                plc["cycle_time_ms"] = clamp(400.0 - progress * 100.0, 300.0, 400.0)
                plc["program_errors"] = int(8 - progress * 7)

        # === Conveyor Overrides (backup and jam) ===
        for i, conveyor in enumerate(self._incident_conveyors):
            if phase == "ramp_up":
                # Conveyors operating normally during ramp
                conveyor["belt_speed_m_min"] = clamp(drift(60.0, 2.0), 58.0, 62.0)
                conveyor["jam_events"] = 0
                conveyor["_incident_jam_count"] = 0

            elif phase == "degrading":
                # Robot slowdown causes conveyor backup
                progress = phase_tick / self.DEGRADING_TICKS
                conveyor["belt_speed_m_min"] = clamp(drift(60.0 - progress * 30.0, 3.0), 30.0, 60.0)
                jam_count = int(progress * 2)
                conveyor["jam_events"] = jam_count
                conveyor["_incident_jam_count"] = jam_count

            elif phase == "halted":
                # Conveyor jammed and stopped
                conveyor["belt_speed_m_min"] = clamp(random.gauss(5, 2), 0, 15)
                conveyor["jam_events"] = int(clamp(random.gauss(3, 1), 1, 5))
                conveyor["_incident_jam_count"] = 3

            elif phase == "recovering":
                # Conveyor restart and speed ramp
                progress = phase_tick / self.RECOVERY_TICKS
                conveyor["belt_speed_m_min"] = progress * 60.0
                conveyor["jam_events"] = int(3 - progress * 3)
                conveyor["_incident_jam_count"] = int(3 - progress * 3)

        # === Vision Inspector Overrides (quality degradation) ===
        for vision in self._incident_vision:
            if phase == "ramp_up":
                # Quality normal
                vision["defect_rate_ppm"] = clamp(drift(30.0, 5.0), 20.0, 45.0)
                vision["image_capture_ms"] = clamp(drift(85.0, 3.0), 80.0, 95.0)

            elif phase == "degrading":
                # Robot slowdown causes quality issues
                progress = phase_tick / self.DEGRADING_TICKS
                vision["defect_rate_ppm"] = clamp(drift(30.0 + progress * 80.0, 10.0), 30.0, 150.0)
                vision["image_capture_ms"] = clamp(drift(85.0 + progress * 20.0, 5.0), 85.0, 120.0)

            elif phase == "halted":
                # High defect rate, line stopped
                vision["defect_rate_ppm"] = clamp(random.gauss(180, 20), 150, 250)
                vision["image_capture_ms"] = clamp(random.gauss(130, 10), 120, 150)

            elif phase == "recovering":
                # Quality recovery
                progress = phase_tick / self.RECOVERY_TICKS
                vision["defect_rate_ppm"] = clamp(180.0 - progress * 150.0, 30.0, 180.0)
                vision["image_capture_ms"] = clamp(130.0 - progress * 45.0, 85.0, 130.0)
