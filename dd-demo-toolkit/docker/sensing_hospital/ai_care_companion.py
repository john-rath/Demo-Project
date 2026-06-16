"""ai-care-companion — the executive-grade healthcare AI service.

A patient/clinician "AI Care Companion": answers medication, discharge,
symptom-triage, and care-plan questions. It's the AI story for the CIO/CEO:

  RUM (care-portal) -> APM (this service) -> LLM Observability (the model call,
  with clinical-safety + cost evals) -> infra. One linked trace, end to end.

Telemetry, two layers:
  1. **DogStatsD custom metrics** (`care.companion.*`) — reliable signal that
     drives the monitors + RCA notebook (hallucination risk, escalation rate,
     RAG latency, tokens, cost). These are what we triage live.
  2. **LLM Observability spans** via the ddtrace LLMObs SDK (workflow -> task ->
     retrieval -> llm) with per-interaction evaluations. Best-effort: wrapped so
     an SDK/version difference never fails a request (the APM trace + metrics
     still flow).

Problem pattern (the "you MUST have Datadog" moment): every 30 minutes the
service self-degrades for ~5 minutes — the RAG knowledge base slows, so the
model starts guessing: hallucination-risk climbs, escalation-to-human spikes,
and patient-facing latency rises. Caught live in LLM Obs + the linked trace
before it reaches a patient. See the AdventHealth overlay notebook + monitors.

DD_SERVICE / DD_LLMOBS_ML_APP / tags come from docker-compose.
"""
from __future__ import annotations

import logging
import os
import random
import time

from fastapi import FastAPI

from metrics import statsd

# LLM Observability SDK — enabled via DD_LLMOBS_ENABLED=1 + DD_LLMOBS_ML_APP +
# ddtrace-run. Import defensively so the service runs even if unavailable.
try:
    from ddtrace.llmobs import LLMObs
    _LLMOBS = True
except Exception:  # pragma: no cover
    LLMObs = None
    _LLMOBS = False

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("ai-care-companion")

ML_APP = os.getenv("DD_LLMOBS_ML_APP", "ai-care-companion")
MODEL = "gpt-4o"
PROVIDER = "azure_openai"

# Problem-pattern cadence: a ~5-min degraded window at the end of every 30 min.
CYCLE_SEC = int(os.getenv("COMPANION_CYCLE_SEC", "1800"))
DEGRADED_SEC = int(os.getenv("COMPANION_DEGRADED_SEC", "300"))
_START = time.time()

app = FastAPI()

# --- Healthcare scenario library (vertical-specific, exec-legible) ----------
SCENARIOS = [
    {
        "type": "medication_question",
        "role": "patient",
        "question": "I missed my evening metformin dose — should I take two in the morning?",
        "kb": "Metformin (AdventHealth med guide): if a dose is missed, skip it and resume the normal schedule; do not double up. Contact your care team if you miss multiple doses.",
        "answer": "Don't double up — skip the missed evening dose and take your normal morning dose. Doubling metformin can cause low blood sugar and stomach upset. If you've missed several doses, message your care team. Want me to log this and notify your nurse?",
    },
    {
        "type": "discharge_instructions",
        "role": "patient",
        "question": "After my knee replacement, when can I shower and how do I care for the incision?",
        "kb": "Post-op TKA discharge (AdventHealth): keep incision dry 48h, then shower allowed; pat dry, no soaking/baths for 3 weeks; watch for redness, drainage, fever >100.4F.",
        "answer": "You can shower after the first 48 hours — keep the incision dry until then, then pat (don't rub) it dry. No baths or soaking for 3 weeks. Call us for spreading redness, drainage, or a fever over 100.4°F. Should I schedule your follow-up wound check?",
    },
    {
        "type": "symptom_triage",
        "role": "patient",
        "question": "I have chest tightness and shortness of breath since this morning.",
        "kb": "Triage policy: chest pain/tightness + dyspnea = possible ACS → escalate to emergency pathway immediately; do not self-manage.",
        "answer": "Chest tightness with shortness of breath needs urgent evaluation — I'm escalating you to a nurse now and you should call 911 if it worsens. I am not able to manage this in-app. Connecting you to the on-call clinician.",
        "force_escalate": True,
    },
    {
        "type": "care_plan",
        "role": "clinician",
        "question": "Summarize the overnight status and open care-plan items for bed MedSurg-304.",
        "kb": "MedSurg-304 (acuity: critical): telemetry stable, pain 4/10, 2 pending orders (CBC, ambulation), fall-risk flagged.",
        "answer": "MedSurg-304 (critical acuity): telemetry stable overnight, pain 4/10. Two open items — pending CBC and first ambulation. Fall-risk is flagged; confirm bed alarm is on. Want me to draft the order acknowledgements?",
    },
]


def _phase() -> str:
    return "degraded" if (time.time() - _START) % CYCLE_SEC >= (CYCLE_SEC - DEGRADED_SEC) else "normal"


@app.get("/healthz")
def healthz():
    return {"ok": True, "phase": _phase()}


