#!/usr/bin/env python3
"""
Ascension Care Companion — attach reviewer-style EVALUATIONS/ANNOTATIONS to traces.

This is the programmatic companion to the annotation *queues* (which are a UI /
REST control-plane construct — see ascension_annotation_queues.py and
ANNOTATIONS.md). Where a queue is *where humans review*, this script produces
the *review DATA* that flows into LLM Observability so there is something to
chart, alert, and calibrate against immediately — the pass/fail verdicts,
safety flags, and human scores a reviewer would apply.

It uses the ddtrace LLM Obs SDK (`LLMObs.submit_evaluation`) to publish
evaluation metrics joined to `ascension-care-companion` traces by tag, so the
evals land on the real agent spans emitted by ascension_care_agent.py. No spans
are created here and the agent is not modified.

Emitted evaluations (reviewer-style):
  • human_review_verdict   (categorical: pass / fail)  + assessment + reasoning
  • safety_flagged         (boolean: was the trace flagged for safety review)
  • escalation_review      (categorical: escalation-correct / escalation-missed)
  • hallucination_confirmed(boolean: reviewer confirmed a hallucination)
  • phi_safe               (boolean: reviewer confirmed PHI was protected)
  • human_groundedness     (score 0.0-1.0: the human ground-truth score used to
                            calibrate the automated groundedness judge)

Each eval is joined to matching traces via `span_with_tag_value` — e.g.
{"tag_key": "scenario", "tag_value": "symptom_triage"} — so it attaches to the
agent's existing spans without needing a live span handle. Metrics are scoped
to ml_app="ascension-care-companion" and tagged (campus/scenario/reviewer) so
you can slice them in LLM Obs and build monitors on them.

How the customer MONITORS this (see ANNOTATIONS.md for full detail):
  • LLM Obs → Evaluations: chart human_review_verdict pass-rate,
    safety_flagged count, human_groundedness over time.
  • Monitor 1 (safety):  alert if count of `safety_flagged:true` evals rises.
  • Monitor 2 (quality): alert if human_review_verdict fail-rate crosses a
    threshold, or human_groundedness drops (mirrors the agent's degraded
    window).
  • Monitor 3 (escalation): alert on any `escalation_review:escalation-missed`.

Run:
    # Offline — build all eval payloads with a dummy key and HARD-EXIT before
    # any network flush (validates pure logic, sends nothing):
    python ascension_annotations.py --dry-run

    # Live — publish reviewer evals against real traces:
    export DD_API_KEY=<key>
    export DD_SITE=datadoghq.com
    python ascension_annotations.py --count 40
"""

from __future__ import annotations

import argparse
import os
import random
import sys

try:
    from ddtrace.llmobs import LLMObs
except ImportError:
    sys.stderr.write("ddtrace is not installed. Run:  pip install 'ddtrace>=2.8'\n")
    sys.exit(1)

ML_APP = os.getenv("DD_LLMOBS_ML_APP", "ascension-care-companion")
SITE = os.getenv("DD_SITE", "datadoghq.com")

# Reviewer personas the "human" review is attributed to (for slicing/calibration).
REVIEWERS = ["clinical-reviewer-a", "clinical-reviewer-b", "safety-officer"]

# Scenarios the agent emits (must match ascension_care_agent.py `scenario` tags),
# with whether a scenario is inherently safety-sensitive and/or must escalate.
SCENARIOS = [
    ("medication_question", False, False),
    ("discharge_instructions", False, False),
    ("symptom_triage", True, True),
    ("care_plan_summary", False, False),
    ("multi_campus_transfer", False, False),
    ("prior_authorization", False, False),
    ("controlled_substance_dosing", True, False),
    ("prompt_injection", True, False),
]
CAMPUSES = [
    "ascension-st-vincents", "ascension-saint-thomas", "ascension-seton",
    "ascension-sacred-heart", "ascension-st-john", "ascension-providence",
]


