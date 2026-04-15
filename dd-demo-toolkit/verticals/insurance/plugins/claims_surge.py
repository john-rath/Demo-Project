"""
Catastrophe Event Claims Surge Incident Plugin for Insurance Vertical

Simulates a major weather event (hurricane, hailstorm, tornado) creating a spike
in claims volume, overwhelming the claims processing pipeline, and cascading
through document processing, OCR, adjuster assignment, and payment processing.

This plugin demonstrates correlated failures where operational constraints
(limited adjuster availability, document processor capacity, payment gateway limits)
become bottlenecks during high-volume events.

The incident plays out in distinct phases:
  1. ramp_up (8 ticks / 2m): Claims volume 2x-3x baseline
  2. peak (12 ticks / 3m): Volume 5x-8x baseline, processing latency spikes
  3. saturated (10 ticks / 2m30s): Processing stalls, queue backlogs accumulate
  4. recovery (15 ticks / 3m45s): Additional capacity brought online, queues drain
  5. normalization (8 ticks / 2m): Volume returns to baseline

Total event duration: 53 ticks = ~13.25 minutes of visible activity
"""

import random
import logging
from typing import Any, Dict, List, Optional

from dd_demo_toolkit.simulator.plugins import IncidentPlugin

logger = logging.getLogger("claims_surge_incident")


def clamp(value: float, lo: float, hi: float) -> float:
    """Clamp a value to [lo, hi]."""
    return max(lo, min(hi, value))


def drift(value: float, magnitude: float = 1.0, bias: float = 0.0) -> float:
    """Apply Gaussian drift to a value."""
    return value + random.gauss(bias, magnitude)


