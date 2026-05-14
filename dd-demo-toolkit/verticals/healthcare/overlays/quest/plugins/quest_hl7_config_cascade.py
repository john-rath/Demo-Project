"""
Quest Diagnostics HL7 Integration Engine Config-Push Cascade
sub-vertical: quest

This plugin drives the BitsSRE problem pattern for the Quest overlay.
The cascade simulates a bad routing-rule config push to the HL7
integration engine (Rhapsody/Mirth-class software Quest owns and runs
in front of every outbound result feed to ordering-provider EHRs).
The cascade is designed to be DIAGNOSABLE in isolation from the
existing Floor 3 East WiFi/pump cascade (`wifi_cascade.py`) and the
BD Pyxis cascade by being explicitly disjoint along four axes:

  1. **Spatial** -- affects every Quest-overlay device in
     `department=Lab` across all floors and wings (LIMS cluster nodes,
     HL7 engine cluster nodes, centrifuges, specimen sorters,
     refrigerated storage). The Lab department is disjoint from the
     WiFi cascade's ED/ICU footprint on Floor 3 East and from the BD
     overlay's Pharmacy footprint. We intentionally do NOT restrict
     to a single floor/wing because the Lab fleet spans the entire
     building -- a config-push regression is global to the engine
     cluster, not site-specific.

  2. **Metric namespace** -- only mutates
     `hospital.hl7.*`, `hospital.lims.*`, `hospital.tat.*`,
     `hospital.specimen.*`, and `hospital.app.errors_total{service_name:
     provider-results-portal}`. Zero overlap with `hospital.pump.*`,
     `hospital.network.*`, `hospital.pyxis.*`, or any of the base
     healthcare medical-device metrics.

  3. **Incident-domain tag** -- emits state into `engine.incident_state`
     under `quest_hl7_config_cascade` with
     `incident_domain=diagnostic-laboratory` (vs. the WiFi cascade's
     `network-to-device` and the BD cascade's `pharmacy-automation`).
     Bits AI SRE filtering by `incident_domain:diagnostic-laboratory`
     will not pick up either of the other cascades.

  4. **Time delta** -- WiFi cascade fires at tick 20-40 with ~40-tick
     duration. Quest cascade initial idle is 90-130 ticks (well past
     the WiFi cascade's first-event tail), satisfying STYLE_GUIDE §9.3
     "initial idle >= 50 ticks more than other plugins". Inter-event
     idle is 100-150 ticks. The BD plugin is NOT loaded alongside Quest
     (one sub-vertical at a time per `--sub-vertical` flag) so we only
     need temporal disjoint from the base WiFi cascade.

The plugin HOLDS BASELINE VALUES during the 'normal' phase on every
targeted device. Without this, gauss random walk pulls each metric
toward its declared range midpoint -- for `hospital.hl7.outbound_queue_depth`
that midpoint is 6000, which is close to the cascade peak of ~8000, so
the spike would vanish into noise. Holding the baselines at the LOW
end of each range keeps the cascade signal unambiguous.

Cascade narrative (see notebook `quest-lab-integration-cascade-rca` for
the full BitsSRE walkthrough):

  Phase 1 -- config_push (4 ticks ≈ 1m):
      A scheduled deploy to the HL7 integration engine pushes a
      malformed outbound routing rule. `hospital.hl7.config_version`
      increments. `hospital.hl7.routing_errors_total` starts climbing.
      Queue depth still healthy. (signal_chain: 1-root-cause)

  Phase 2 -- queue_buildup (8 ticks ≈ 2m):
      Outbound HL7 ORU^R01 messages start being NACKed by receiving
      EHRs (the bad route produces malformed payloads). Worker thread
      pool starts retry-spinning. `hospital.hl7.outbound_queue_depth`
      climbs from ~150 baseline toward 8000.
      `hospital.hl7.worker_thread_utilization_pct` saturates >90%.
      `hospital.hl7.nack_received_total` and
      `hospital.hl7.worker_thread_retries_total` climb. ACK counter
      flattens. (signal_chain: 2-leading-indicator)

  Phase 3 -- lims_backpressure (8 ticks ≈ 2m):
      LIMS feels the back-pressure: results sit in 'pending delivery'
      because the HL7 engine cannot accept them.
      `hospital.lims.results_pending_delivery` climbs from ~100
      baseline toward 2500. `hospital.lims.hl7_writes_failed_total`
      spikes as LIMS retries time out. `hospital.lims.db_pool_active`
      climbs toward 180 (out of pool capacity 200) as retries hold
      connections. (signal_chain: 3-symptom)

  Phase 4 -- operations_impact (10 ticks ≈ 2m30s):
      With LIMS slowed, the lab cannot accept new orders fast enough.
      Specimens pile up at accessioning:
      `hospital.specimen.queue_depth_pre_analytic` climbs to ~200,
      `hospital.specimen.queue_depth_analytic` follows, and
      `hospital.specimen.refrigerated_capacity_pct` rises toward 92%.
      Worst-case specimen `hospital.specimen.stability_min_remaining`
      drops to ~40 min. Customer-facing TAT counters tick:
      `hospital.tat.stat_breaches_total` and
      `hospital.tat.routine_breaches_total` accumulate; in-flight
      counts climb. (signal_chain: 4-cascade + 5-business-impact)

  Phase 5 -- recovery (10 ticks ≈ 2m30s):
      Self-heal workflow rolls the HL7 engine back to the previous
      config version. Worker threads stop retry-spinning. Queue
      drains. LIMS results-pending falls back to baseline. Pre-analytic
      and refrigerated storage recover as the backlog clears. TAT
      breach counters stop climbing (already-breached orders cannot be
      un-breached, but no new breaches accumulate).
      (signal_chain: 5-recovery)

The plugin writes the raw values into `device.state[<metric>]` for the
duration of the incident, then lets the engine's normal drift logic
resume.
"""

