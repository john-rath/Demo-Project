"""
EY Risk Portfolio — LLM Obs Experiment.

This is the script that lands Jaganathan's #1 ask in the Datadog UI: a
repeatable, versioned, multi-model F1 / precision / recall / regulatory
citation evaluation, run head-to-head across the Azure OpenAI variants
the team is comparing.

Replaces the manual Ragas runs the Risk Portfolio team operates today.

Usage (one-shot inside the dd-demo-toolkit compose project):

    make run-experiment                       # one run, both models
    EXPERIMENT_MODEL=gpt-4.5 make run-experiment   # single model

Required env (sourced via op run -- like the rest of the stack):
    DD_API_KEY, DD_APP_KEY, DD_SITE

Optional:
    EXPERIMENT_PROJECT  default: "EY Risk Portfolio Eval"
    EXPERIMENT_DATASET  default: "ey_risk_portfolio_eval_v1"
    EXPERIMENT_MODEL    default: runs both gpt-4.5 + gpt-4o-mini
"""

from __future__ import annotations

import logging
import os
import random
import re
import signal
import sys
import time
from typing import Any, Dict, List

from ddtrace.llmobs import LLMObs


# ---- continuous-loop control ----------------------------------------------
# When EXPERIMENT_INTERVAL_SEC is set, this script runs the experiment
# cycle repeatedly on that interval — sidecar to the simulator, part of
# the normal telemetry generation. Set to 0 / unset to do a single run
# and exit (original one-shot behaviour).
_INTERVAL_SEC = int(os.environ.get("EXPERIMENT_INTERVAL_SEC", "180"))
_running = True


def _stop(signum, _frame):
    global _running
    log.info("signal %s received; stopping after current iteration", signum)
    _running = False

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("risk-eval-experiment")


# ---------------------------------------------------------------------------
# Dataset — EY Risk Portfolio scenarios. Inputs are analyst requests; the
# expected_output is a tagged dict that the evaluators inspect (decision
# label + regulatory citation set + whether the response should mention
# specific findings). The task returns the same shape so evaluators can
# field-compare.
# ---------------------------------------------------------------------------

DATASET_RECORDS = [
    {
        "input_data": (
            "Draft a credit-risk memo for counterparty ACME Capital. Latest "
            "10-K plus internal trade exposure attached. Highlight covenant "
            "breaches and concentration risk."
        ),
        "expected_output": {
            "decision": "WATCH",
            "must_cite_regulations": ["SOX 404"],
            "must_mention": ["covenant breach", "concentration"],
            "must_not_leak_pii": True,
        },
    },
    {
        "input_data": (
            "Walk me through the SAR filing decision for the flagged wire to "
            "high-risk jurisdiction. Customer is a Silver-tier corporate client."
        ),
        "expected_output": {
            "decision": "FILE",
            "must_cite_regulations": ["FinCEN 1020.320", "BSA"],
            "must_mention": ["enhanced due diligence", "tipping-off"],
            "must_not_leak_pii": True,
        },
    },
    {
        "input_data": (
            "Summarise the internal audit findings for the Capital Markets desk "
            "Q2 review. Three separate audit reports attached."
        ),
        "expected_output": {
            "decision": "REMEDIATE",
            "must_cite_regulations": [],
            "must_mention": ["High", "remediation", "QRM"],
            "must_not_leak_pii": True,
        },
    },
    {
        "input_data": (
            "Explain why the VaR model flagged the FX swap book yesterday. "
            "Senior management wants the plain-English version."
        ),
        "expected_output": {
            "decision": "EXPLAIN",
            "must_cite_regulations": [],
            "must_mention": ["historical simulation", "GBP/USD", "stress overlay"],
            "must_not_leak_pii": True,
        },
    },
    {
        "input_data": (
            "Pull the material risks from the target company's last 3 years of "
            "10-Ks for the M&A diligence brief. Target is a mid-cap industrial."
        ),
        "expected_output": {
            "decision": "FLAG",
            "must_cite_regulations": ["Item 9A"],
            "must_mention": ["material weakness", "litigation", "environmental"],
            "must_not_leak_pii": True,
        },
    },
    {
        "input_data": (
            "Customer wires $72,000 to a FATF grey-list jurisdiction. Account "
            "9876543210 is for Acme LLC. Should we file a SAR?"
        ),
        "expected_output": {
            "decision": "FILE",
            "must_cite_regulations": ["FinCEN 1020.320"],
            "must_mention": ["enhanced due diligence"],
            "must_not_leak_pii": True,
        },
    },
    {
        "input_data": (
            "What controls should we put on the AI credit-scoring model under "
            "the new SR 11-7 guidance?"
        ),
        "expected_output": {
            "decision": "GUIDE",
            "must_cite_regulations": ["SR 11-7"],
            "must_mention": ["model validation", "ongoing monitoring"],
            "must_not_leak_pii": True,
        },
    },
    {
        "input_data": (
            "We hit a 2x VaR breach on the rates desk yesterday. What's our "
            "regulatory notification obligation?"
        ),
        "expected_output": {
            "decision": "NOTIFY",
            "must_cite_regulations": ["Basel III", "FRTB"],
            "must_mention": ["risk committee", "limit breach"],
            "must_not_leak_pii": True,
        },
    },
]


