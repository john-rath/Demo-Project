"""
EY Risk Portfolio — Feature-Pipeline Null-Rate Spike → LLM Eval F1
Regression cascade.

The simulator-side counterpart to the DSM data-quality cascade in
`data_obs/`. Drives the notebook narrative (`ey-risk-portfolio-llm-eval-rca`)
end-to-end: a synthetic upstream data-quality event surfaces as
`finserv.ai_data.null_rate_pct` climbing, the ingestion-lag follows, and
within ~2 minutes the eval scores on all three model nodes regress.

Disjoint from the base finance vertical along the four axes that the
sub-vertical overlay system documents (see CLAUDE.md §6.5):

  1. Spatial — only touches `feature_pipeline_node` and `llm_eval_node`
     devices. Base finance trading / payment / data-infrastructure
     devices are not touched.
  2. Metric namespace — only mutates `finserv.ai_data.*` and
     `finserv.llm_eval.*`. Zero overlap with any base-finance metric.
  3. Incident-domain tag — emits state into engine.incident_state under
     `ey_eval_regression` with `incident_domain=ai-eval-pipeline`.
  4. Time delta — first cascade fires ~2–6 ticks after simulator start
     (demo-friendly), then 80–120 ticks of idle between cascades.

Cascade narrative (matches `notebooks.yaml → ey-risk-portfolio-llm-eval-rca`):

  Phase 1 — drift_up (8 ticks ≈ 2m):
      A scheduled Airflow DAG begins emitting events with elevated
      null_rate (4-12%). `finserv.ai_data.null_rate_pct` climbs;
      everything else looks healthy. (signal_chain: 1-root-cause)

  Phase 2 — upstream_impact (8 ticks ≈ 2m):
      Ingestion lag climbs and last-successful-run-age starts ticking up.
      Schema-drift events log. The downstream model nodes haven't seen
      the bad data yet. (signal_chain: 2-leading-indicator)

  Phase 3 — eval_regression (12 ticks ≈ 3m):
      Bad-data feature vectors hit the eval set. F1 / precision / recall
      drop across all three Azure OpenAI model variants in lockstep —
      the hallmark that this is an INPUT problem, not a model problem.
      Hallucination rate climbs. Guardrail block rate climbs slightly.
      (signal_chain: 3-symptom)

  Phase 4 — recovery (10 ticks ≈ 2m30s):
      Upstream remediation (DAG hotfix) drains the null-rate spike.
      Eval scores climb back to baseline. (signal_chain: 5-recovery)
"""

import logging
import random
from typing import Any, Dict, List, Optional

from dd_demo_toolkit.simulator.plugins import IncidentPlugin

logger = logging.getLogger("ey_eval_regression_incident")


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _drift(value: float, magnitude: float = 1.0, bias: float = 0.0) -> float:
    return value + random.gauss(bias, magnitude)