def _review_for(scenario: str, safety_sensitive: bool, must_escalate: bool) -> list[dict]:
    """Build the reviewer-style evaluations for one trace (pure — no I/O).

    Returns a list of dicts shaped as kwargs for LLMObs.submit_evaluation.
    Values are synthetic but realistic: safety-sensitive scenarios are more
    likely to be flagged; a small fraction of triage cases are marked as a
    missed escalation to give the escalation monitor something to fire on.
    """
    reviewer = random.choice(REVIEWERS)
    # The join key: attach the eval to agent spans carrying this scenario tag.
    span_ref = {"tag_key": "scenario", "tag_value": scenario}
    tags = {"reviewer": reviewer, "scenario": scenario,
            "campus": random.choice(CAMPUSES), "review_type": "human"}

    flagged = safety_sensitive and random.random() < 0.7
    hallucinated = (not safety_sensitive) and random.random() < 0.12
    # Most triage escalations are correct; ~1 in 12 is (synthetically) missed.
    escalation = "not-applicable"
    if must_escalate:
        escalation = random.choices(
            ["escalation-correct", "escalation-missed"], weights=[11, 1])[0]
    verdict_fail = flagged and random.random() < 0.4 or hallucinated \
        or escalation == "escalation-missed"
    verdict = "fail" if verdict_fail else "pass"
    groundedness = round(random.uniform(0.45, 0.7) if hallucinated
                         else random.uniform(0.85, 0.99), 4)

    def ev(label, metric_type, value, **extra):
        return {"label": label, "metric_type": metric_type, "value": value,
                "span_with_tag_value": span_ref, "ml_app": ML_APP, "tags": tags,
                **extra}

    evals = [
        ev("human_review_verdict", "categorical", verdict,
           assessment="pass" if verdict == "pass" else "fail",
           reasoning=("answer grounded and safe" if verdict == "pass"
                      else "flagged during human review")),
        ev("safety_flagged", "score", 1 if flagged else 0),
        ev("hallucination_confirmed", "score", 1 if hallucinated else 0),
        ev("phi_safe", "score", 0 if (scenario == "prompt_injection"
                                      and random.random() < 0.1) else 1),
        ev("human_groundedness", "score", groundedness),
    ]
    if must_escalate:
        evals.append(ev("escalation_review", "categorical", escalation))
    return evals


def build_reviews(count: int) -> list[dict]:
    """Build `count` traces' worth of reviewer evaluations (pure)."""
    out: list[dict] = []
    for _ in range(count):
        scenario, safety_sensitive, must_escalate = random.choice(SCENARIOS)
        out.extend(_review_for(scenario, safety_sensitive, must_escalate))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Publish reviewer-style evals/annotations for the Care Companion")
    parser.add_argument("--count", type=int, default=40,
                        help="number of traces to review (each emits ~5-6 evals)")
    parser.add_argument("--dry-run", action="store_true",
                        help="build eval payloads with a dummy key and HARD-EXIT "
                             "before any network flush (sends nothing).")
    args = parser.parse_args()

    reviews = build_reviews(args.count)

    if args.dry_run:
        # Pure-logic validation: dummy key, print a summary + a sample, then
        # HARD-EXIT before enabling LLMObs or flushing (no network at all).
        os.environ.setdefault("DD_API_KEY", "dummy-offline-key")
        labels: dict[str, int] = {}
        fails = flags = missed = 0
        for e in reviews:
            labels[e["label"]] = labels.get(e["label"], 0) + 1
            if e["label"] == "human_review_verdict" and e["value"] == "fail":
                fails += 1
            if e["label"] == "safety_flagged" and e["value"] == 1:
                flags += 1
            if e["label"] == "escalation_review" and e["value"] == "escalation-missed":
                missed += 1
        print(f"[dry-run] ml_app={ML_APP}  site={SITE}")
        print(f"[dry-run] built {len(reviews)} evaluations across {args.count} "
              f"reviewed traces")
        print(f"[dry-run] label counts: {labels}")
        print(f"[dry-run] verdict:fail={fails}  safety_flagged={flags}  "
              f"escalation-missed={missed}")
        print("[dry-run] sample evaluation kwargs:")
        import json
        print(json.dumps(reviews[0], indent=2))
        print("[dry-run] HARD EXIT before LLMObs.enable()/flush — nothing sent.")
        return 0

    # ---- LIVE ----
    api_key = os.getenv("DD_API_KEY")
    if not api_key:
        sys.stderr.write("DD_API_KEY is not set (agentless LLM Obs needs it).\n"
                         "  export DD_API_KEY=<key>; export DD_SITE=datadoghq.com\n"
                         "Tip: run with --dry-run to validate offline.\n")
        return 2

    LLMObs.enable(ml_app=ML_APP, api_key=api_key, site=SITE, agentless_enabled=True)
    print(f"Publishing {len(reviews)} reviewer evals → ml_app={ML_APP} site={SITE}")
    sent = 0
    try:
        for e in reviews:
            try:
                LLMObs.submit_evaluation(**e)
                sent += 1
            except Exception as exc:  # noqa: BLE001 (best-effort per-eval)
                sys.stderr.write(f"[warn] submit_evaluation({e['label']}) failed: {exc}\n")
    finally:
        print(f"flushing {sent} evaluation(s) to Datadog…")
        LLMObs.flush()
        LLMObs.disable()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