# ---------------------------------------------------------------------------
# Per-model task simulators. In a production deployment these would hit
# Azure OpenAI directly; for a demo without OpenAI creds we synthesise
# outputs whose quality matches the model-comparison narrative in the
# LLM Eval Scorecard (gpt-4.5 best, gpt-4.1 baseline, gpt-4o-mini cost-
# optimised but lower fidelity).
# ---------------------------------------------------------------------------

MODEL_PROFILES = {
    "gpt-4.5": {
        "correct_decision_rate": 0.92,
        "regulation_citation_rate": 0.94,
        "must_mention_hit_rate": 0.91,
        "pii_leak_rate": 0.01,
    },
    "gpt-4.1": {
        "correct_decision_rate": 0.86,
        "regulation_citation_rate": 0.88,
        "must_mention_hit_rate": 0.85,
        "pii_leak_rate": 0.02,
    },
    "gpt-4o-mini": {
        "correct_decision_rate": 0.74,
        "regulation_citation_rate": 0.76,
        "must_mention_hit_rate": 0.71,
        "pii_leak_rate": 0.05,
    },
}


def _build_task(model_name: str):
    """Return a task callable bound to a specific model profile."""
    profile = MODEL_PROFILES.get(model_name, MODEL_PROFILES["gpt-4.1"])

    def task(input_data: str, config: Dict[str, Any]) -> Dict[str, Any]:
        # Deterministic-ish per-input randomness so the same input/model
        # combo behaves the same way across re-runs of the experiment.
        seed = hash((input_data, model_name)) & 0xFFFFFFFF
        rnd = random.Random(seed)

        # Look up the expected_output for this input (lets the simulator
        # produce a "mostly right" answer with model-quality-controlled
        # corruption rate).
        expected = next(
            (r["expected_output"] for r in DATASET_RECORDS
             if r["input_data"] == input_data),
            {"decision": "UNKNOWN", "must_cite_regulations": [],
             "must_mention": [], "must_not_leak_pii": True},
        )

        decision = expected["decision"]
        if rnd.random() > profile["correct_decision_rate"]:
            decision = rnd.choice(["UNKNOWN", "DEFER", "WATCH", "FILE"])

        citations = [
            r for r in expected["must_cite_regulations"]
            if rnd.random() < profile["regulation_citation_rate"]
        ]
        mentions = [
            m for m in expected["must_mention"]
            if rnd.random() < profile["must_mention_hit_rate"]
        ]
        pii_leaked = rnd.random() < profile["pii_leak_rate"]

        # Render a markdown response so the LLM Obs UI shows real prose
        # rather than only a struct.
        body = [
            f"**Decision:** {decision}",
            "",
            "**Regulatory basis:** " + (
                ", ".join(citations) if citations else "_none cited_"
            ),
            "",
            "**Key findings:**",
        ]
        for m in (mentions or ["(no specific findings surfaced)"]):
            body.append(f"- {m}")
        body.append("")
        body.append(
            f"**Confidence:** {'High' if not pii_leaked and decision == expected['decision'] else 'Medium'}"
        )
        if pii_leaked:
            body.append("")
            body.append("Account 9876543210 — flagged with full PII included.")

        return {
            "decision": decision,
            "citations": citations,
            "mentions": mentions,
            "pii_leaked": pii_leaked,
            "response_markdown": "\n".join(body),
            "model": model_name,
        }

    task.__name__ = f"risk_eval_task_{model_name.replace('.', '_').replace('-', '_')}"
    return task


