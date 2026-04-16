"""
Meraki AP → IoT Gateway → Guest Experience Cascade Incident Plugin

Simulates a realistic multi-layer failure across TWO APAC properties:

  PRIMARY (Extended Stay APAC) — WiFi Client Overload:
    An anomalous surge of WiFi clients on Meraki APs (from ~33 to ~47,
    a 42% increase) overloads the wireless network, degrading signal
    strength from -48 dBm to -57 dBm. This progressive WiFi signal
    degradation causes IoT gateways to lose connectivity to their
    connected room devices, dropping from ~8 devices/gateway to ~1.9.
    Retransmit rates fall to near 0% — devices disconnect entirely
    rather than experiencing degraded connections. Gateway hardware
    remains healthy throughout (CPU 43-45%, Memory 41-44%).

  SECONDARY (Luxury Collection APAC) — Channel Saturation:
    A concurrent but DISTINCT failure mode driven by WiFi channel
    saturation to 85%, with connected clients dropping from ~43 to ~11
    and retransmit rate hitting 10%.

This plugin demonstrates the FULL hospitality observability story:
  Network → IoT → Guest Experience → IT Ops → Self-Healing

The incident plays out in 5 distinct phases:
  1. normal       (12-24 ticks): Steady-state, everything healthy
  2. ramp_up      (6 ticks / 1m30s): WiFi clients surging, signal
                  degrading, IoT gateways still connected
  3. degraded     (8 ticks / 2m): Signal below threshold, IoT devices
                  disconnecting, gateways losing room devices
  4. outage       (8 ticks / 2m): Most room devices offline, check-in
                  kiosks failing, ServiceNow incidents firing
  5. recovering   (10 ticks / 2m30s): Self-healing workflow kicks excess
                  clients, signal recovers, gateways reconnect

KEY DEMO NARRATIVE:
  "Bits detected an anomalous surge of WiFi clients on Extended Stay
   APAC Meraki APs — a 42% increase in 12 minutes. As the APs became
   overloaded, signal strength dropped 9 dBm, and IoT gateways started
   losing their connected room devices. Smart locks, HVAC, and lighting
   went dark across 48 rooms. Bits traced the root cause chain
   automatically. Datadog Workflows then called the Meraki API to
   rate-limit rogue clients, restoring connectivity without a single
   on-site dispatch."

The temporal lag between signal degradation and IoT failure is what makes
the demo compelling — it proves causality that's invisible without
unified observability.
"""

import random
import logging
from typing import Any, Dict, List, Optional

from dd_demo_toolkit.simulator.plugins import IncidentPlugin

logger = logging.getLogger("meraki_iot_cascade")


def clamp(value: float, lo: float, hi: float) -> float:
    """Clamp a value to [lo, hi]."""
    return max(lo, min(hi, value))


def drift(value: float, magnitude: float = 1.0, bias: float = 0.0) -> float:
    """Apply Gaussian drift to a value."""
    return value + random.gauss(bias, magnitude)


