"""
Ascension "Sensing Hospital" Care-Experience Cascade — sub-vertical: ascension

Tells the patient/care-provider end-user-experience story: an on-prem RTLS
location-service config drift degrades staff-badge location sensing, the
cloud Care Experience Platform can no longer route care-team requests to the
nearest caregiver, and the patient feels it — nurse-call acknowledgement
slows, perceived responsiveness drops, comfort requests go unmet, and the
bedside engagement tablet (an end-user device) lags.

This mirrors the AdventHealth sensing-hospital cascade exactly (same metric
namespaces, same phase model, same MedSurg/care_sensing scope), rebranded for
Ascension. The Ascension fleet additionally spans a `campus` dimension (~15
hospitals); the cascade is scoped by DEPARTMENT (MedSurg) and so degrades the
sensing fleet fleet-wide — across every campus at once — which reads as a
clear, system-wide anomaly rather than a single-room blip.

DISJOINTNESS (Style Guide §9.3) — this plugin is diagnosable in isolation
from the base Floor 3 East WiFi/pump cascade (`wifi_cascade.py`) along all
four axes. (Only one sub-vertical deploys at a time, so this plugin is never
co-resident with the AdventHealth overlay's plugin.)

  | Axis            | wifi_cascade               | this plugin                       |
  |-----------------|----------------------------|-----------------------------------|
  | Spatial         | Floor 3 East (pumps + APs) | department=MedSurg, care_sensing  |
  |                 |                            | device types (fleet-wide)         |
  | Metric namespace| hospital.network.*,        | hospital.sensing.*, hospital.cxp.*|
  |                 | device.signal/online       | (zero overlap)                    |
  | incident_domain | network-to-device          | care-experience                   |
  | Temporal        | initial idle 20-40 ticks   | initial idle 4-8 ticks; ~9-min    |
  |                 |                            | active window, then 80-120 idle   |

Spatial scope is by DEPARTMENT (MedSurg), fleet-wide across floors/wings/
campuses — the same lesson the BD Pyxis plugin documents: restricting to a
single floor/wing/campus would mutate only a handful of devices and let the
cascade signal get drowned by gauss-random-walk drift on the unaffected
majority. Even where a MedSurg room is physically on Floor 3 East, there is no
collision: this plugin only touches care_sensing device types and the
hospital.sensing.* / hospital.cxp.* namespaces, while wifi_cascade only
touches pumps/APs and hospital.network.* — so device_type + namespace +
incident_domain fully bifurcate the two stories.

Like the BD plugin, baselines are HELD during the 'normal' phase so the
subsequent cascade reads as a clear anomaly instead of disappearing into the
range-midpoint random walk.

Cascade narrative (see notebook ascension-care-experience-rca):

  Phase 1 — drift_up (8 ticks ≈ 2m):
      An on-prem rtls-location-service config push raises location-event
      sync lag. Only hospital.sensing.rtls_sync_lag_ms moves; care-team
      routing and patient signals still look healthy.
      (signal_chain: 1-root-cause)

  Phase 2 — routing_saturation (10 ticks ≈ 2m30s):
      Stale location data starves the cloud routing engine.
      hospital.cxp.care_team_routing_latency_ms climbs,
      hospital.sensing.location_staleness_sec increments monotonically,
      routing failures tick. (signal_chain: 2-leading-indicator)

  Phase 3 — experience_impact (12 ticks ≈ 3m):
      Patient-visible. Nurse-call acknowledgement latency spikes,
      perceived responsiveness score drops, comfort requests go unmet,
      and the bedside engagement tablet (EuD) round-trip slows.
      (signal_chain: 3-symptom)

  Phase 4 — recovery (10 ticks ≈ 2m30s):
      Automated repair pins rtls-location-service to last-good config and
      restarts the sync; staleness drains, routing recovers, the patient
      experience normalizes. (signal_chain: 5-recovery)
"""

import random
import logging
from typing import Any, Dict, List, Optional

from dd_demo_toolkit.simulator.plugins import IncidentPlugin

