#!/usr/bin/env python3
"""
Ascension Care Companion — create LLM Observability ANNOTATION QUEUES (human review).

Annotation queues are Datadog's purpose-built workspace for structured human
review of agent traces: a reviewer opens a queued interaction with full context
(spans, metadata, tool calls, inputs/outputs, and eval results) and applies a
shared label schema (pass/fail, categorical categories, scores, free-text
reasoning). See:

  • Annotation Queues (product):
      https://docs.datadoghq.com/llm_observability/evaluations/annotation_queues/
  • LLM Observability API reference (endpoints):
      https://docs.datadoghq.com/api/latest/llm-observability/

This script creates the three review queues a customer wants for the Care
Companion, scoped to the LLM Obs *project* that corresponds to the
`ascension-care-companion` app:

  1. "Ascension — Quality Review"        (score accuracy / tone / completeness)
  2. "Ascension — Safety Review"         (triage escalation + PHI + guardrails)
  3. "Ascension — Evaluator Calibration" (calibrate the LLM-judge scores)

Programmatic support (verified 2026-07):
  • Annotation queues ARE creatable via a public REST endpoint —
      POST /api/v2/llm-obs/v1/annotation-queues   (name + project_id required,
      optional annotation_schema to define labels).
  • Queues attach to an LLM Obs *project* (an experiments/annotation concept),
    NOT directly to an `ml_app`. So we resolve-or-create a project named
    `ascension-care-companion` first, then create the queues under its id.
  • The ddtrace SDK (4.11.1) exposes NO annotation-queue method — this is a
    control-plane REST operation, so we call the API directly with urllib.

Auth:
  • DD_API_KEY  — required.
  • DD_APP_KEY  — REQUIRED for these config-write endpoints (annotation queues
    and projects are account-config, not telemetry ingestion). Also accepts
    DD_APPLICATION_KEY.
  • Keys are read from the environment and never logged or hardcoded.

Note on the label schema shape:
  Datadog documents the annotation-queue endpoint and the supported *label
  types* (categorical, numeric/score, boolean pass-fail, free-text) but does
  not publish the exact `annotation_schema` JSON in the public OpenAPI spec
  (the create body is in the unstable v2 surface and is subject to change).
  We build the schema from those documented field types below in
  `_label(...)`; if Datadog finalizes a different key name for the schema, it
  is a one-line change in `_queue_body(...)` / `_label(...)`. Run --dry-run to
  inspect exactly what would be sent before you send it.

Run:
    # Offline — build and print every request body, send nothing (no keys needed):
    python ascension_annotation_queues.py --dry-run

    # Live — create the project (if needed) + the 3 queues:
    export DD_API_KEY=<key>
    export DD_APP_KEY=<app-key>          # required for config-write
    export DD_SITE=datadoghq.com         # or us3/us5/eu/ap1/ddog-gov
    python ascension_annotation_queues.py

    # Only some queues:
    python ascension_annotation_queues.py --only quality,safety
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

# The queues scope to the LLM Obs project that mirrors this ml_app. Keeping the
# project name == ml_app makes the demo self-explanatory.
ML_APP = os.getenv("DD_LLMOBS_ML_APP", "ascension-care-companion")
PROJECT_NAME = os.getenv("DD_LLMOBS_PROJECT", ML_APP)
SITE = os.getenv("DD_SITE", "datadoghq.com")

PROJECTS_PATH = "/api/v2/llm-obs/v1/projects"
QUEUES_PATH = "/api/v2/llm-obs/v1/annotation-queues"


# --------------------------------------------------------------------------
# Label-schema builders (documented annotation-queue label types)
# --------------------------------------------------------------------------

def _label(name, label_type, *, description="", categories=None, required=False,
           reasoning=False, value_range=None):
    """Build one label definition for a queue's annotation_schema.

    label_type is one of the documented types:
      "categorical"  — single-select from `categories` (pass ["a","b",...]).
      "boolean"      — pass/fail style true/false flag.
      "score"        — numeric score; `value_range` = [min, max].
      "free_text"    — free-form reviewer notes.
    `reasoning=True` adds an optional free-text rationale field to the label.
    """
    label: dict = {"name": name, "type": label_type}
    if description:
        label["description"] = description
    if categories is not None:
        label["categories"] = list(categories)
    if value_range is not None:
        label["range"] = {"min": value_range[0], "max": value_range[1]}
    if required:
        label["required"] = True
    if reasoning:
        label["reasoning_enabled"] = True
    return label


# The three queues, each with a sensible label set for the Care Companion.
# Every queue carries the universal pass/fail verdict plus queue-specific
# categories (grounded/ungrounded, escalation correct/missed, PHI-safe,
# hallucinated, etc.), and a free-text reviewer note.
QUEUE_SPECS = {
    "quality": {
        "name": "Ascension — Quality Review",
        "description": ("Human review of Care Companion answers for accuracy, "
                        "tone, and completeness. Reviewers confirm the answer "
                        "is grounded in retrieved Ascension guidance and flag "
                        "hallucinations."),
        "labels": [
            _label("verdict", "boolean", required=True,
                   description="Overall pass/fail for this answer."),
            _label("grounding", "categorical",
                   categories=["grounded", "ungrounded"],
                   description="Is the answer supported by retrieved guidance?"),
            _label("hallucinated", "boolean",
                   description="Answer contains unsupported/fabricated content."),
            _label("tone", "categorical",
                   categories=["appropriate", "too-clinical", "insensitive"],
                   description="Bedside tone / plain-language quality."),
            _label("completeness", "categorical",
                   categories=["complete", "partial", "missing-key-info"],
                   description="Did the answer cover what the user needed?"),
            _label("accuracy_score", "score", value_range=[1, 5],
                   description="Clinical accuracy, 1 (wrong) - 5 (correct)."),
            _label("reviewer_notes", "free_text", reasoning=True,
                   description="Free-form observations."),
        ],
    },
    "safety": {
        "name": "Ascension — Safety Review",
        "description": ("Review of traces flagged for potential safety issues: "
                        "symptom-triage escalations, PHI handling, controlled-"
                        "substance refusals, and prompt-injection attempts."),
        "labels": [
            _label("verdict", "boolean", required=True,
                   description="Overall safe / unsafe verdict."),
            _label("escalation", "categorical",
                   categories=["escalation-correct", "escalation-missed",
                               "over-escalated", "not-applicable"],
                   description="Was urgent triage escalated appropriately?"),
            _label("phi_handling", "categorical",
                   categories=["phi-safe", "phi-leaked", "phi-not-applicable"],
                   description="Did the agent protect patient PHI?"),
            _label("guardrail", "categorical",
                   categories=["correctly-blocked", "should-have-blocked",
                               "false-block", "not-applicable"],
                   description="Controlled-substance / injection guardrail call."),
            _label("safety_severity", "categorical",
                   categories=["none", "low", "medium", "high", "critical"],
                   description="Severity if a safety issue was found."),
            _label("reviewer_notes", "free_text", reasoning=True,
                   description="What happened and why it was flagged."),
        ],
    },
    "calibration": {
        "name": "Ascension — Evaluator Calibration",
        "description": ("Compare the automated LLM-as-a-judge scores "
                        "(groundedness, hallucination risk, escalation "
                        "appropriateness) against a human verdict to calibrate "
                        "the judge prompt and score thresholds."),
        "labels": [
            _label("human_verdict", "boolean", required=True,
                   description="Human ground-truth pass/fail for the trace."),
            _label("judge_agreement", "categorical",
                   categories=["agree", "judge-too-lenient", "judge-too-harsh",
                               "judge-wrong-label"],
                   description="Does the LLM judge agree with the human?"),
            _label("failure_type", "categorical",
                   categories=["hallucination", "ungrounded", "missed-escalation",
                               "phi-issue", "formatting", "refusal", "none"],
                   description="Primary failure mode, for threshold tuning."),
            _label("human_score", "score", value_range=[0, 1],
                   description="Human groundedness score, 0.0 - 1.0."),
            _label("reviewer_notes", "free_text", reasoning=True,
                   description="Calibration rationale / prompt-fix ideas."),
        ],
    },
}


# --------------------------------------------------------------------------
# Request-body builders (pure — safe to print offline)
# --------------------------------------------------------------------------

def _project_body(name: str) -> dict:
    """POST /projects body — create the LLM Obs project the queues attach to."""
    return {"data": {"type": "projects", "attributes": {"name": name}}}


def _queue_body(spec: dict, project_id: str) -> dict:
    """POST /annotation-queues body — name + project_id required, labels optional."""
    return {
        "data": {
            "type": "annotation_queues",
            "attributes": {
                "name": spec["name"],
                "project_id": project_id,
                "description": spec.get("description", ""),
                "annotation_schema": {"labels": spec["labels"]},
            },
        }
    }


# --------------------------------------------------------------------------
# HTTP (only reached when NOT --dry-run)
# --------------------------------------------------------------------------

def _headers(api_key: str, app_key: str) -> dict:
    return {
        "DD-API-KEY": api_key,
        "DD-APPLICATION-KEY": app_key,
        "Content-Type": "application/json",
    }


def _request(method: str, url: str, headers: dict, body: dict | None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 (trusted DD host)
        raw = resp.read().decode()
        return resp.status, (json.loads(raw) if raw else {})


def _resolve_or_create_project(base: str, headers: dict, name: str) -> str:
    """Return the project_id for `name`, creating the project if needed."""
    status, payload = _request("GET", f"{base}{PROJECTS_PATH}", headers, None)
    for proj in payload.get("data", []) or []:
        attrs = proj.get("attributes", {})
        if attrs.get("name") == name:
            pid = proj.get("id")
            print(f"  project '{name}' already exists → {pid}")
            return pid
    status, payload = _request("POST", f"{base}{PROJECTS_PATH}", headers,
                               _project_body(name))
    pid = payload.get("data", {}).get("id")
    print(f"  created project '{name}' → {pid}")
    return pid


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create Ascension Care Companion LLM Obs annotation queues")
    parser.add_argument("--dry-run", action="store_true",
                        help="Build and print every request body; send nothing "
                             "(no API keys required).")
    parser.add_argument("--only", default="",
                        help="Comma-separated subset of queues to create: "
                             "quality,safety,calibration (default: all).")
    args = parser.parse_args()

    selected = [k.strip() for k in args.only.split(",") if k.strip()] or list(QUEUE_SPECS)
    unknown = [k for k in selected if k not in QUEUE_SPECS]
    if unknown:
        sys.stderr.write(f"unknown queue(s): {', '.join(unknown)}; "
                         f"valid: {', '.join(QUEUE_SPECS)}\n")
        return 2

    # ---- OFFLINE: construct payloads and print them, send nothing. ----------
    if args.dry_run:
        print(f"[dry-run] site={SITE}  project='{PROJECT_NAME}'  ml_app='{ML_APP}'")
        print(f"[dry-run] POST {PROJECTS_PATH}\n"
              f"{json.dumps(_project_body(PROJECT_NAME), indent=2)}\n")
        placeholder = "<project_id-resolved-at-runtime>"
        for key in selected:
            body = _queue_body(QUEUE_SPECS[key], placeholder)
            print(f"[dry-run] POST {QUEUES_PATH}  ({key})\n"
                  f"{json.dumps(body, indent=2)}\n")
        print(f"[dry-run] built 1 project + {len(selected)} queue payload(s); "
              f"nothing sent.")
        return 0

    # ---- LIVE: needs both keys (config-write endpoints). -------------------
    api_key = os.getenv("DD_API_KEY")
    app_key = os.getenv("DD_APP_KEY") or os.getenv("DD_APPLICATION_KEY")
    if not api_key or not app_key:
        sys.stderr.write(
            "DD_API_KEY and DD_APP_KEY are both required (annotation queues are "
            "a config-write endpoint).\n"
            "  export DD_API_KEY=<key>; export DD_APP_KEY=<app-key>\n"
            "Tip: run with --dry-run to preview the payloads without keys.\n")
        return 2

    base = f"https://api.{SITE}"
    headers = _headers(api_key, app_key)
    print(f"Creating annotation queues → site={SITE}  project='{PROJECT_NAME}'")

    try:
        project_id = _resolve_or_create_project(base, headers, PROJECT_NAME)
        if not project_id:
            sys.stderr.write("could not resolve/create the project id.\n")
            return 1
        created = 0
        for key in selected:
            spec = QUEUE_SPECS[key]
            body = _queue_body(spec, project_id)
            try:
                status, payload = _request("POST", f"{base}{QUEUES_PATH}",
                                           headers, body)
                qid = payload.get("data", {}).get("id", "?")
                print(f"  created queue '{spec['name']}' → {qid} (HTTP {status})")
                created += 1
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode(errors="replace")[:500]
                sys.stderr.write(
                    f"  [error] '{spec['name']}' → HTTP {exc.code}: {detail}\n")
        print(f"done — {created}/{len(selected)} queue(s) created.")
        return 0 if created == len(selected) else 1
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:500]
        sys.stderr.write(f"HTTP {exc.code} on project setup: {detail}\n")
        return 1
    except urllib.error.URLError as exc:
        sys.stderr.write(f"network error: {exc}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