class EYEvalRegressionCascade(IncidentPlugin):
    """
    Drives the EY notebook's RCA arc from upstream data quality to
    downstream LLM eval regression.

    Targets every `feature_pipeline_node` (4 devices) and every
    `llm_eval_node` (3 devices, one per model variant). The cascade is
    fleet-wide on both groups so the symptom is unambiguous when split
    by `model` on the LLM Eval Scorecard dashboard.
    """

    # Phase durations (15s/tick → ~10 min full cascade)
    DRIFT_UP_TICKS = 8           # 2m   — null_rate climbing on pipelines
    UPSTREAM_IMPACT_TICKS = 8    # 2m   — lag + staleness compounding
    EVAL_REGRESSION_TICKS = 12   # 3m   — F1/precision/recall regress
    RECOVERY_TICKS = 10          # 2m30 — DAG hotfix restores baseline

    EVENT_TICKS = (
        DRIFT_UP_TICKS
        + UPSTREAM_IMPACT_TICKS
        + EVAL_REGRESSION_TICKS
        + RECOVERY_TICKS
    )

    # --- Clean baselines held during the 'normal' phase ---------------
    # Without these, gauss random walk drifts every metric toward its
    # declared range midpoint, which sits close to the cascade peak for
    # F1/precision/recall — making the regression invisible against
    # baseline noise. Anchoring at the healthy upper end of each range
    # keeps the cascade trough an obvious anomaly.
    BASELINE_NULL_RATE_PCT = 0.8
    BASELINE_INGEST_LAG_SEC = 40.0
    BASELINE_RUN_AGE_MIN = 6.0
    BASELINE_SCHEMA_DRIFT_PER_HR = 0.3

    # Per-model healthy F1 baselines (mirrors ranges in ey.yaml).
    MODEL_BASELINES = {
        "gpt-4.1": {
            "f1": 0.86, "precision": 0.88, "recall": 0.84,
            "relevance": 0.92, "halluc": 2.6,
        },
        "gpt-4.5": {
            "f1": 0.91, "precision": 0.93, "recall": 0.89,
            "relevance": 0.96, "halluc": 1.6,
        },
        "gpt-4o-mini": {
            "f1": 0.76, "precision": 0.80, "recall": 0.73,
            "relevance": 0.85, "halluc": 4.5,
        },
    }

    # Cascade trough per model (the value F1 etc. lands at in phase 3).
    MODEL_TROUGHS = {
        "gpt-4.1": {
            "f1": 0.69, "precision": 0.74, "recall": 0.66,
            "relevance": 0.78, "halluc": 7.5,
        },
        "gpt-4.5": {
            "f1": 0.74, "precision": 0.78, "recall": 0.71,
            "relevance": 0.82, "halluc": 5.2,
        },
        "gpt-4o-mini": {
            "f1": 0.58, "precision": 0.63, "recall": 0.55,
            "relevance": 0.68, "halluc": 11.0,
        },
    }

    # Metric names — must match the device declarations in ey.yaml.
    NULL_METRIC = "finserv.ai_data.null_rate_pct"
    LAG_METRIC = "finserv.ai_data.ingestion_lag_sec"
    AGE_METRIC = "finserv.ai_data.last_successful_run_age_min"
    SCHEMA_DRIFT_METRIC = "finserv.ai_data.schema_drift_events_per_hr"
    F1_METRIC = "finserv.llm_eval.f1_score"
    PRECISION_METRIC = "finserv.llm_eval.precision"
    RECALL_METRIC = "finserv.llm_eval.recall"
    RELEVANCE_METRIC = "finserv.llm_eval.relevance"
    HALLUC_METRIC = "finserv.llm_eval.hallucination_rate_pct"

    def __init__(self) -> None:
        self._ticks_until_next = random.randint(2, 6)
        self._active_tick: Optional[int] = None
        self._pipelines: List[Any] = []
        self._models: List[Any] = []

        logger.info(
            "EY eval regression cascade initialized. First event in ~%d sec",
            self._ticks_until_next * 15,
        )

    def get_incident_name(self) -> str:
        return (
            "EY Risk Portfolio — Feature-Pipeline Null-Rate Spike → "
            "LLM Eval F1 Regression (fleet-wide, all models)"
        )

    def reset(self) -> None:
        self._ticks_until_next = random.randint(2, 6)
        self._active_tick = None
        self._pipelines = []
        self._models = []

    # ------------------------------------------------------------------
    # Tick entry point
    # ------------------------------------------------------------------

    def on_tick(
        self,
        tick_count: int,
        fleet: List[Any],
        engine: Any,
    ) -> None:
        # Lazy-index target devices on first tick.
        if not self._pipelines and not self._models:
            for d in fleet:
                dtype = getattr(d, "type", None) or (
                    d.get("device_type") if isinstance(d, dict) else None
                )
                if dtype == "feature_pipeline_node":
                    self._pipelines.append(d)
                elif dtype == "llm_eval_node":
                    self._models.append(d)
            if self._pipelines or self._models:
                logger.info(
                    "Indexed %d feature pipelines + %d model nodes",
                    len(self._pipelines), len(self._models),
                )

        self._advance_clock()
        phase, phase_tick = self._current_phase()

        if hasattr(engine, "incident_state"):
            if phase == "normal":
                engine.incident_state.pop("ey_eval_regression", None)
            else:
                engine.incident_state["ey_eval_regression"] = {
                    "phase": phase,
                    "phase_tick": phase_tick,
                    "incident_domain": "ai-eval-pipeline",
                    "signal_chain_root": "feature-pipeline-null-spike",
                }

        self._apply_overrides(phase, phase_tick)
        if phase != "normal":
            logger.info(
                "EY EVAL CASCADE [%s t=%d] pipelines=%d models=%d",
                phase, phase_tick, len(self._pipelines), len(self._models),
            )

    # ------------------------------------------------------------------
    # Phase / clock
    # ------------------------------------------------------------------

    def _current_phase(self) -> tuple:
        if self._active_tick is None:
            return ("normal", 0)
        t = self._active_tick
        a = self.DRIFT_UP_TICKS
        b = a + self.UPSTREAM_IMPACT_TICKS
        c = b + self.EVAL_REGRESSION_TICKS
        d = c + self.RECOVERY_TICKS
        if t < a:
            return ("drift_up", t)
        if t < b:
            return ("upstream_impact", t - a)
        if t < c:
            return ("eval_regression", t - b)
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
                    "EY eval cascade complete. Next event in ~%d min",
                    self._ticks_until_next * 15 // 60,
                )
        else:
            self._ticks_until_next -= 1
            if self._ticks_until_next <= 0:
                self._active_tick = 0
                logger.info("EY EVAL CASCADE STARTING (feature-pipeline null-spike)")

    # ------------------------------------------------------------------
    # State writers
    # ------------------------------------------------------------------

    def _set_state(self, device: Any, metric: str, value: float) -> None:
        state = getattr(device, "state", None)
        if state is None and isinstance(device, dict):
            state = device.setdefault("state", {})
        if state is not None:
            state[metric] = value

    def _device_model(self, device: Any) -> Optional[str]:
        return getattr(device, "model", None) or (
            device.get("model") if isinstance(device, dict) else None
        )

    # ------------------------------------------------------------------
    # Phase overrides
    # ------------------------------------------------------------------

    def _apply_overrides(self, phase: str, phase_tick: int) -> None:
        if phase == "normal":
            self._hold_baseline()
            return
        if phase == "drift_up":
            self._phase_drift_up(phase_tick)
        elif phase == "upstream_impact":
            self._phase_upstream_impact(phase_tick)
        elif phase == "eval_regression":
            self._phase_eval_regression(phase_tick)
        elif phase == "recovery":
            self._phase_recovery(phase_tick)

    def _hold_baseline(self) -> None:
        # Pipelines: clean, fresh, low null-rate
        for p in self._pipelines:
            self._set_state(p, self.NULL_METRIC,
                            _clamp(_drift(self.BASELINE_NULL_RATE_PCT, 0.15), 0.2, 1.6))
            self._set_state(p, self.LAG_METRIC,
                            _clamp(_drift(self.BASELINE_INGEST_LAG_SEC, 5), 15, 80))
            self._set_state(p, self.AGE_METRIC,
                            _clamp(_drift(self.BASELINE_RUN_AGE_MIN, 1), 2, 12))
            self._set_state(p, self.SCHEMA_DRIFT_METRIC,
                            _clamp(_drift(self.BASELINE_SCHEMA_DRIFT_PER_HR, 0.1), 0, 0.8))

        # Models: healthy F1 / precision / recall band
        for m in self._models:
            model = self._device_model(m)
            base = self.MODEL_BASELINES.get(model)
            if not base:
                continue
            self._set_state(m, self.F1_METRIC,
                            _clamp(_drift(base["f1"], 0.008), 0.5, 0.99))
            self._set_state(m, self.PRECISION_METRIC,
                            _clamp(_drift(base["precision"], 0.008), 0.5, 0.99))
            self._set_state(m, self.RECALL_METRIC,
                            _clamp(_drift(base["recall"], 0.009), 0.5, 0.99))
            self._set_state(m, self.RELEVANCE_METRIC,
                            _clamp(_drift(base["relevance"], 0.006), 0.5, 0.99))
            self._set_state(m, self.HALLUC_METRIC,
                            _clamp(_drift(base["halluc"], 0.4), 0.5, 14))

    def _interp(self, lo: float, hi: float, progress: float) -> float:
        return lo + (hi - lo) * progress

    def _phase_drift_up(self, t: int) -> None:
        progress = (t + 1) / self.DRIFT_UP_TICKS
        for p in self._pipelines:
            null_target = self._interp(self.BASELINE_NULL_RATE_PCT, 9.5, progress)
            self._set_state(p, self.NULL_METRIC,
                            _clamp(_drift(null_target, 0.5), 0.5, 13))
            # Lag + age still mostly normal in this phase
            self._set_state(p, self.LAG_METRIC,
                            _clamp(_drift(self.BASELINE_INGEST_LAG_SEC, 6), 20, 100))
            self._set_state(p, self.AGE_METRIC,
                            _clamp(_drift(self.BASELINE_RUN_AGE_MIN, 1), 3, 14))
            self._set_state(p, self.SCHEMA_DRIFT_METRIC,
                            _clamp(_drift(self.BASELINE_SCHEMA_DRIFT_PER_HR + progress * 0.4, 0.1), 0, 1.4))

        # Models still healthy in phase 1 — the lag between bad data
        # arriving and eval scores reacting is the whole point.
        self._hold_models_baseline()

    def _phase_upstream_impact(self, t: int) -> None:
        progress = (t + 1) / self.UPSTREAM_IMPACT_TICKS
        for p in self._pipelines:
            # Null rate stays elevated, lag and staleness compound
            self._set_state(p, self.NULL_METRIC,
                            _clamp(_drift(9.5, 0.8), 6, 13))
            lag_target = self._interp(self.BASELINE_INGEST_LAG_SEC, 160.0, progress)
            self._set_state(p, self.LAG_METRIC,
                            _clamp(_drift(lag_target, 12), 30, 220))
            age_target = self._interp(self.BASELINE_RUN_AGE_MIN, 34.0, progress)
            self._set_state(p, self.AGE_METRIC,
                            _clamp(_drift(age_target, 2.5), 5, 50))
            self._set_state(p, self.SCHEMA_DRIFT_METRIC,
                            _clamp(_drift(1.2, 0.15), 0.4, 2.2))

        # Models still healthy — Bits AI's hypothesis test should find
        # the lag here BEFORE F1 starts dropping.
        self._hold_models_baseline()

    def _phase_eval_regression(self, t: int) -> None:
        progress = (t + 1) / self.EVAL_REGRESSION_TICKS

        # Pipelines stay degraded
        for p in self._pipelines:
            self._set_state(p, self.NULL_METRIC,
                            _clamp(_drift(9.8, 0.8), 6, 13))
            self._set_state(p, self.LAG_METRIC,
                            _clamp(_drift(170, 18), 60, 240))
            self._set_state(p, self.AGE_METRIC,
                            _clamp(_drift(36, 3), 12, 55))
            self._set_state(p, self.SCHEMA_DRIFT_METRIC,
                            _clamp(_drift(1.3, 0.18), 0.5, 2.5))

        # Models regress in lockstep — characteristic "input problem"
        # signature. All three models drop together at roughly equal
        # relative magnitude.
        for m in self._models:
            model = self._device_model(m)
            base = self.MODEL_BASELINES.get(model)
            trough = self.MODEL_TROUGHS.get(model)
            if not base or not trough:
                continue
            self._set_state(m, self.F1_METRIC,
                            _clamp(_drift(self._interp(base["f1"], trough["f1"], progress), 0.01), 0.5, 0.99))
            self._set_state(m, self.PRECISION_METRIC,
                            _clamp(_drift(self._interp(base["precision"], trough["precision"], progress), 0.01), 0.5, 0.99))
            self._set_state(m, self.RECALL_METRIC,
                            _clamp(_drift(self._interp(base["recall"], trough["recall"], progress), 0.012), 0.5, 0.99))
            self._set_state(m, self.RELEVANCE_METRIC,
                            _clamp(_drift(self._interp(base["relevance"], trough["relevance"], progress), 0.008), 0.5, 0.99))
            self._set_state(m, self.HALLUC_METRIC,
                            _clamp(_drift(self._interp(base["halluc"], trough["halluc"], progress), 0.5), 0.5, 14))

    def _phase_recovery(self, t: int) -> None:
        progress = (t + 1) / self.RECOVERY_TICKS
        # Pipelines recover quickly (DAG hotfix)
        for p in self._pipelines:
            null_target = self._interp(9.5, self.BASELINE_NULL_RATE_PCT, progress)
            self._set_state(p, self.NULL_METRIC,
                            _clamp(_drift(null_target, 0.4), 0.3, 11))
            lag_target = self._interp(160.0, self.BASELINE_INGEST_LAG_SEC, progress)
            self._set_state(p, self.LAG_METRIC,
                            _clamp(_drift(lag_target, 8), 18, 200))
            age_target = self._interp(34.0, self.BASELINE_RUN_AGE_MIN, progress)
            self._set_state(p, self.AGE_METRIC,
                            _clamp(_drift(age_target, 2), 3, 45))
            self._set_state(p, self.SCHEMA_DRIFT_METRIC,
                            _clamp(_drift(self._interp(1.2, 0.3, progress), 0.1), 0, 1.6))

        # Models lag pipeline recovery slightly — closer to linear
        # but still catching up.
        for m in self._models:
            model = self._device_model(m)
            base = self.MODEL_BASELINES.get(model)
            trough = self.MODEL_TROUGHS.get(model)
            if not base or not trough:
                continue
            self._set_state(m, self.F1_METRIC,
                            _clamp(_drift(self._interp(trough["f1"], base["f1"], progress), 0.01), 0.5, 0.99))
            self._set_state(m, self.PRECISION_METRIC,
                            _clamp(_drift(self._interp(trough["precision"], base["precision"], progress), 0.01), 0.5, 0.99))
            self._set_state(m, self.RECALL_METRIC,
                            _clamp(_drift(self._interp(trough["recall"], base["recall"], progress), 0.012), 0.5, 0.99))
            self._set_state(m, self.RELEVANCE_METRIC,
                            _clamp(_drift(self._interp(trough["relevance"], base["relevance"], progress), 0.008), 0.5, 0.99))
            self._set_state(m, self.HALLUC_METRIC,
                            _clamp(_drift(self._interp(trough["halluc"], base["halluc"], progress), 0.4), 0.5, 14))

    def _hold_models_baseline(self) -> None:
        """Model nodes stay at healthy baseline during pipeline-only
        phases (drift_up, upstream_impact). The diagnostic point is
        that bad data shows in DSM / pipeline metrics BEFORE the LLM
        eval scores have caught up — that's the "we knew before the
        model did" narrative."""
        for m in self._models:
            model = self._device_model(m)
            base = self.MODEL_BASELINES.get(model)
            if not base:
                continue
            self._set_state(m, self.F1_METRIC,
                            _clamp(_drift(base["f1"], 0.008), 0.5, 0.99))
            self._set_state(m, self.PRECISION_METRIC,
                            _clamp(_drift(base["precision"], 0.008), 0.5, 0.99))
            self._set_state(m, self.RECALL_METRIC,
                            _clamp(_drift(base["recall"], 0.009), 0.5, 0.99))
            self._set_state(m, self.RELEVANCE_METRIC,
                            _clamp(_drift(base["relevance"], 0.006), 0.5, 0.99))
            self._set_state(m, self.HALLUC_METRIC,
                            _clamp(_drift(base["halluc"], 0.4), 0.5, 14))