import random
import logging
from typing import Any, Dict, List, Optional

from dd_demo_toolkit.simulator.plugins import IncidentPlugin

logger = logging.getLogger("quest_hl7_config_cascade_incident")


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _drift(value: float, magnitude: float = 1.0, bias: float = 0.0) -> float:
    return value + random.gauss(bias, magnitude)


class QuestHL7ConfigCascade(IncidentPlugin):
    """
    Quest HL7 integration engine bad-config-push cascade.

    Targets all Quest-overlay devices in `department=Lab` across all
    floors and wings, chosen specifically to be disjoint from the
    WiFi cascade's Floor 3 East footprint and the BD cascade's
    Pharmacy footprint.
    """

    # Phase durations (15s/tick by default → ~10min full cascade)
    CONFIG_PUSH_TICKS = 4            # 1m: routing errors climbing
    QUEUE_BUILDUP_TICKS = 8          # 2m: HL7 queue + worker saturation
    LIMS_BACKPRESSURE_TICKS = 8      # 2m: LIMS feeling pain
    OPERATIONS_IMPACT_TICKS = 10     # 2m30s: specimens + TAT breaches
    RECOVERY_TICKS = 10              # 2m30s: workflow rollback

    EVENT_TICKS = (
        CONFIG_PUSH_TICKS
        + QUEUE_BUILDUP_TICKS
        + LIMS_BACKPRESSURE_TICKS
        + OPERATIONS_IMPACT_TICKS
        + RECOVERY_TICKS
    )

    # Spatial scope -- disjoint from WiFi (3 East / ED-ICU) and BD
    # (Pharmacy / all floors) by DEPARTMENT.
    INCIDENT_DEPARTMENT = "Lab"

    # Device types the cascade touches. Grouped by the cascade phase
    # they participate in.
    HL7_ENGINE_TYPE = "hl7_integration_engine"
    LIMS_TYPE = "lims_app_server"
    CENTRIFUGE_TYPE = "centrifuge"
    SORTER_TYPE = "specimen_sorter"
    FRIDGE_TYPE = "refrigerated_storage"

    # Baseline values held during 'normal' phase. Anchored at the LOWER
    # end of each metric's declared range (see quest.yaml) so the
    # cascade peak reads as a clear anomaly. Without this, gauss random
    # walk pulls each value toward the range midpoint and the cascade
    # delta vanishes into noise.
    BASELINE_HL7_QUEUE = 150.0
    BASELINE_HL7_WORKER_UTIL_PCT = 35.0
    BASELINE_HL7_CONFIG_VERSION = 47.0  # arbitrary anchor
    BASELINE_LIMS_PENDING = 120.0
    BASELINE_LIMS_DB_POOL = 35.0
    BASELINE_SPECIMEN_PRE_QUEUE = 18.0
    BASELINE_SPECIMEN_AN_QUEUE = 40.0
    BASELINE_FRIDGE_CAPACITY_PCT = 50.0
    BASELINE_STABILITY_MIN = 350.0

    # Metric namespace -- HL7/LIMS/TAT/specimen only, zero overlap
    # with WiFi or BD cascades.
    HL7_QUEUE_METRIC = "hospital.hl7.outbound_queue_depth"
    HL7_WORKER_UTIL_METRIC = "hospital.hl7.worker_thread_utilization_pct"
    HL7_CONFIG_VERSION_METRIC = "hospital.hl7.config_version"
    HL7_ROUTING_ERRORS_METRIC = "hospital.hl7.routing_errors_total"
    HL7_NACK_METRIC = "hospital.hl7.nack_received_total"
    HL7_ACK_METRIC = "hospital.hl7.ack_received_total"
    HL7_RETRY_METRIC = "hospital.hl7.worker_thread_retries_total"
    HL7_SENT_METRIC = "hospital.hl7.messages_sent_total"
    HL7_DELIVERED_METRIC = "hospital.hl7.messages_delivered_total"

    LIMS_PENDING_METRIC = "hospital.lims.results_pending_delivery"
    LIMS_DB_POOL_METRIC = "hospital.lims.db_pool_active"
    LIMS_HL7_FAIL_METRIC = "hospital.lims.hl7_writes_failed_total"

    PRE_QUEUE_METRIC = "hospital.specimen.queue_depth_pre_analytic"
    AN_QUEUE_METRIC = "hospital.specimen.queue_depth_analytic"
    FRIDGE_CAP_METRIC = "hospital.specimen.refrigerated_capacity_pct"
    STABILITY_METRIC = "hospital.specimen.stability_min_remaining"

    TAT_STAT_BREACH_METRIC = "hospital.tat.stat_breaches_total"
    TAT_ROUTINE_BREACH_METRIC = "hospital.tat.routine_breaches_total"
    TAT_STAT_IN_FLIGHT = "hospital.tat.stat_in_flight_count"

    def __init__(self) -> None:
        # Per STYLE_GUIDE §9.3 the initial idle must be >= 50 ticks more
        # than every other plugin in the vertical. WiFi cascade idles
        # 20-40 ticks. We use 90-130 to land past its first-event tail
        # with margin.
        self._ticks_until_next = random.randint(90, 130)
        self._active_tick: Optional[int] = None
        self._hl7_engines: List[Dict[str, Any]] = []
        self._lims_nodes: List[Dict[str, Any]] = []
        self._centrifuges: List[Dict[str, Any]] = []
        self._sorters: List[Dict[str, Any]] = []
        self._fridges: List[Dict[str, Any]] = []
        # Config version increments once per cascade so the time-series
        # for `hospital.hl7.config_version` shows an obvious step
        # immediately before routing errors begin.
        self._current_config_version = self.BASELINE_HL7_CONFIG_VERSION

        logger.info(
            "Quest HL7 cascade initialized. First event in ~%d sec "
            "(department=%s, fleet-wide)",
            self._ticks_until_next * 15,
            self.INCIDENT_DEPARTMENT,
        )

    def get_incident_name(self) -> str:
        return (
            "Quest HL7 Integration Engine Config-Push Cascade → "
            "LIMS Backpressure → Specimen Backlog → TAT SLA Breach "
            "(Lab department, fleet-wide)"
        )

    def reset(self) -> None:
        self._ticks_until_next = random.randint(90, 130)
        self._active_tick = None
        self._hl7_engines = []
        self._lims_nodes = []
        self._centrifuges = []
        self._sorters = []
        self._fridges = []
        self._current_config_version = self.BASELINE_HL7_CONFIG_VERSION

    # ------------------------------------------------------------------
    # Tick entry point
    # ------------------------------------------------------------------

    def on_tick(
        self,
        tick_count: int,
        fleet: List[Dict[str, Any]],
        engine: Any,
    ) -> None:
        # Lazy-index target devices by type. We index every Quest-overlay
        # device in department=Lab; the cascade spans the whole fleet
        # because the HL7 engine and LIMS clusters serve the entire lab.
        if not self._hl7_engines:
            for d in fleet:
                if not self._is_in_lab(d):
                    continue
                dtype = self._device_type(d)
                if dtype == self.HL7_ENGINE_TYPE:
                    self._hl7_engines.append(d)
                elif dtype == self.LIMS_TYPE:
                    self._lims_nodes.append(d)
                elif dtype == self.CENTRIFUGE_TYPE:
                    self._centrifuges.append(d)
                elif dtype == self.SORTER_TYPE:
                    self._sorters.append(d)
                elif dtype == self.FRIDGE_TYPE:
                    self._fridges.append(d)
            if self._hl7_engines or self._lims_nodes:
                logger.info(
                    "Indexed Quest Lab fleet: %d HL7 engine nodes, "
                    "%d LIMS nodes, %d centrifuges, %d sorters, "
                    "%d refrigerators",
                    len(self._hl7_engines),
                    len(self._lims_nodes),
                    len(self._centrifuges),
                    len(self._sorters),
                    len(self._fridges),
                )

        self._advance_clock()
        phase, phase_tick = self._current_phase()

        # Publish phase to the engine's shared incident_state so other
        # subsystems (and Bits-AI-SRE-style queries that read it via
        # tags) can see the active narrative.
        if hasattr(engine, "incident_state"):
            if phase == "normal":
                engine.incident_state.pop("quest_hl7_config_cascade", None)
            else:
                engine.incident_state["quest_hl7_config_cascade"] = {
                    "phase": phase,
                    "phase_tick": phase_tick,
                    "incident_domain": "diagnostic-laboratory",
                    "signal_chain_root": "hl7-config-push",
                    "department": self.INCIDENT_DEPARTMENT,
                }

        # Apply overrides every tick, including 'normal' (so baselines
        # hold and the cascade reads as a clear anomaly to AI RCA tools).
        self._apply_overrides(phase, phase_tick)

        if phase != "normal":
            logger.info(
                "QUEST HL7 CASCADE [%s t=%d] hl7=%d lims=%d "
                "centrifuges=%d sorters=%d fridges=%d (dept=%s)",
                phase,
                phase_tick,
                len(self._hl7_engines),
                len(self._lims_nodes),
                len(self._centrifuges),
                len(self._sorters),
                len(self._fridges),
                self.INCIDENT_DEPARTMENT,
            )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _device_type(self, device: Any) -> Optional[str]:
        return getattr(device, "type", None) or (
            device.get("device_type") if isinstance(device, dict) else None
        )

    def _is_in_lab(self, device: Any) -> bool:
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
        a = self.CONFIG_PUSH_TICKS
        b = a + self.QUEUE_BUILDUP_TICKS
        c = b + self.LIMS_BACKPRESSURE_TICKS
        d = c + self.OPERATIONS_IMPACT_TICKS
        e = d + self.RECOVERY_TICKS
        if t < a:
            return ("config_push", t)
        if t < b:
            return ("queue_buildup", t - a)
        if t < c:
            return ("lims_backpressure", t - b)
        if t < d:
            return ("operations_impact", t - c)
        if t < e:
            return ("recovery", t - d)
        return ("normal", 0)

    def _advance_clock(self) -> None:
        if self._active_tick is not None:
            self._active_tick += 1
            if self._active_tick >= self.EVENT_TICKS:
                self._active_tick = None
                # Inter-event idle 100-150 ticks. Disjoint from WiFi
                # cascade's typical inter-event cadence.
                self._ticks_until_next = random.randint(100, 150)
                logger.info(
                    "Quest HL7 cascade complete. Next event in ~%d min",
                    self._ticks_until_next * 15 // 60,
                )
        else:
            self._ticks_until_next -= 1
            if self._ticks_until_next <= 0:
                self._active_tick = 0
                # Increment config version once at cascade start so the
                # `hospital.hl7.config_version` time-series shows an
                # obvious step immediately before the routing errors
                # begin -- this is the Bits-AI-SRE smoking gun.
                self._current_config_version += 1
                logger.info(
                    "QUEST HL7 CASCADE STARTING in %s "
                    "(config_version=%d, HL7 fleet=%d nodes)",
                    self.INCIDENT_DEPARTMENT,
                    int(self._current_config_version),
                    len(self._hl7_engines),
                )

    def _set_state(self, device: Any, metric: str, value: float) -> None:
        """Write metric value into device.state, supporting both shapes."""
        state = getattr(device, "state", None)
        if state is None and isinstance(device, dict):
            state = device.setdefault("state", {})
        if state is not None:
            state[metric] = value

    def _apply_overrides(self, phase: str, phase_tick: int) -> None:
        # ---------- HL7 engine cluster ----------
        for node in self._hl7_engines:
            if phase == "normal":
                self._set_state(node, self.HL7_QUEUE_METRIC,
                    _clamp(_drift(self.BASELINE_HL7_QUEUE, 25), 50, 400))
                self._set_state(node, self.HL7_WORKER_UTIL_METRIC,
                    _clamp(_drift(self.BASELINE_HL7_WORKER_UTIL_PCT, 4), 20, 55))
                self._set_state(node, self.HL7_CONFIG_VERSION_METRIC,
                    self._current_config_version)
                # Healthy ACK/sent flow
                if random.random() < 0.6:
                    self._set_state(node, self.HL7_SENT_METRIC,
                        random.randint(80, 110))
                    self._set_state(node, self.HL7_ACK_METRIC,
                        random.randint(78, 108))
                    self._set_state(node, self.HL7_DELIVERED_METRIC,
                        random.randint(78, 108))

            elif phase == "config_push":
                # Config bumped THIS tick. Routing errors START climbing
                # but queue still mostly normal (it takes a moment for
                # NACK retries to accumulate).
                progress = phase_tick / self.CONFIG_PUSH_TICKS
                self._set_state(node, self.HL7_CONFIG_VERSION_METRIC,
                    self._current_config_version)
                self._set_state(node, self.HL7_ROUTING_ERRORS_METRIC,
                    random.randint(int(8 + progress * 25),
                                   int(15 + progress * 40)))
                self._set_state(node, self.HL7_QUEUE_METRIC,
                    _clamp(_drift(200 + progress * 600, 80), 100, 1200))
                self._set_state(node, self.HL7_WORKER_UTIL_METRIC,
                    _clamp(_drift(40 + progress * 25, 4), 30, 80))

            elif phase == "queue_buildup":
                progress = phase_tick / self.QUEUE_BUILDUP_TICKS
                # Queue climbs hard, worker pool saturates, NACKs spike
                self._set_state(node, self.HL7_CONFIG_VERSION_METRIC,
                    self._current_config_version)
                self._set_state(node, self.HL7_QUEUE_METRIC,
                    _clamp(_drift(1200 + progress * 6500, 400), 800, 10000))
                self._set_state(node, self.HL7_WORKER_UTIL_METRIC,
                    _clamp(_drift(70 + progress * 25, 3), 65, 99))
                self._set_state(node, self.HL7_ROUTING_ERRORS_METRIC,
                    random.randint(30, 70))
                # Sent stays high (engine is trying), ACK collapses
                if random.random() < 0.7:
                    self._set_state(node, self.HL7_SENT_METRIC,
                        random.randint(85, 115))
                    self._set_state(node, self.HL7_NACK_METRIC,
                        random.randint(int(15 + progress * 50),
                                       int(40 + progress * 70)))
                    self._set_state(node, self.HL7_ACK_METRIC,
                        random.randint(20, 50))
                    self._set_state(node, self.HL7_DELIVERED_METRIC,
                        random.randint(20, 50))
                    self._set_state(node, self.HL7_RETRY_METRIC,
                        random.randint(8, 25))

            elif phase == "lims_backpressure":
                progress = phase_tick / self.LIMS_BACKPRESSURE_TICKS
                # Sustained saturation on the engine side
                self._set_state(node, self.HL7_CONFIG_VERSION_METRIC,
                    self._current_config_version)
                self._set_state(node, self.HL7_QUEUE_METRIC,
                    _clamp(_drift(8200, 350), 7000, 10500))
                self._set_state(node, self.HL7_WORKER_UTIL_METRIC,
                    _clamp(_drift(96, 2), 90, 99))
                self._set_state(node, self.HL7_ROUTING_ERRORS_METRIC,
                    random.randint(50, 90))
                if random.random() < 0.7:
                    self._set_state(node, self.HL7_SENT_METRIC,
                        random.randint(85, 115))
                    self._set_state(node, self.HL7_NACK_METRIC,
                        random.randint(60, 100))
                    self._set_state(node, self.HL7_ACK_METRIC,
                        random.randint(15, 35))
                    self._set_state(node, self.HL7_RETRY_METRIC,
                        random.randint(18, 35))

            elif phase == "operations_impact":
                # Sustained engine saturation continues
                self._set_state(node, self.HL7_CONFIG_VERSION_METRIC,
                    self._current_config_version)
                self._set_state(node, self.HL7_QUEUE_METRIC,
                    _clamp(_drift(8500, 400), 7200, 10800))
                self._set_state(node, self.HL7_WORKER_UTIL_METRIC,
                    _clamp(_drift(97, 1.5), 92, 99))
                self._set_state(node, self.HL7_ROUTING_ERRORS_METRIC,
                    random.randint(55, 95))
                if random.random() < 0.7:
                    self._set_state(node, self.HL7_NACK_METRIC,
                        random.randint(65, 105))
                    self._set_state(node, self.HL7_ACK_METRIC,
                        random.randint(15, 35))

            elif phase == "recovery":
                # Workflow rolled back config -- routing errors drop,
                # queue drains, worker pool recovers.
                progress = phase_tick / self.RECOVERY_TICKS
                # Config version stays at the new (rolled-back-to)
                # version; the increment of the gauge captured the
                # event already.
                self._set_state(node, self.HL7_CONFIG_VERSION_METRIC,
                    self._current_config_version)
                self._set_state(node, self.HL7_QUEUE_METRIC,
                    _clamp(_drift(8500 - progress * 8200, 300), 100, 9000))
                self._set_state(node, self.HL7_WORKER_UTIL_METRIC,
                    _clamp(_drift(95 - progress * 55, 4), 30, 99))
                self._set_state(node, self.HL7_ROUTING_ERRORS_METRIC,
                    random.randint(0, 3))
                if random.random() < 0.7:
                    self._set_state(node, self.HL7_SENT_METRIC,
                        random.randint(80, 110))
                    self._set_state(node, self.HL7_ACK_METRIC,
                        random.randint(int(40 + progress * 60),
                                       int(80 + progress * 30)))
                    self._set_state(node, self.HL7_DELIVERED_METRIC,
                        random.randint(int(40 + progress * 60),
                                       int(80 + progress * 30)))
                    self._set_state(node, self.HL7_NACK_METRIC,
                        random.randint(0, 5))

        # ---------- LIMS cluster ----------
        for node in self._lims_nodes:
            if phase == "normal":
                self._set_state(node, self.LIMS_PENDING_METRIC,
                    _clamp(_drift(self.BASELINE_LIMS_PENDING, 20), 50, 350))
                self._set_state(node, self.LIMS_DB_POOL_METRIC,
                    _clamp(_drift(self.BASELINE_LIMS_DB_POOL, 5), 20, 70))

            elif phase in ("config_push", "queue_buildup"):
                # LIMS not yet feeling pain
                self._set_state(node, self.LIMS_PENDING_METRIC,
                    _clamp(_drift(180, 30), 80, 500))
                self._set_state(node, self.LIMS_DB_POOL_METRIC,
                    _clamp(_drift(40, 5), 25, 75))

            elif phase == "lims_backpressure":
                progress = phase_tick / self.LIMS_BACKPRESSURE_TICKS
                # Results-pending climbs hard; db pool climbs toward
                # saturation; hl7 write failures spike.
                self._set_state(node, self.LIMS_PENDING_METRIC,
                    _clamp(_drift(300 + progress * 2200, 120), 200, 2800))
                self._set_state(node, self.LIMS_DB_POOL_METRIC,
                    _clamp(_drift(60 + progress * 120, 8), 50, 195))
                if random.random() < 0.5:
                    self._set_state(node, self.LIMS_HL7_FAIL_METRIC,
                        random.randint(4, 12))

            elif phase == "operations_impact":
                # Sustained backpressure on LIMS
                self._set_state(node, self.LIMS_PENDING_METRIC,
                    _clamp(_drift(2600, 200), 2000, 3000))
                self._set_state(node, self.LIMS_DB_POOL_METRIC,
                    _clamp(_drift(185, 6), 170, 200))
                if random.random() < 0.6:
                    self._set_state(node, self.LIMS_HL7_FAIL_METRIC,
                        random.randint(8, 20))
                # TAT breach counters tick on the lims node (LIMS is the
                # source of truth for TAT).
                if random.random() < 0.7:
                    self._set_state(node, self.TAT_STAT_BREACH_METRIC,
                        random.randint(1, 3))
                    self._set_state(node, self.TAT_ROUTINE_BREACH_METRIC,
                        random.randint(6, 18))
                    self._set_state(node, self.TAT_STAT_IN_FLIGHT,
                        _clamp(_drift(45, 5), 30, 60))

            elif phase == "recovery":
                progress = phase_tick / self.RECOVERY_TICKS
                self._set_state(node, self.LIMS_PENDING_METRIC,
                    _clamp(_drift(2600 - progress * 2500, 150), 100, 2800))
                self._set_state(node, self.LIMS_DB_POOL_METRIC,
                    _clamp(_drift(180 - progress * 140, 7), 35, 195))
                # Failures stop accumulating (counter resets each tick;
                # writing 0 means no new failures this tick).
                self._set_state(node, self.LIMS_HL7_FAIL_METRIC, 0)
                # In-flight STAT count drains as the queue clears
                self._set_state(node, self.TAT_STAT_IN_FLIGHT,
                    _clamp(_drift(45 - progress * 35, 4), 8, 55))

        # ---------- Specimen pre-analytic (centrifuges) ----------
        for centrifuge in self._centrifuges:
            if phase in ("normal", "config_push", "queue_buildup"):
                self._set_state(centrifuge, self.PRE_QUEUE_METRIC,
                    _clamp(_drift(self.BASELINE_SPECIMEN_PRE_QUEUE, 5), 5, 50))

            elif phase == "lims_backpressure":
                progress = phase_tick / self.LIMS_BACKPRESSURE_TICKS
                # Queue starts growing as LIMS can't accession fast enough
                self._set_state(centrifuge, self.PRE_QUEUE_METRIC,
                    _clamp(_drift(40 + progress * 120, 18), 20, 220))

            elif phase == "operations_impact":
                progress = phase_tick / self.OPERATIONS_IMPACT_TICKS
                self._set_state(centrifuge, self.PRE_QUEUE_METRIC,
                    _clamp(_drift(160 + progress * 60, 15), 140, 240))

            elif phase == "recovery":
                progress = phase_tick / self.RECOVERY_TICKS
                self._set_state(centrifuge, self.PRE_QUEUE_METRIC,
                    _clamp(_drift(220 - progress * 195, 18), 20, 230))

        # ---------- Specimen analytic (sorters) ----------
        for sorter in self._sorters:
            if phase in ("normal", "config_push", "queue_buildup"):
                self._set_state(sorter, self.AN_QUEUE_METRIC,
                    _clamp(_drift(self.BASELINE_SPECIMEN_AN_QUEUE, 10), 15, 120))

            elif phase == "lims_backpressure":
                progress = phase_tick / self.LIMS_BACKPRESSURE_TICKS
                self._set_state(sorter, self.AN_QUEUE_METRIC,
                    _clamp(_drift(80 + progress * 180, 30), 50, 400))

            elif phase == "operations_impact":
                self._set_state(sorter, self.AN_QUEUE_METRIC,
                    _clamp(_drift(310, 30), 200, 450))

            elif phase == "recovery":
                progress = phase_tick / self.RECOVERY_TICKS
                self._set_state(sorter, self.AN_QUEUE_METRIC,
                    _clamp(_drift(310 - progress * 270, 25), 40, 380))

        # ---------- Refrigerated storage ----------
        for fridge in self._fridges:
            if phase in ("normal", "config_push", "queue_buildup"):
                self._set_state(fridge, self.FRIDGE_CAP_METRIC,
                    _clamp(_drift(self.BASELINE_FRIDGE_CAPACITY_PCT, 4), 35, 70))
                self._set_state(fridge, self.STABILITY_METRIC,
                    _clamp(_drift(self.BASELINE_STABILITY_MIN, 25), 250, 460))

            elif phase == "lims_backpressure":
                progress = phase_tick / self.LIMS_BACKPRESSURE_TICKS
                # Capacity climbs as backlog accumulates
                self._set_state(fridge, self.FRIDGE_CAP_METRIC,
                    _clamp(_drift(60 + progress * 25, 3), 55, 92))
                # Worst-case stability time starts dropping
                self._set_state(fridge, self.STABILITY_METRIC,
                    _clamp(_drift(300 - progress * 180, 20), 80, 360))

            elif phase == "operations_impact":
                progress = phase_tick / self.OPERATIONS_IMPACT_TICKS
                self._set_state(fridge, self.FRIDGE_CAP_METRIC,
                    _clamp(_drift(90 + progress * 4, 2), 85, 96))
                # Worst-case stability time critical
                self._set_state(fridge, self.STABILITY_METRIC,
                    _clamp(_drift(80 - progress * 40, 10), 30, 110))

            elif phase == "recovery":
                progress = phase_tick / self.RECOVERY_TICKS
                # Capacity drains as backlog clears
                self._set_state(fridge, self.FRIDGE_CAP_METRIC,
                    _clamp(_drift(94 - progress * 42, 3), 50, 96))
                self._set_state(fridge, self.STABILITY_METRIC,
                    _clamp(_drift(40 + progress * 300, 25), 35, 400))