class ClaimsSurgeIncident(IncidentPlugin):
    """
    Simulates a catastrophe event (hurricane, hailstorm) creating a claims surge.

    This is the primary incident scenario for the insurance vertical, demonstrating
    how operational constraints become bottlenecks during high-volume events, and
    how dependent systems cascade in failure modes.
    """

    # Incident phase durations (in ticks, each tick = 15 seconds by default)
    RAMP_UP_TICKS = 8          # 2m: Claims arriving, 2-3x baseline
    PEAK_TICKS = 12            # 3m: Peak volume, 5-8x baseline
    SATURATED_TICKS = 10       # 2m30s: Queues backed up, latencies spiking
    RECOVERY_TICKS = 15        # 3m45s: Additional capacity, queues draining
    NORMALIZATION_TICKS = 8    # 2m: Return to baseline

    # Total event duration: 53 ticks = 13m15s of visible activity
    EVENT_TICKS = (
        RAMP_UP_TICKS
        + PEAK_TICKS
        + SATURATED_TICKS
        + RECOVERY_TICKS
        + NORMALIZATION_TICKS
    )

    # Casualty location (can be any region; we use US-East as default)
    INCIDENT_REGION = "us-east"
    INCIDENT_ENVIRONMENT = "production"

    def __init__(self):
        """Initialize the incident plugin."""
        self._ticks_until_next = random.randint(15, 30)
        self._active_tick: Optional[int] = None
        self._incident_devices: Dict[str, List[Dict[str, Any]]] = {
            "claims_intake_server": [],
            "document_processor": [],
            "adjuster_mobile_device": [],
            "payment_disbursement_node": [],
            "rating_engine": [],
        }

        logger.info(
            f"Claims Surge Incident initialized. First incident in ~{self._ticks_until_next * 15 // 60} min"
        )

    def get_incident_name(self) -> str:
        """Return the human-readable name for this incident."""
        return "Catastrophe Event Claims Surge"

    def on_tick(self, tick_count: int, fleet: List[Dict[str, Any]], engine: Any) -> None:
        """Called on each simulator tick to apply incident overrides."""
        # Index incident devices on first tick
        if tick_count == 0 or not self._incident_devices["claims_intake_server"]:
            for device_type in self._incident_devices:
                self._incident_devices[device_type] = [
                    d
                    for d in fleet
                    if d.get("device_type") == device_type
                    and d.get("region") == self.INCIDENT_REGION
                    and d.get("environment") == self.INCIDENT_ENVIRONMENT
                ]

            if self._incident_devices["claims_intake_server"]:
                logger.info(
                    f"Indexed incident devices in {self.INCIDENT_REGION} {self.INCIDENT_ENVIRONMENT}: "
                    f"{len(self._incident_devices['claims_intake_server'])} intake servers, "
                    f"{len(self._incident_devices['document_processor'])} doc processors, "
                    f"{len(self._incident_devices['adjuster_mobile_device'])} adjusters"
                )

        # Advance incident clock and apply overrides
        self._advance_incident_clock()
        phase, phase_tick = self._get_incident_phase()

        if phase != "normal":
            self._apply_overrides(phase, phase_tick)
            logger.debug(
                f"Claims Surge Incident: phase={phase} tick={phase_tick} "
                f"(global tick {tick_count}, incident tick {self._active_tick})"
            )

    def _advance_incident_clock(self) -> None:
        """Advance the incident clock; trigger new incident if needed."""
        if self._active_tick is None:
            # Not currently in an incident; count down to next one
            self._ticks_until_next -= 1
            if self._ticks_until_next <= 0:
                logger.info("Catastrophe claims surge incident triggered!")
                self._active_tick = 0
        else:
            # In an incident; advance the clock
            self._active_tick += 1
            if self._active_tick >= self.EVENT_TICKS:
                logger.info("Claims surge incident concluded")
                self._active_tick = None
                self._ticks_until_next = random.randint(20, 40)

    def _get_incident_phase(self) -> tuple[str, int]:
        """Return the current phase and phase-local tick."""
        if self._active_tick is None:
            return ("normal", 0)

        tick = self._active_tick
        if tick < self.RAMP_UP_TICKS:
            return ("ramp_up", tick)
        tick -= self.RAMP_UP_TICKS

        if tick < self.PEAK_TICKS:
            return ("peak", tick)
        tick -= self.PEAK_TICKS

        if tick < self.SATURATED_TICKS:
            return ("saturated", tick)
        tick -= self.SATURATED_TICKS

        if tick < self.RECOVERY_TICKS:
            return ("recovery", tick)
        tick -= self.RECOVERY_TICKS

        return ("normalization", tick)

    def _apply_overrides(self, phase: str, phase_tick: int) -> None:
        """Apply incident metric overrides based on current phase."""
        if phase == "normal":
            return

        # Phase-specific multipliers for claim volume
        phase_multipliers = {
            "ramp_up": 2.0 + (phase_tick / self.RAMP_UP_TICKS) * 1.5,  # 2x -> 3.5x
            "peak": 5.0 + (phase_tick / self.PEAK_TICKS) * 3.0,  # 5x -> 8x
            "saturated": 6.0,  # Sustained high volume
            "recovery": 6.0 - (phase_tick / self.RECOVERY_TICKS) * 4.0,  # 6x -> 2x
            "normalization": 2.0 - (phase_tick / self.NORMALIZATION_TICKS) * 1.5,  # 2x -> 0.5x
        }
        volume_multiplier = phase_multipliers.get(phase, 1.0)

        # Claims intake servers: higher volume, no catastrophic degradation
        for device in self._incident_devices["claims_intake_server"]:
            device["metrics"]["claims_received_per_min"] = int(
                device["metrics"]["claims_received_per_min"] * volume_multiplier
            )
            # Processing latency increases slightly under load
            if phase in ("saturated",):
                device["metrics"]["avg_processing_ms"] *= 1.8
            elif phase in ("peak",):
                device["metrics"]["avg_processing_ms"] *= 1.4

        # Document processors: extreme stress in saturated/recovery phases
        for device in self._incident_devices["document_processor"]:
            device["metrics"]["pages_processed_per_min"] = int(
                device["metrics"]["pages_processed_per_min"] * volume_multiplier
            )
            # Queue backs up dramatically
            device["metrics"]["queue_backlog"] = int(
                device["metrics"]["queue_backlog"]
                * {
                    "ramp_up": 1.5,
                    "peak": 3.5,
                    "saturated": 6.0,  # Severe backlog
                    "recovery": 3.0,
                    "normalization": 1.2,
                }.get(phase, 1.0)
            )
            # OCR accuracy drops under high volume
            if phase in ("saturated", "recovery"):
                device["metrics"]["ocr_accuracy_pct"] -= 2.5
            elif phase in ("peak",):
                device["metrics"]["ocr_accuracy_pct"] -= 1.2
            # Classification errors spike
            device["metrics"]["classification_errors"] = int(
                device["metrics"]["classification_errors"]
                * {
                    "ramp_up": 1.8,
                    "peak": 3.5,
                    "saturated": 5.0,
                    "recovery": 2.5,
                    "normalization": 1.1,
                }.get(phase, 1.0)
            )

        # Adjuster mobile devices: limited availability, escalating photo uploads
        for device in self._incident_devices["adjuster_mobile_device"]:
            # More claims assigned = more photo uploads
            device["metrics"]["photo_uploads_per_hour"] = int(
                device["metrics"]["photo_uploads_per_hour"] * volume_multiplier
            )
            # Network congestion from surge of uploads
            if phase in ("saturated", "recovery"):
                device["metrics"]["sync_lag_ms"] *= 3.0
            elif phase in ("peak",):
                device["metrics"]["sync_lag_ms"] *= 1.8
            # App instability under load
            device["metrics"]["app_crash_rate"] *= {
                "ramp_up": 1.3,
                "peak": 2.5,
                "saturated": 4.0,
                "recovery": 2.0,
                "normalization": 1.1,
            }.get(phase, 1.0)

        # Payment disbursement nodes: bottleneck in recovery phase
        for device in self._incident_devices["payment_disbursement_node"]:
            # More claims = more payments
            device["metrics"]["payments_per_min"] = int(
                device["metrics"]["payments_per_min"] * volume_multiplier
            )
            # Check print queue backed up (physical bottleneck)
            device["metrics"]["check_print_queue"] *= {
                "ramp_up": 1.2,
                "peak": 2.0,
                "saturated": 3.5,
                "recovery": 2.2,
                "normalization": 1.0,
            }.get(phase, 1.0)
            # Failed payments increase slightly
            device["metrics"]["failed_payments"] *= {
                "ramp_up": 1.0,
                "peak": 1.5,
                "saturated": 2.5,
                "recovery": 1.8,
                "normalization": 1.0,
            }.get(phase, 1.0)

        # Rating engines: quote volume from customer portal spike
        for device in self._incident_devices["rating_engine"]:
            # Customers checking quotes online post-event
            device["metrics"]["quotes_per_sec"] *= {
                "ramp_up": 1.8,
                "peak": 3.0,
                "saturated": 2.5,
                "recovery": 2.0,
                "normalization": 1.0,
            }.get(phase, 1.0)
            # Cache hit rate drops (cache thrashing under high volume)
            if phase in ("peak", "saturated"):
                device["metrics"]["cache_hit_rate_pct"] -= 15
            # Calculation latency increases
            device["metrics"]["avg_calculation_ms"] *= {
                "ramp_up": 1.1,
                "peak": 1.3,
                "saturated": 1.6,
                "recovery": 1.2,
                "normalization": 1.0,
            }.get(phase, 1.0)


# Export the incident class
__all__ = ["ClaimsSurgeIncident"]