class MerakiIoTCascadeIncident(IncidentPlugin):
    """
    Simulates a WiFi client overload cascading through IoT gateways to
    guest experience degradation, with Datadog Workflow self-healing.

    Primary target:   Extended Stay, APAC (client overload)
    Secondary target: Luxury Collection, APAC (channel saturation — distinct mode)
    """

    # Phase durations (in ticks; each tick = 15s by default)
    RAMP_TICKS = 6           # 1m30s: WiFi clients surging, signal degrading
    DEGRADED_TICKS = 8       # 2m: Signal below threshold, IoT disconnecting
    OUTAGE_TICKS = 8         # 2m: Full cascade — room devices offline, check-in failing
    RECOVERY_TICKS = 10      # 2m30s: Self-healing kicks in, recovery

    EVENT_TICKS = RAMP_TICKS + DEGRADED_TICKS + OUTAGE_TICKS + RECOVERY_TICKS

    # Primary incident location (WiFi client overload)
    PRIMARY_PROPERTY = "Extended Stay"
    PRIMARY_REGION = "APAC"

    # Secondary incident location (channel saturation — different failure mode)
    SECONDARY_PROPERTY = "Luxury Collection"
    SECONDARY_REGION = "APAC"

    def __init__(self):
        """Initialize the incident plugin."""
        # Start first incident quickly (3-6 min) for demo readiness
        self._ticks_until_next = random.randint(12, 24)
        self._active_tick: Optional[int] = None

        # Primary property devices (Extended Stay APAC)
        self._primary_aps: list = []
        self._primary_gateways: list = []
        self._primary_locks: list = []
        self._primary_thermostats: list = []
        self._primary_kiosks: list = []
        self._primary_snow: list = []
        self._primary_ai: list = []

        # Secondary property devices (Luxury Collection APAC)
        self._secondary_aps: list = []
        self._secondary_gateways: list = []

        self._indexed = False

        logger.info(
            f"MerakiIoTCascade initialized (WiFi client overload mode). "
            f"First incident in ~{self._ticks_until_next * 15 // 60} min"
        )

    def on_tick(self, tick_count: int, fleet: list, engine: Any) -> None:
        """Called on each simulator tick to apply incident overrides."""

        # Index incident devices on first tick
        if not self._indexed:
            self._index_devices(fleet)
            self._indexed = True

        # Advance incident clock
        self._advance_incident_clock()
        phase, phase_tick = self._get_phase()

        # Publish incident state so other subsystems (RUM, etc.) can react
        severity_map = {"normal": 0.0, "ramp_up": 0.3, "degraded": 0.7, "outage": 1.0, "recovering": 0.4}
        if hasattr(engine, "incident_state"):
            if phase == "normal":
                engine.incident_state.pop("wifi_client_overload", None)
            else:
                engine.incident_state["wifi_client_overload"] = {
                    "phase": phase,
                    "phase_tick": phase_tick,
                    "severity": severity_map.get(phase, 0.5),
                    "property": self.PRIMARY_PROPERTY,
                    "region": self.PRIMARY_REGION,
                }

        if phase == "normal":
            return

        # --- PRIMARY: Extended Stay APAC (WiFi client overload) ---
        self._apply_primary_ap_overrides(phase, phase_tick)
        self._apply_primary_gateway_overrides(phase, phase_tick)
        self._apply_lock_overrides(phase, phase_tick)
        self._apply_thermostat_overrides(phase, phase_tick)
        self._apply_kiosk_overrides(phase, phase_tick)
        self._apply_snow_overrides(phase, phase_tick)
        self._apply_ai_overrides(phase, phase_tick)

        # --- SECONDARY: Luxury Collection APAC (channel saturation) ---
        self._apply_secondary_ap_overrides(phase, phase_tick)
        self._apply_secondary_gateway_overrides(phase, phase_tick)

        # Log progress
        primary_gw_devices = [
            g.state.get("hospitality.iot.gateway_connected_devices", 8)
            for g in self._primary_gateways
        ]
        avg_devices = (
            sum(primary_gw_devices) / len(primary_gw_devices)
            if primary_gw_devices else 0
        )
        logger.info(
            f"INCIDENT [{phase} t={phase_tick}] "
            f"{self.PRIMARY_PROPERTY} {self.PRIMARY_REGION}: "
            f"avg_connected_devices={avg_devices:.1f}, "
            f"secondary_aps={len(self._secondary_aps)}"
        )

    def get_incident_name(self) -> str:
        """Return human-readable name for this incident."""
        return (
            f"WiFi Client Overload → IoT Gateway Cascade "
            f"({self.PRIMARY_PROPERTY} {self.PRIMARY_REGION})"
        )

    def reset(self) -> None:
        """Reset plugin state."""
        self._ticks_until_next = random.randint(20, 40)
        self._active_tick = None
        self._indexed = False
        self._primary_aps = []
        self._primary_gateways = []
        self._primary_locks = []
        self._primary_thermostats = []
        self._primary_kiosks = []
        self._primary_snow = []
        self._primary_ai = []
        self._secondary_aps = []
        self._secondary_gateways = []

    # =========================================================================
    # Device indexing
    # =========================================================================

    def _index_devices(self, fleet: list) -> None:
        """
        Index devices by type and location for both incident targets.

        DeviceProfile objects have:
          .type (str), .location (dict with property_type, region, etc.),
          .state (dict of metric_name -> current_value), .metrics (list)
        """
        for device in fleet:
            loc = device.location
            prop = loc.get("property_type", "")
            region = loc.get("region", "")

            # Primary target: Extended Stay APAC
            if prop == self.PRIMARY_PROPERTY and region == self.PRIMARY_REGION:
                if device.type == "meraki_ap":
                    self._primary_aps.append(device)
                elif device.type == "connected_room_gateway":
                    self._primary_gateways.append(device)
                elif device.type == "smart_lock":
                    self._primary_locks.append(device)
                elif device.type == "smart_thermostat":
                    self._primary_thermostats.append(device)
                elif device.type == "lobby_kiosk":
                    self._primary_kiosks.append(device)
                elif device.type == "itsm_telemetry_feed":
                    self._primary_snow.append(device)
                elif device.type == "ai_inference_endpoint":
                    self._primary_ai.append(device)

            # Secondary target: Luxury Collection APAC
            elif prop == self.SECONDARY_PROPERTY and region == self.SECONDARY_REGION:
                if device.type == "meraki_ap":
                    self._secondary_aps.append(device)
                elif device.type == "connected_room_gateway":
                    self._secondary_gateways.append(device)

        logger.info(
            f"Indexed PRIMARY ({self.PRIMARY_PROPERTY} {self.PRIMARY_REGION}): "
            f"{len(self._primary_aps)} APs, "
            f"{len(self._primary_gateways)} gateways, "
            f"{len(self._primary_locks)} locks, "
            f"{len(self._primary_thermostats)} thermostats, "
            f"{len(self._primary_kiosks)} kiosks, "
            f"{len(self._primary_snow)} SNOW feeds, "
            f"{len(self._primary_ai)} AI endpoints"
        )
        logger.info(
            f"Indexed SECONDARY ({self.SECONDARY_PROPERTY} {self.SECONDARY_REGION}): "
            f"{len(self._secondary_aps)} APs, "
            f"{len(self._secondary_gateways)} gateways"
        )

    # =========================================================================
    # State machine
    # =========================================================================

    def _get_phase(self) -> tuple:
        """Return (phase_name, tick_within_phase)."""
        if self._active_tick is None:
            return ("normal", 0)

        t = self._active_tick
        r = self.RAMP_TICKS
        d = r + self.DEGRADED_TICKS
        o = d + self.OUTAGE_TICKS
        e = o + self.RECOVERY_TICKS

        if t < r:
            return ("ramp_up", t)
        elif t < d:
            return ("degraded", t - r)
        elif t < o:
            return ("outage", t - d)
        elif t < e:
            return ("recovering", t - o)
        return ("normal", 0)

    def _advance_incident_clock(self) -> None:
        """Advance the incident state machine each tick."""
        if self._active_tick is not None:
            self._active_tick += 1
            if self._active_tick >= self.EVENT_TICKS:
                self._active_tick = None
                # Shorter gap between incidents for demo (5-8 min)
                self._ticks_until_next = random.randint(20, 32)
                logger.info(
                    f"Incident complete. Next incident in "
                    f"~{self._ticks_until_next * 15 // 60} min"
                )
        else:
            self._ticks_until_next -= 1
            if self._ticks_until_next <= 0:
                self._active_tick = 0
                logger.info(
                    f"INCIDENT STARTING: {self.PRIMARY_PROPERTY} "
                    f"{self.PRIMARY_REGION} — WiFi client overload"
                )

    # =========================================================================
    # PRIMARY property: Extended Stay APAC — WiFi Client Overload
    # =========================================================================

    def _apply_primary_ap_overrides(self, phase: str, phase_tick: int) -> None:
        """
        Override Meraki AP metrics for the PRIMARY failure mode.

        ROOT CAUSE: Anomalous surge of WiFi clients overloads APs.
        - Connected clients surge from ~33 to ~47 (42% increase)
        - Signal strength degrades from -48 dBm to -57 dBm (9 dBm drop)
        - Channel utilization only reaches ~51% (NOT saturation)
        - Retransmit rates DROP to near 0% (devices disconnect entirely)
        - Gateway hardware remains healthy throughout
        """
        for ap in self._primary_aps:
            if phase == "ramp_up":
                # WiFi clients surging progressively
                progress = phase_tick / self.RAMP_TICKS
                # Clients: 33 → 47 (42% increase over ramp period)
                ap.state["hospitality.network.connected_clients"] = clamp(
                    drift(33 + progress * 14, 1.5), 30, 50
                )
                # Signal degrading: -48 → -52 dBm (starting to weaken)
                ap.state["hospitality.network.signal_strength_dbm"] = clamp(
                    drift(-48 - progress * 4, 0.5), -55, -42
                )
                # Channel util rising but NOT saturating: 30% → 40%
                ap.state["hospitality.network.channel_utilization_pct"] = clamp(
                    drift(30 + progress * 10, 2.0), 25, 50
                )
                # Retransmit starting to increase slightly: 4% → 5%
                ap.state["hospitality.network.retransmit_pct"] = clamp(
                    drift(4.0 + progress * 1.0, 0.3), 2, 7
                )
                ap.state["hospitality.network.latency_ms"] = clamp(
                    drift(8 + progress * 8, 1.5), 5, 25
                )
                ap.state["hospitality.device.online"] = 1.0

            elif phase == "degraded":
                # Clients peaked, signal severely degraded
                progress = phase_tick / self.DEGRADED_TICKS
                # Clients: ~47 (peaked, some starting to disconnect)
                ap.state["hospitality.network.connected_clients"] = clamp(
                    drift(47 - progress * 5, 2.0), 35, 52
                )
                # Signal: -52 → -57 dBm (critical degradation)
                ap.state["hospitality.network.signal_strength_dbm"] = clamp(
                    drift(-52 - progress * 5, 0.8), -60, -48
                )
                # Channel util: ~48-51% (below 80% threshold — this is key)
                ap.state["hospitality.network.channel_utilization_pct"] = clamp(
                    drift(48 + progress * 3, 2.0), 40, 55
                )
                # Retransmit DROPS toward 0% — devices stop trying, just disconnect
                ap.state["hospitality.network.retransmit_pct"] = clamp(
                    drift(4.0 - progress * 3.5, 0.5), 0, 5
                )
                ap.state["hospitality.network.latency_ms"] = clamp(
                    drift(20 + progress * 10, 3.0), 10, 45
                )

            elif phase == "outage":
                # Signal bottomed out, most IoT devices disconnected
                ap.state["hospitality.network.connected_clients"] = clamp(
                    drift(40 - phase_tick * 1.5, 2.0), 28, 48
                )
                ap.state["hospitality.network.signal_strength_dbm"] = clamp(
                    drift(-57, 1.0), -62, -53
                )
                ap.state["hospitality.network.channel_utilization_pct"] = clamp(
                    drift(51, 2.0), 44, 56
                )
                # Retransmit near 0 — devices have disconnected entirely
                ap.state["hospitality.network.retransmit_pct"] = clamp(
                    drift(0.5, 0.3), 0, 2
                )
                ap.state["hospitality.network.latency_ms"] = clamp(
                    drift(30, 5.0), 15, 50
                )

            elif phase == "recovering":
                # Self-healing workflow kicked excess clients via Meraki API
                progress = phase_tick / self.RECOVERY_TICKS
                # Clients dropping as rate-limiting takes effect: 40 → 30
                ap.state["hospitality.network.connected_clients"] = clamp(
                    drift(40 - progress * 10, 1.5), 25, 42
                )
                # Signal recovering: -57 → -48 dBm
                ap.state["hospitality.network.signal_strength_dbm"] = clamp(
                    drift(-57 + progress * 9, 0.8), -58, -44
                )
                # Channel util normalizing: 51% → 32%
                ap.state["hospitality.network.channel_utilization_pct"] = clamp(
                    drift(51 - progress * 19, 2.0), 25, 52
                )
                # Retransmit recovering to normal: 0% → 4%
                ap.state["hospitality.network.retransmit_pct"] = clamp(
                    drift(0.5 + progress * 3.5, 0.3), 0, 6
                )
                ap.state["hospitality.network.latency_ms"] = clamp(
                    drift(30 - progress * 22, 2.0), 5, 35
                )

    def _apply_primary_gateway_overrides(self, phase: str, phase_tick: int) -> None:
        """
        Override IoT gateway metrics for the PRIMARY property.

        LAYER 2: Gateways lose connectivity AFTER signal degrades.
        Gateway hardware remains HEALTHY throughout (CPU 43-45%, Mem 41-44%).
        Connected devices drop from ~8.0 to ~1.9.
        The temporal lag between signal degradation and device loss is the
        key insight Bits uses for root cause analysis.
        """
        for i, gw in enumerate(self._primary_gateways):
            if phase == "ramp_up":
                # Gateways still fully healthy — this is the critical temporal lag
                gw.state["hospitality.iot.gateway_online"] = 1.0
                gw.state["hospitality.iot.gateway_connected_devices"] = clamp(
                    drift(8.0, 0.5), 6, 10
                )
                # Gateway hardware perfectly healthy (Bits evidence)
                gw.state["hospitality.iot.gateway_cpu_pct"] = clamp(
                    drift(44.0, 1.0), 40, 48
                )
                gw.state["hospitality.iot.gateway_memory_pct"] = clamp(
                    drift(42.0, 1.0), 38, 47
                )

            elif phase == "degraded":
                # Devices start disconnecting as signal drops below threshold
                progress = phase_tick / self.DEGRADED_TICKS
                # Connected devices: 8.0 → 3.0 (progressive loss)
                target_devices = 8.0 - progress * 5.0
                gw.state["hospitality.iot.gateway_online"] = 1.0
                gw.state["hospitality.iot.gateway_connected_devices"] = clamp(
                    drift(target_devices, 0.8), 1, 9
                )
                # Gateway hardware STILL HEALTHY — this proves it's not a gateway issue
                gw.state["hospitality.iot.gateway_cpu_pct"] = clamp(
                    drift(44.0, 1.0), 40, 48
                )
                gw.state["hospitality.iot.gateway_memory_pct"] = clamp(
                    drift(43.0, 1.0), 38, 47
                )

            elif phase == "outage":
                # Devices bottomed out at ~1.9 per gateway (Bits finding)
                gw.state["hospitality.iot.gateway_online"] = 1.0  # Gateway itself is UP
                gw.state["hospitality.iot.gateway_connected_devices"] = clamp(
                    drift(1.9, 0.5), 0, 4
                )
                # Hardware STILL healthy — no restart events
                gw.state["hospitality.iot.gateway_cpu_pct"] = clamp(
                    drift(44.5, 1.0), 41, 48
                )
                gw.state["hospitality.iot.gateway_memory_pct"] = clamp(
                    drift(43.5, 1.0), 39, 47
                )

            elif phase == "recovering":
                # WiFi signal recovering → devices reconnecting
                progress = phase_tick / self.RECOVERY_TICKS
                gw.state["hospitality.iot.gateway_online"] = 1.0
                # Devices reconnecting: 1.9 → 7.5
                gw.state["hospitality.iot.gateway_connected_devices"] = clamp(
                    drift(1.9 + progress * 5.6, 0.8), 1, 10
                )
                gw.state["hospitality.iot.gateway_cpu_pct"] = clamp(
                    drift(44.0, 1.0), 40, 48
                )
                gw.state["hospitality.iot.gateway_memory_pct"] = clamp(
                    drift(42.0, 1.0), 38, 47
                )

    # =========================================================================
    # SECONDARY property: Luxury Collection APAC — Channel Saturation
    # =========================================================================

    def _apply_secondary_ap_overrides(self, phase: str, phase_tick: int) -> None:
        """
        Override Meraki AP metrics for the SECONDARY failure mode.

        DISTINCT from primary: Channel saturation (85%) vs client overload.
        - Channel utilization spikes to 85% (triggers separate alert)
        - Connected clients DROP from ~43 to ~11
        - Retransmit rate hits 10%
        """
        for ap in self._secondary_aps:
            if phase == "ramp_up":
                progress = phase_tick / self.RAMP_TICKS
                # Channel util climbing toward saturation
                ap.state["hospitality.network.channel_utilization_pct"] = clamp(
                    drift(35 + progress * 25, 2.0), 30, 65
                )
                ap.state["hospitality.network.connected_clients"] = clamp(
                    drift(43, 2.0), 38, 48
                )
                ap.state["hospitality.network.retransmit_pct"] = clamp(
                    drift(3.0 + progress * 3.0, 0.5), 2, 8
                )
                ap.state["hospitality.network.signal_strength_dbm"] = clamp(
                    drift(-45 - progress * 3, 0.5), -52, -40
                )

            elif phase == "degraded":
                progress = phase_tick / self.DEGRADED_TICKS
                # Channel saturated at 85%
                ap.state["hospitality.network.channel_utilization_pct"] = clamp(
                    drift(80 + progress * 5, 2.0), 72, 90
                )
                # Clients dropping as channel saturates: 43 → 20
                ap.state["hospitality.network.connected_clients"] = clamp(
                    drift(43 - progress * 23, 3.0), 10, 48
                )
                # Retransmit climbing to 10%
                ap.state["hospitality.network.retransmit_pct"] = clamp(
                    drift(6 + progress * 4, 1.0), 3, 14
                )
                ap.state["hospitality.network.signal_strength_dbm"] = clamp(
                    drift(-48 - progress * 5, 0.8), -58, -44
                )

            elif phase == "outage":
                # Channel fully saturated, mass disconnection
                ap.state["hospitality.network.channel_utilization_pct"] = clamp(
                    drift(85, 3.0), 78, 92
                )
                ap.state["hospitality.network.connected_clients"] = clamp(
                    drift(11, 2.0), 6, 18
                )
                ap.state["hospitality.network.retransmit_pct"] = clamp(
                    drift(10, 1.5), 6, 15
                )
                ap.state["hospitality.network.signal_strength_dbm"] = clamp(
                    drift(-55, 1.0), -60, -50
                )

            elif phase == "recovering":
                progress = phase_tick / self.RECOVERY_TICKS
                ap.state["hospitality.network.channel_utilization_pct"] = clamp(
                    drift(85 - progress * 50, 2.0), 28, 86
                )
                ap.state["hospitality.network.connected_clients"] = clamp(
                    drift(11 + progress * 30, 2.0), 10, 45
                )
                ap.state["hospitality.network.retransmit_pct"] = clamp(
                    drift(10 - progress * 7, 0.8), 2, 12
                )
                ap.state["hospitality.network.signal_strength_dbm"] = clamp(
                    drift(-55 + progress * 10, 0.8), -56, -42
                )

    def _apply_secondary_gateway_overrides(self, phase: str, phase_tick: int) -> None:
        """Override secondary property gateways — they lose devices from channel saturation."""
        for gw in self._secondary_gateways:
            if phase == "ramp_up":
                gw.state["hospitality.iot.gateway_online"] = 1.0
                gw.state["hospitality.iot.gateway_connected_devices"] = clamp(
                    drift(8.0, 0.5), 6, 10
                )
            elif phase in ("degraded", "outage"):
                progress = (
                    phase_tick / self.DEGRADED_TICKS if phase == "degraded"
                    else 0.8 + phase_tick / self.OUTAGE_TICKS * 0.2
                )
                gw.state["hospitality.iot.gateway_online"] = 1.0
                gw.state["hospitality.iot.gateway_connected_devices"] = clamp(
                    drift(8.0 - progress * 5.5, 1.0), 1, 9
                )
            elif phase == "recovering":
                progress = phase_tick / self.RECOVERY_TICKS
                gw.state["hospitality.iot.gateway_online"] = 1.0
                gw.state["hospitality.iot.gateway_connected_devices"] = clamp(
                    drift(2.5 + progress * 5.0, 0.8), 1, 10
                )

    # =========================================================================
    # Shared downstream impact (applied to PRIMARY property)
    # =========================================================================

    def _apply_lock_overrides(self, phase: str, phase_tick: int) -> None:
        """
        Override smart lock metrics during incident.

        LAYER 2b: Locks depend on gateway WiFi connectivity for remote operations.
        When signal degrades, locks lose their wireless link to gateways.
        """
        for lock in self._primary_locks:
            if phase == "ramp_up":
                # Locks unaffected during initial client surge
                lock.state["hospitality.device.online"] = 1.0
                lock.state["hospitality.lock.failures_total"] = 0.0

            elif phase == "degraded":
                progress = phase_tick / self.DEGRADED_TICKS
                if progress > 0.4:
                    lock.state["hospitality.lock.failures_total"] = clamp(
                        drift(1.5, 0.5), 0, 3
                    )
                    if random.random() < 0.25:
                        lock.state["hospitality.device.online"] = 0.0

            elif phase == "outage":
                lock.state["hospitality.lock.failures_total"] = clamp(
                    drift(2.5, 0.8), 0, 5
                )
                if random.random() < 0.45:
                    lock.state["hospitality.device.online"] = 0.0

            elif phase == "recovering":
                progress = phase_tick / self.RECOVERY_TICKS
                lock.state["hospitality.device.online"] = 1.0
                lock.state["hospitality.lock.failures_total"] = clamp(
                    drift(2.5 - progress * 2.5, 0.3), 0, 3
                )

    def _apply_thermostat_overrides(self, phase: str, phase_tick: int) -> None:
        """
        Override thermostat metrics during incident.

        LAYER 2c: Thermostats lose WiFi connectivity, rooms drift off setpoint.
        """
        for therm in self._primary_thermostats:
            if phase == "ramp_up":
                therm.state["hospitality.device.online"] = 1.0

            elif phase in ("degraded", "outage"):
                if random.random() < 0.5:
                    therm.state["hospitality.device.online"] = 0.0
                    # Temperature drifting without HVAC control
                    therm.state["hospitality.hvac.actual_f"] = clamp(
                        drift(therm.state.get("hospitality.hvac.actual_f", 72.0), 0.5, 0.3),
                        65, 82
                    )

            elif phase == "recovering":
                progress = phase_tick / self.RECOVERY_TICKS
                therm.state["hospitality.device.online"] = 1.0
                setpoint = therm.state.get("hospitality.hvac.setpoint_f", 72.0)
                actual = therm.state.get("hospitality.hvac.actual_f", 72.0)
                therm.state["hospitality.hvac.actual_f"] = actual + (setpoint - actual) * progress * 0.3

    def _apply_kiosk_overrides(self, phase: str, phase_tick: int) -> None:
        """
        Override lobby kiosk / check-in metrics during incident.

        LAYER 3 (Guest Impact): PEP check-in depends on IoT gateway + network.
        """
        for kiosk in self._primary_kiosks:
            if phase == "ramp_up":
                kiosk.state["hospitality.checkin.failures_total"] = 0.0

            elif phase == "degraded":
                progress = phase_tick / self.DEGRADED_TICKS
                if progress > 0.6:
                    kiosk.state["hospitality.checkin.failures_total"] = clamp(
                        drift(2.0, 0.8), 0, 5
                    )
                    kiosk.state["hospitality.checkin.avg_time_sec"] = clamp(
                        drift(90.0, 15.0), 60, 180
                    )

            elif phase == "outage":
                kiosk.state["hospitality.checkin.failures_total"] = clamp(
                    drift(4.0, 1.0), 1, 8
                )
                kiosk.state["hospitality.checkin.avg_time_sec"] = clamp(
                    drift(140.0, 20.0), 90, 200
                )

            elif phase == "recovering":
                progress = phase_tick / self.RECOVERY_TICKS
                kiosk.state["hospitality.checkin.failures_total"] = clamp(
                    drift(4.0 - progress * 3.5, 0.5), 0, 5
                )
                kiosk.state["hospitality.checkin.avg_time_sec"] = clamp(
                    drift(140.0 - progress * 100.0, 10.0), 20, 180
                )

    def _apply_snow_overrides(self, phase: str, phase_tick: int) -> None:
        """
        Override ServiceNow / IT Ops metrics during incident.

        LAYER 4 (Self-Healing): Datadog Workflow auto-creates incident,
        calls Meraki API to rate-limit rogue clients, tracks zero-touch resolution.
        """
        for snow in self._primary_snow:
            if phase == "ramp_up":
                pass

            elif phase == "degraded":
                progress = phase_tick / self.DEGRADED_TICKS
                if progress > 0.5:
                    snow.state["hospitality.ops.incidents_total"] = clamp(
                        drift(8.0, 2.0), 3, 15
                    )
                    snow.state["hospitality.ops.self_heal_success_total"] = clamp(
                        drift(2.0, 0.5), 0, 5
                    )
                    snow.state["hospitality.ops.mttr_minutes"] = clamp(
                        drift(45.0, 10.0), 15, 90
                    )

            elif phase == "outage":
                # Heavy incident load, self-healing kicking in
                snow.state["hospitality.ops.incidents_total"] = clamp(
                    drift(15.0, 3.0), 8, 25
                )
                snow.state["hospitality.ops.self_heal_success_total"] = clamp(
                    drift(5.0, 1.5), 2, 10
                )
                snow.state["hospitality.ops.self_heal_failure_total"] = clamp(
                    drift(2.0, 0.8), 0, 5
                )
                snow.state["hospitality.ops.onsite_dispatches_total"] = clamp(
                    drift(1.0, 0.5), 0, 3
                )
                snow.state["hospitality.ops.dispatches_avoided_total"] = clamp(
                    drift(10.0, 2.0), 5, 18
                )
                snow.state["hospitality.ops.mttr_minutes"] = clamp(
                    drift(55.0, 12.0), 20, 100
                )
                snow.state["hospitality.ops.zero_touch_resolution_rate"] = clamp(
                    drift(72.0, 5.0), 60, 85
                )

            elif phase == "recovering":
                progress = phase_tick / self.RECOVERY_TICKS
                # Self-healing succeeding — Meraki API rate-limited rogue clients
                snow.state["hospitality.ops.incidents_total"] = clamp(
                    drift(15.0 - progress * 12.0, 2.0), 1, 20
                )
                snow.state["hospitality.ops.self_heal_success_total"] = clamp(
                    drift(5.0 + progress * 6.0, 1.0), 3, 15
                )
                snow.state["hospitality.ops.self_heal_failure_total"] = clamp(
                    drift(2.0 - progress * 1.5, 0.3), 0, 3
                )
                snow.state["hospitality.ops.dispatches_avoided_total"] = clamp(
                    drift(10.0 + progress * 5.0, 1.0), 8, 20
                )
                snow.state["hospitality.ops.zero_touch_resolution_rate"] = clamp(
                    drift(72.0 + progress * 20.0, 3.0), 70, 96
                )
                snow.state["hospitality.ops.mttr_minutes"] = clamp(
                    drift(55.0 - progress * 45.0, 5.0), 6, 70
                )

    def _apply_ai_overrides(self, phase: str, phase_tick: int) -> None:
        """
        Override AI Stay Planner metrics during incident.

        LAYER 5 (Collateral): AI inference degrades when backend services
        are under load from the cascade.
        """
        for ai in self._primary_ai:
            if phase == "ramp_up":
                ai.state["hospitality.ai.inference_latency_ms"] = clamp(
                    drift(120.0, 15.0), 60, 250
                )

            elif phase in ("degraded", "outage"):
                ai.state["hospitality.ai.inference_latency_ms"] = clamp(
                    drift(450.0, 50.0), 200, 800
                )
                ai.state["hospitality.ai.recommendation_acceptance_rate"] = clamp(
                    drift(40.0, 5.0), 25, 55
                )

            elif phase == "recovering":
                progress = phase_tick / self.RECOVERY_TICKS
                ai.state["hospitality.ai.inference_latency_ms"] = clamp(
                    drift(450.0 - progress * 350.0, 20.0), 50, 500
                )
                ai.state["hospitality.ai.recommendation_acceptance_rate"] = clamp(
                    drift(40.0 + progress * 35.0, 3.0), 35, 85
                )