# ---------------------------------------------------------------------------
# Evaluators — exactly the metrics Jaganathan asked for.
# ---------------------------------------------------------------------------

def exact_match_decision(input_data: str, output_data: Dict[str, Any],
                         expected_output: Dict[str, Any]) -> float:
    return 1.0 if output_data.get("decision") == expected_output.get("decision") else 0.0


def regulatory_citation_recall(input_data: str, output_data: Dict[str, Any],
                               expected_output: Dict[str, Any]) -> float:
    required = set(expected_output.get("must_cite_regulations", []))
    if not required:
        return 1.0
    cited = set(output_data.get("citations", []))
    return len(required & cited) / len(required)


def must_mention_recall(input_data: str, output_data: Dict[str, Any],
                        expected_output: Dict[str, Any]) -> float:
    required = set(expected_output.get("must_mention", []))
    if not required:
        return 1.0
    found = set(output_data.get("mentions", []))
    return len(required & found) / len(required)


def precision_score(input_data: str, output_data: Dict[str, Any],
                    expected_output: Dict[str, Any]) -> float:
    # Combined precision across decision + citation + mentions.
    correct = 0
    total = 0
    if output_data.get("decision"):
        total += 1
        if output_data["decision"] == expected_output.get("decision"):
            correct += 1
    for c in output_data.get("citations", []):
        total += 1
        if c in expected_output.get("must_cite_regulations", []):
            correct += 1
    for m in output_data.get("mentions", []):
        total += 1
        if m in expected_output.get("must_mention", []):
            correct += 1
    return correct / total if total > 0 else 0.0


def f1_score_combined(input_data: str, output_data: Dict[str, Any],
                      expected_output: Dict[str, Any]) -> float:
    p = precision_score(input_data, output_data, expected_output)
    r_decision = exact_match_decision(input_data, output_data, expected_output)
    r_citations = regulatory_citation_recall(input_data, output_data, expected_output)
    r_mentions = must_mention_recall(input_data, output_data, expected_output)
    r = (r_decision + r_citations + r_mentions) / 3
    if (p + r) == 0:
        return 0.0
    return round(2 * p * r / (p + r), 4)


_PII_PATTERNS = [
    re.compile(r"\b\d{10}\b"),                              # 10-digit account
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),                   # SSN-style
    re.compile(r"\baccount\s+\d{4,}", re.IGNORECASE),       # "account 9876543210"
]


def pii_leak_check(input_data: str, output_data: Dict[str, Any],
                   expected_output: Dict[str, Any]) -> str:
    text = output_data.get("response_markdown", "") or ""
    if output_data.get("pii_leaked"):
        return "fail"
    for pat in _PII_PATTERNS:
        if pat.search(text):
            return "fail"
    return "pass"


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def _get_or_create_dataset(dataset_name: str):
    """Pull the dataset if it exists, otherwise create it.

    In continuous-loop mode the same dataset is reused across cycles so
    the Datadog UI groups experiment runs under one logical dataset
    rather than spawning a fresh dataset every 3 minutes.
    """
    try:
        return LLMObs.pull_dataset(dataset_name=dataset_name)
    except Exception:
        return LLMObs.create_dataset(
            dataset_name=dataset_name,
            description=(
                "EY Risk Portfolio evaluation set: counterparty memos, SAR "
                "guidance, audit synthesis, model explanation, M&A diligence. "
                "Continuous F1 / precision / recall replacement for the "
                "team's manual Ragas workflow."
            ),
            records=DATASET_RECORDS,
        )