logger = logging.getLogger("ascension_care_experience_incident")


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _drift(value: float, magnitude: float = 1.0, bias: float = 0.0) -> float:
    return value + random.gauss(bias, magnitude)


class AscensionCareExperienceCascade(IncidentPlugin):
    """
    Ascension Sensing-Hospital care-experience cascade.

    RTLS location-sync degradation (on-prem) → care-team routing latency
    (cloud) → patient-visible responsiveness impact, scoped to the MedSurg
    care_sensing fleet (fleet-wide across the Ascension campuses).
    """

    # Phase durations (15s/tick by default → ~10min full cascade)
    DRIFT_UP_TICKS = 8            # 2m: RTLS sync lag climbing
    ROUTING_SATURATION_TICKS = 10  # 2m30s: routing latency growing
    EXPERIENCE_IMPACT_TICKS = 12  # 3m: patient-visible symptoms
    RECOVERY_TICKS = 10           # 2m30s: automated repair recovers

    EVENT_TICKS = (
        DRIFT_UP_TICKS
        + ROUTING_SATURATION_TICKS
        + EXPERIENCE_IMPACT_TICKS
        + RECOVERY_TICKS
    )

    # Spatial scope — disjoint from WiFi cascade by DEPARTMENT.
    INCIDENT_DEPARTMENT = "MedSurg"

    GATEWAY_TYPE = "room_sensing_gateway"
    TABLET_TYPE = "bedside_engagement_tablet"
    CLINICIAN_TYPE = "clinician_mobile"

    # Baselines held during 'normal' so the cascade peak reads as a clear
    # anomaly rather than random-walking toward each metric's midpoint.
    BASELINE_RTLS_LAG_MS = 150.0
    BASELINE_STALENESS_SEC = 6.0
    BASELINE_ROUTING_LAT_MS = 250.0
    BASELINE_CALL_ACK_SEC = 18.0
    BASELINE_RESPONSIVENESS = 92.0
    BASELINE_BEDSIDE_LAT_MS = 350.0
    BASELINE_EUD_RTT_MS = 60.0
    BASELINE_SECURE_MSG_MS = 300.0

    # Metric namespace — sensing/cxp only, no overlap with WiFi cascade.
    RTLS_LAG_METRIC = "hospital.sensing.rtls_sync_lag_ms"
    STALENESS_METRIC = "hospital.sensing.location_staleness_sec"
    SYNC_FAIL_METRIC = "hospital.sensing.sync_failures_total"
    ROUTING_LAT_METRIC = "hospital.cxp.care_team_routing_latency_ms"
    ROUTING_FAIL_METRIC = "hospital.cxp.routing_failures_total"
    CALL_ACK_METRIC = "hospital.cxp.call_ack_latency_sec"
    RESPONSIVENESS_METRIC = "hospital.cxp.responsiveness_score"
    COMFORT_UNMET_METRIC = "hospital.cxp.comfort_requests_unmet_total"
    BEDSIDE_LAT_METRIC = "hospital.cxp.bedside_app_latency_ms"
    EUD_RTT_METRIC = "hospital.eud.network_rtt_ms"
    EUD_HANG_METRIC = "hospital.eud.app_hang_total"
    SECURE_MSG_METRIC = "hospital.eud.secure_msg_latency_ms"

    def __init__(self) -> None:
        # Demo-friendly initial idle: fire within ~1-2 min of start. Kept
        # below the WiFi cascade's 20-40 tick idle so the two stories begin
        # at clearly different times on a fresh run.
        self._ticks_until_next = random.randint(4, 8)
        self._active_tick: Optional[int] = None
        self._incident_gateways: List[Any] = []
        self._incident_tablets: List[Any] = []
        self._incident_clinicians: List[Any] = []

        logger.info(
            "Ascension care-experience cascade initialized. First event "
            "in ~%d sec (department=%s, fleet-wide)",
            self._ticks_until_next * 15,
            self.INCIDENT_DEPARTMENT,
        )

    def get_incident_name(self) -> str:
        return (
            "Ascension Sensing-Hospital RTLS Location-Sync Degradation → "
            "Care-Team Routing → Patient-Experience Cascade (MedSurg)"
        )

    def reset(self) -> None:
        self._ticks_until_next = random.randint(4, 8)
        self._active_tick = None
        self._incident_gateways = []
        self._incident_tablets = []
        self._incident_clinicians = []

    # ------------------------------------------------------------------
    # Tick entry point
    # ------------------------------------------------------------------

    def on_tick(
        self,
        tick_count: int,
        fleet: List[Any],
        engine: Any,
    ) -> None:
        if not self._incident_gateways and not self._incident_tablets:
            for d in fleet:
                if self._matches_type(d, self.GATEWAY_TYPE):
                    self._incident_gateways.append(d)
                elif self._matches_type(d, self.TABLET_TYPE):
                    self._incident_tablets.append(d)
                elif self._matches_type(d, self.CLINICIAN_TYPE):
                    self._incident_clinicians.append(d)
            if self._incident_gateways:
                logger.info(
                    "Indexed %d sensing gateways + %d bedside tablets + "
                    "%d clinician mobiles in %s",
                    len(self._incident_gateways),
                    len(self._incident_tablets),
                    len(self._incident_clinicians),
                    self.INCIDENT_DEPARTMENT,
                )

        self._advance_clock()
        phase, phase_tick = self._current_phase()

        if hasattr(engine, "incident_state"):
            if phase == "normal":
                engine.incident_state.pop("ascension_care_experience", None)
            else:
                engine.incident_state["ascension_care_experience"] = {
                    "phase": phase,
                    "phase_tick": phase_tick,
                    "incident_domain": "care-experience",
                    "signal_chain_root": "rtls-location-sync",
                    "department": self.INCIDENT_DEPARTMENT,
                }

        self._apply_overrides(phase, phase_tick)
        if phase != "normal":
            logger.info(
                "CARE-EXPERIENCE CASCADE [%s t=%d] gateways=%d tablets=%d "
                "(department=%s)",
                phase,
                phase_tick,
                len(self._incident_gateways),
                len(self._incident_tablets),
                self.INCIDENT_DEPARTMENT,
            )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _matches_type(self, device: Any, dtype_wanted: str) -> bool:
        """True iff device is the wanted type and in the incident department.
        Filters by department only (not floor/wing/campus) so the cascade
        reads as a clear MedSurg-wide anomaly across the fleet rather than
        mutating a single room or campus."""
        dtype = getattr(device, "type", None) or (
            device.get("device_type") if isinstance(device, dict) else None
        )
        if dtype != dtype_wanted:
            return False
        loc = getattr(device, "location", None)
        if loc is None and isinstance(device, dict):
            loc = {"department": device.get("department")}
        if not loc:
            return False
        return loc.get("department") == self.INCIDENT_DEPARTMENT

    def _current_phase(self) -> tuple:
        if self._active_tick is None:
            return ("normal", 0)
        t = self._active_tick
        a = self.DRIFT_UP_TICKS
        b = a + self.ROUTING_SATURATION_TICKS
        c = b + self.EXPERIENCE_IMPACT_TICKS
        d = c + self.RECOVERY_TICKS
        if t < a:
            return ("drift_up", t)
        if t < b:
            return ("routing_saturation", t - a)
        if t < c:
            return ("experience_impact", t - b)
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
                    "Care-experience cascade complete. Next event in ~%d min",
                    self._ticks_until_next * 15 // 60,
                )
        else:
            self._ticks_until_next -= 1
            if self._ticks_until_next <= 0:
                self._active_tick = 0
                logger.info(
                    "CARE-EXPERIENCE CASCADE STARTING in department=%s",
                    self.INCIDENT_DEPARTMENT,
                )

    def _set_state(self, device: Any, metric: str, value: float) -> None:
        """Write metric value into device.state, supporting both shapes."""
        state = getattr(device, "state", None)
        if state is None and isinstance(device, dict):
            state = device.setdefault("state", {})
        if state is not None:
            state[metric] = value

    def _apply_overrides(self, phase: str, phase_tick: int) -> None:
        for gw in self._incident_gateways:
            if phase == "normal":
                self._set_state(gw, self.RTLS_LAG_METRIC,
                                _clamp(_drift(self.BASELINE_RTLS_LAG_MS, 25), 80, 300))
                self._set_state(gw, self.STALENESS_METRIC,
                                _clamp(_drift(self.BASELINE_STALENESS_SEC, 2), 0, 25))
                self._set_state(gw, self.ROUTING_LAT_METRIC,
                                _clamp(_drift(self.BASELINE_ROUTING_LAT_MS, 30), 120, 500))
                self._set_state(gw, self.CALL_ACK_METRIC,
                                _clamp(_drift(self.BASELINE_CALL_ACK_SEC, 3), 8, 35))
                self._set_state(gw, self.RESPONSIVENESS_METRIC,
                                _clamp(_drift(self.BASELINE_RESPONSIVENESS, 1.5), 82, 100))

            elif phase == "drift_up":
                progress = phase_tick / self.DRIFT_UP_TICKS
                # RTLS sync lag ramps from ~150ms baseline toward ~3.5s
                self._set_state(gw, self.RTLS_LAG_METRIC,
                                _clamp(_drift(150 + progress * 3350, 60), 100, 4000))
                # Everything downstream still healthy — the diagnostic gap
                self._set_state(gw, self.STALENESS_METRIC,
                                _clamp(_drift(8, 2), 0, 30))
                self._set_state(gw, self.ROUTING_LAT_METRIC,
                                _clamp(_drift(260, 30), 120, 600))
                self._set_state(gw, self.CALL_ACK_METRIC,
                                _clamp(_drift(18, 3), 8, 40))
                self._set_state(gw, self.RESPONSIVENESS_METRIC,
                                _clamp(_drift(91, 1.5), 80, 100))

            elif phase == "routing_saturation":
                progress = phase_tick / self.ROUTING_SATURATION_TICKS
                # RTLS lag sustained high
                self._set_state(gw, self.RTLS_LAG_METRIC,
                                _clamp(_drift(5500, 400), 4000, 9000))
                # Staleness climbs monotonically
                self._set_state(gw, self.STALENESS_METRIC,
                                _clamp(_drift(30 + progress * 240, 10), 20, 400))
                # Routing latency CLIMBS — leading indicator
                self._set_state(gw, self.ROUTING_LAT_METRIC,
                                _clamp(_drift(600 + progress * 6000, 250), 500, 9000))
                if random.random() < 0.3:
                    self._set_state(gw, self.ROUTING_FAIL_METRIC, random.randint(1, 3))
                # Patient signals still mostly normal — root-cause is upstream
                self._set_state(gw, self.CALL_ACK_METRIC,
                                _clamp(_drift(22 + progress * 15, 4), 12, 60))
                self._set_state(gw, self.RESPONSIVENESS_METRIC,
                                _clamp(_drift(88 - progress * 6, 1.5), 78, 96))

            elif phase == "experience_impact":
                # Sustained saturation; patient-visible symptoms peak
                self._set_state(gw, self.RTLS_LAG_METRIC,
                                _clamp(_drift(6500, 500), 4500, 9000))
                self._set_state(gw, self.STALENESS_METRIC,
                                _clamp(_drift(300 + phase_tick * 8, 15), 200, 600))
                self._set_state(gw, self.ROUTING_LAT_METRIC,
                                _clamp(_drift(7200, 500), 5000, 9000))
                self._set_state(gw, self.CALL_ACK_METRIC,
                                _clamp(_drift(150, 25), 90, 240))
                self._set_state(gw, self.RESPONSIVENESS_METRIC,
                                _clamp(_drift(58, 5), 40, 72))
                if random.random() < 0.4:
                    self._set_state(gw, self.ROUTING_FAIL_METRIC, random.randint(2, 5))
                if random.random() < 0.4:
                    self._set_state(gw, self.COMFORT_UNMET_METRIC, random.randint(1, 4))

            elif phase == "recovery":
                progress = phase_tick / self.RECOVERY_TICKS
                # Automated repair: config pinned, sync restarted
                self._set_state(gw, self.RTLS_LAG_METRIC,
                                _clamp(_drift(6500 - progress * 6300, 300), 120, 7000))
                self._set_state(gw, self.STALENESS_METRIC,
                                _clamp(_drift(300 - progress * 292, 10), 5, 320))
                self._set_state(gw, self.ROUTING_LAT_METRIC,
                                _clamp(_drift(7200 - progress * 6900, 250), 250, 7500))
                self._set_state(gw, self.CALL_ACK_METRIC,
                                _clamp(_drift(150 - progress * 130, 12), 16, 170))
                self._set_state(gw, self.RESPONSIVENESS_METRIC,
                                _clamp(_drift(58 + progress * 33, 2), 55, 94))

        # Bedside engagement tablet (EuD): app round-trip degrades only once
        # the cloud platform is choking, then recovers. Held at baseline
        # otherwise so the EuD impact is a clean, demonstrable symptom.
        for tablet in self._incident_tablets:
            if phase in ("experience_impact",):
                self._set_state(tablet, self.BEDSIDE_LAT_METRIC,
                                _clamp(_drift(3800, 500), 2500, 6000))
                if random.random() < 0.15:
                    self._set_state(tablet, "hospital.cxp.bedside_crash_total",
                                    random.randint(1, 2))
            elif phase == "routing_saturation":
                progress = phase_tick / self.ROUTING_SATURATION_TICKS
                self._set_state(tablet, self.BEDSIDE_LAT_METRIC,
                                _clamp(_drift(800 + progress * 2200, 200), 400, 4000))
            elif phase == "recovery":
                progress = phase_tick / self.RECOVERY_TICKS
                self._set_state(tablet, self.BEDSIDE_LAT_METRIC,
                                _clamp(_drift(3800 - progress * 3400, 250), 350, 4200))
            else:
                self._set_state(tablet, self.BEDSIDE_LAT_METRIC,
                                _clamp(_drift(self.BASELINE_BEDSIDE_LAT_MS, 60), 150, 800))

        # End-user-device (EuD) experience — both the patient tablet and the
        # care-provider mobile. On-device network RTT climbs as the cloud
        # platform saturates (the device "feels slow"); clinician secure-
        # messaging latency follows. Held at baseline otherwise so the EuD
        # lens shows a clean, demonstrable degrade-and-recover during the
        # cascade without overlapping the WiFi cascade's network namespace.
        for dev in self._incident_tablets + self._incident_clinicians:
            is_clinician = dev in self._incident_clinicians
            if phase == "routing_saturation":
                progress = phase_tick / self.ROUTING_SATURATION_TICKS
                self._set_state(dev, self.EUD_RTT_METRIC,
                                _clamp(_drift(200 + progress * 1600, 150), 60, 4000))
                if is_clinician:
                    self._set_state(dev, self.SECURE_MSG_METRIC,
                                    _clamp(_drift(500 + progress * 2000, 200), 200, 5000))
            elif phase == "experience_impact":
                self._set_state(dev, self.EUD_RTT_METRIC,
                                _clamp(_drift(2400, 400), 1500, 4000))
                if is_clinician:
                    self._set_state(dev, self.SECURE_MSG_METRIC,
                                    _clamp(_drift(3200, 500), 2000, 6000))
                if random.random() < 0.12:
                    self._set_state(dev, self.EUD_HANG_METRIC, random.randint(1, 3))
            elif phase == "recovery":
                progress = phase_tick / self.RECOVERY_TICKS
                self._set_state(dev, self.EUD_RTT_METRIC,
                                _clamp(_drift(2400 - progress * 2300, 200), 60, 2600))
                if is_clinician:
                    self._set_state(dev, self.SECURE_MSG_METRIC,
                                    _clamp(_drift(3200 - progress * 2850, 250), 250, 3400))
            else:
                self._set_state(dev, self.EUD_RTT_METRIC,
                                _clamp(_drift(self.BASELINE_EUD_RTT_MS, 12), 20, 200))
                if is_clinician:
                    self._set_state(dev, self.SECURE_MSG_METRIC,
                                    _clamp(_drift(self.BASELINE_SECURE_MSG_MS, 50), 120, 700))