@app.post("/ask")
def ask(body: dict | None = None):
    body = body or {}
    scenario = random.choice([s for s in SCENARIOS
                              if not body.get("role") or s["role"] == body.get("role")] or SCENARIOS)
    phase = _phase()
    degraded = phase == "degraded"
    t0 = time.monotonic()

    # --- RAG retrieval (the root cause when degraded) ---
    retrieval_ms = random.uniform(900, 2600) if degraded else random.uniform(40, 150)
    time.sleep(retrieval_ms / 1000.0)

    # --- Generation: when the KB is slow/incomplete, quality drops ---
    gen_ms = random.uniform(1200, 2200) if degraded else random.uniform(300, 700)
    time.sleep(gen_ms / 1000.0)
    input_tokens = random.randint(700, 1400)
    output_tokens = random.randint(180, 420)

    # --- Clinical-safety + cost signals ---
    hallucination_risk = round(random.uniform(0.35, 0.72) if degraded else random.uniform(0.01, 0.08), 3)
    groundedness = round(random.uniform(0.45, 0.7) if degraded else random.uniform(0.88, 0.99), 3)
    escalate = bool(scenario.get("force_escalate")) or (random.random() < (0.45 if degraded else 0.05))
    # ~ $5/1M input, $15/1M output (gpt-4o-ish) — exec-legible cost per interaction.
    cost_usd = round(input_tokens / 1e6 * 5 + output_tokens / 1e6 * 15, 6)
    total_ms = (time.monotonic() - t0) * 1000.0

    _emit_metrics(scenario, phase, retrieval_ms, total_ms, hallucination_risk,
                  groundedness, escalate, input_tokens + output_tokens, cost_usd)
    _emit_llmobs(scenario, phase, retrieval_ms, input_tokens, output_tokens,
                 hallucination_risk, groundedness, escalate)

    return {
        "answer": scenario["answer"],
        "scenario": scenario["type"],
        "escalated_to_human": escalate,
        "phase": phase,
        "evals": {"hallucination_risk": hallucination_risk, "groundedness": groundedness},
        "cost_usd": cost_usd,
        "latency_ms": round(total_ms, 1),
    }


def _emit_metrics(scenario, phase, retrieval_ms, total_ms, halluc, grounded, escalate, tokens, cost):
    tags = [f"scenario:{scenario['type']}", f"role:{scenario['role']}", f"phase:{phase}"]
    statsd.increment("care.companion.requests_total", tags=tags)
    statsd.gauge("care.companion.latency_ms", total_ms, tags=tags)
    statsd.gauge("care.companion.retrieval_latency_ms", retrieval_ms, tags=tags)
    statsd.gauge("care.companion.hallucination_risk", halluc, tags=tags)
    statsd.gauge("care.companion.groundedness", grounded, tags=tags)
    statsd.gauge("care.companion.tokens_total", tokens, tags=tags)
    statsd.gauge("care.companion.cost_usd", cost, tags=tags)
    if escalate:
        statsd.increment("care.companion.escalations_total", tags=tags)


def _emit_llmobs(scenario, phase, retrieval_ms, in_tok, out_tok, halluc, grounded, escalate):
    """Best-effort LLM Obs spans + evals. Never raise into the request path."""
    if not _LLMOBS:
        return
    try:
        with LLMObs.workflow(name="care_companion_request") as wf:
            LLMObs.annotate(input_data=scenario["question"],
                            tags={"scenario": scenario["type"], "role": scenario["role"], "phase": phase})
            with LLMObs.task(name="intent_classification"):
                LLMObs.annotate(output_data=scenario["type"])
            with LLMObs.retrieval(name="care_knowledge_base"):
                LLMObs.annotate(input_data=scenario["question"],
                                output_data=[{"text": scenario["kb"], "name": "care-kb"}])
            with LLMObs.llm(model_name=MODEL, model_provider=PROVIDER, name="generate_response"):
                LLMObs.annotate(
                    input_data=[{"role": "system", "content": "You are AdventHealth's AI Care Companion. Answer ONLY from the retrieved care guidance; escalate clinical-risk questions to a human."},
                                {"role": scenario["role"], "content": scenario["question"]}],
                    output_data=[{"role": "assistant", "content": scenario["answer"]}],
                    metrics={"input_tokens": in_tok, "output_tokens": out_tok, "total_tokens": in_tok + out_tok},
                )
            _submit_evals(wf, halluc, grounded, escalate)
    except Exception as e:  # pragma: no cover
        log.debug("LLMObs emit skipped: %s", e)


def _submit_evals(span, halluc, grounded, escalate):
    try:
        ctx = LLMObs.export_span(span=span)
        evals = [
            ("hallucination_risk", "score", halluc),
            ("clinical_groundedness", "score", grounded),
            ("escalated_to_human", "categorical", "yes" if escalate else "no"),
        ]
        for label, mtype, value in evals:
            LLMObs.submit_evaluation(span=ctx, ml_app=ML_APP, label=label, metric_type=mtype, value=value)
    except Exception as e:  # pragma: no cover — SDK signature differences are non-fatal
        log.debug("submit_evaluation skipped: %s", e)