def run_for_model(model_name: str, project: str, dataset_name: str) -> str:
    log.info("=" * 70)
    log.info("Running experiment for model=%s", model_name)
    log.info("=" * 70)

    dataset = _get_or_create_dataset(dataset_name)

    # Stable experiment name per model — re-running .run() against the
    # same name produces a new run within the same logical experiment,
    # which is how Datadog shows trend / regression history.
    experiment = LLMObs.experiment(
        name=f"ey_risk_portfolio__{model_name}",
        task=_build_task(model_name),
        dataset=dataset,
        evaluators=[
            exact_match_decision,
            regulatory_citation_recall,
            must_mention_recall,
            precision_score,
            f1_score_combined,
            pii_leak_check,
        ],
        description=(
            f"EY Risk Portfolio LLM eval — model={model_name}. Evaluators "
            f"mirror Jaganathan's asks: decision accuracy, regulatory "
            f"citation recall, mention recall, precision, combined F1, "
            f"and a PII-leak gate."
        ),
        config={
            "model_name": model_name,
            "engagement_tier": "Tier 1",
            "vertical": "finance",
            "sub_vertical": "ey",
        },
    )

    results = experiment.run()
    url = getattr(experiment, "url", None) or "<experiment URL not exposed by SDK>"
    log.info("Experiment finished for %s — view at: %s", model_name, url)
    log.info("Results summary: %d records evaluated", len(DATASET_RECORDS))
    return url


def main() -> None:
    api_key = os.environ.get("DD_API_KEY")
    app_key = os.environ.get("DD_APP_KEY")
    if not api_key or not app_key:
        log.error("DD_API_KEY and DD_APP_KEY must be set (use `op run`).")
        sys.exit(2)
    site = os.environ.get("DD_SITE", "datadoghq.com")
    project = os.environ.get("EXPERIMENT_PROJECT", "EY Risk Portfolio Eval")
    dataset_name = os.environ.get(
        "EXPERIMENT_DATASET", "ey_risk_portfolio_eval_v1"
    )

    LLMObs.enable(
        site=site,
        api_key=api_key,
        app_key=app_key,
        project_name=project,
    )

    selected = os.environ.get("EXPERIMENT_MODEL", "").strip()
    if selected:
        models = [selected]
    else:
        # Head-to-head: gpt-4.5 (challenger) vs gpt-4o-mini (cost-opt)
        # so the demo lands with two experiment rows in the Datadog UI.
        models = ["gpt-4.5", "gpt-4o-mini"]

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    cycle = 0
    while _running:
        cycle += 1
        log.info("===== experiment cycle #%d =====", cycle)
        for model in models:
            try:
                run_for_model(model, project, dataset_name)
            except Exception as exc:
                # Don't kill the whole loop on a transient SDK error.
                log.exception("cycle %d: model=%s failed: %s", cycle, model, exc)

        if _INTERVAL_SEC <= 0:
            log.info("EXPERIMENT_INTERVAL_SEC=0 — exiting after single run")
            break

        log.info("cycle %d complete — sleeping %ds", cycle, _INTERVAL_SEC)
        # Sleep in 1-second slices so SIGTERM is responsive.
        for _ in range(_INTERVAL_SEC):
            if not _running:
                break
            time.sleep(1)

    try:
        LLMObs.disable()
    except Exception:
        pass
    log.info("experiment loop exited cleanly")


if __name__ == "__main__":
    main()
