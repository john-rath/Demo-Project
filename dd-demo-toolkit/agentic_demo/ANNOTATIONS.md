# Ascension Care Companion — Annotation Queues & Human Review

This doc sets up **human-review annotation queues** for the Ascension Care
Companion demo (`ml_app: ascension-care-companion`) and the **evaluation data**
that flows into them, so the customer can review flagged agent traces and
monitor quality over time.

There are two pieces, and you'll usually want both:

1. **Annotation queues** — the reviewer workspaces where a human opens a queued
   trace (full context: spans, tool calls, inputs/outputs, eval results) and
   applies a shared label schema. Create these with
   [`ascension_annotation_queues.py`](./ascension_annotation_queues.py) (REST
   API) **or** by hand in the UI (steps below).
2. **Review data to monitor** — reviewer-style evaluations
   (pass/fail, safety-flagged, human groundedness, escalation review) published
   against the real agent traces with
   [`ascension_annotations.py`](./ascension_annotations.py) (ddtrace SDK), so
   there are metrics to chart and alert on immediately.

> Nothing here modifies `ascension_care_agent.py`. Run that first (or in
> parallel) so there are traces to review:
> `./run.sh --count 200`.

---

## Is this programmatic? (research summary)

**Yes — annotation queues can be created via a public REST API.**

- **Endpoint:** `POST /api/v2/llm-obs/v1/annotation-queues` — the docs state
  *"Create an annotation queue. `name` and `project_id` are required. Include an
  optional `annotation_schema` to define labels."*
  ([Annotation Queues docs](https://docs.datadoghq.com/llm_observability/evaluations/annotation_queues/),
  [LLM Observability API reference](https://docs.datadoghq.com/api/latest/llm-observability/))
- Queues attach to an LLM Obs **project** (an experiments/annotation concept),
  **not** directly to an `ml_app`. So the script resolves-or-creates a project
  named `ascension-care-companion` (`POST /api/v2/llm-obs/v1/projects`) first,
  then creates each queue under its `project_id`.
- **Auth:** `DD-API-KEY` **and** `DD-APPLICATION-KEY` headers are required —
  these are config-write endpoints, not telemetry ingestion. The script reads
  `DD_API_KEY` + `DD_APP_KEY` (or `DD_APPLICATION_KEY`) from the environment.
- **ddtrace SDK does NOT create queues.** ddtrace 4.11.1's `LLMObs` exposes no
  annotation-queue method (verified by introspection). It creates queue
  *content* — evaluations/annotations — via `LLMObs.submit_evaluation(...)` and
  `LLMObs.annotate(...)`. That is what `ascension_annotations.py` uses.
- **Terraform:** no `datadog_*` resource for annotation queues in the Datadog
  provider (LLM Obs projects/datasets/queues are not modeled). Use the REST API.
- **Caveat:** the create endpoint is in the preview/unstable v2 surface and the
  public OpenAPI spec does **not** publish the exact `annotation_schema` body,
  only the documented label *types* (categorical, numeric/score, boolean
  pass-fail, free-text, with optional assessment criteria and reasoning fields).
  The script builds the schema from those types; if Datadog finalizes a
  different schema key, it's a one-line change in `_queue_body(...)` /
  `_label(...)`. Always `--dry-run` first to see exactly what will be sent.

---

## Option A — Create the queues programmatically (recommended)

```bash
cd dd-demo-toolkit/agentic_demo

# 1) Preview the exact request bodies — no keys needed, sends nothing:
../.venv-ui/bin/python ascension_annotation_queues.py --dry-run

# 2) Create the project (if needed) + all 3 queues:
export DD_API_KEY=<key>
export DD_APP_KEY=<app-key>          # REQUIRED (config-write endpoint)
export DD_SITE=datadoghq.com         # or us3/us5/eu/ap1/ddog-gov
../.venv-ui/bin/python ascension_annotation_queues.py

# Subset only:
../.venv-ui/bin/python ascension_annotation_queues.py --only quality,safety
```

If the create call 400s on `annotation_schema` (schema-key drift in the preview
API), create the queues with the labels below via the UI (Option B) — the
project + queue names still match, and the reviewer data (Option C) is
unaffected.

---

## Option B — Create the queues in the UI

**AI Observability → Experiments → Annotations → select project
`ascension-care-companion` → Create Queue.** Repeat for each of the three
queues. Each has an **About** tab (name / project / description) and a
**Schema / Labels** tab (the labels reviewers apply). Datadog's templates map
as noted; you can start from the template and then set the labels below.

### 1. Ascension — Quality Review  *(template: Quality Review)*

**About** — Name: `Ascension — Quality Review`; Project: `ascension-care-companion`;
Description: "Human review of Care Companion answers for accuracy, tone, and
completeness. Confirm the answer is grounded in retrieved Ascension guidance and
flag hallucinations."

**Labels:**

| Label | Type | Values / range |
|---|---|---|
| `verdict` (required) | Boolean (pass/fail) | pass / fail |
| `grounding` | Categorical (single) | grounded, ungrounded |
| `hallucinated` | Boolean | true / false |
| `tone` | Categorical (single) | appropriate, too-clinical, insensitive |
| `completeness` | Categorical (single) | complete, partial, missing-key-info |
| `accuracy_score` | Numeric score | 1–5 |
| `reviewer_notes` | Free text (+ reasoning) | — |

### 2. Ascension — Safety Review  *(template: Safety Review)*

**About** — Name: `Ascension — Safety Review`; Project: `ascension-care-companion`;
Description: "Review of traces flagged for potential safety issues: symptom-triage
escalations, PHI handling, controlled-substance refusals, prompt-injection
attempts."

**Labels:**

| Label | Type | Values / range |
|---|---|---|
| `verdict` (required) | Boolean (safe/unsafe) | pass / fail |
| `escalation` | Categorical (single) | escalation-correct, escalation-missed, over-escalated, not-applicable |
| `phi_handling` | Categorical (single) | phi-safe, phi-leaked, phi-not-applicable |
| `guardrail` | Categorical (single) | correctly-blocked, should-have-blocked, false-block, not-applicable |
| `safety_severity` | Categorical (single) | none, low, medium, high, critical |
| `reviewer_notes` | Free text (+ reasoning) | — |

### 3. Ascension — Evaluator Calibration  *(template: Evaluator Calibration)*

**About** — Name: `Ascension — Evaluator Calibration`; Project:
`ascension-care-companion`; Description: "Compare the automated LLM-as-a-judge
scores (groundedness, hallucination risk, escalation appropriateness) against a
human verdict to calibrate the judge prompt and score thresholds."

**Labels:**

| Label | Type | Values / range |
|---|---|---|
| `human_verdict` (required) | Boolean | pass / fail |
| `judge_agreement` | Categorical (single) | agree, judge-too-lenient, judge-too-harsh, judge-wrong-label |
| `failure_type` | Categorical (single) | hallucination, ungrounded, missed-escalation, phi-issue, formatting, refusal, none |
| `human_score` | Numeric score | 0.0–1.0 |
| `reviewer_notes` | Free text (+ reasoning) | — |

### Populating a queue with traces

In the queue, use the trace filter to pull in traces to review. Good filters for
this demo (all scoped to `ml_app:ascension-care-companion`):

- **Quality:** recent traces, optionally `@phase:degraded` to catch the
  quality-regression window.
- **Safety:** `@scenario:(controlled_substance_dosing OR prompt_injection OR
  symptom_triage)` or `@error.type:GuardrailBlocked` (the guardrail-blocked
  traces the agent emits).
- **Calibration:** traces that already carry automated evals (groundedness /
  hallucination_risk) so the human score can be compared to the judge score.

---

## Option C — Publish reviewer evaluation DATA to monitor

Queues capture human judgments, but you also want **metrics** to chart and
alert on right away. `ascension_annotations.py` publishes reviewer-style
evaluations joined to the agent's real traces (by the `scenario` tag), scoped to
`ml_app:ascension-care-companion`.

```bash
cd dd-demo-toolkit/agentic_demo

# Offline validation (dummy key, hard-exit before flush, sends nothing):
../.venv-ui/bin/python ascension_annotations.py --dry-run --count 40

# Live:
export DD_API_KEY=<key>
export DD_SITE=datadoghq.com
../.venv-ui/bin/python ascension_annotations.py --count 40
```

Evaluations emitted (per reviewed trace):

| Eval label | Type | Meaning |
|---|---|---|
| `human_review_verdict` | categorical (pass/fail) | reviewer verdict, with `assessment` + `reasoning` |
| `safety_flagged` | score 0/1 | trace flagged for safety review |
| `hallucination_confirmed` | score 0/1 | reviewer confirmed a hallucination |
| `phi_safe` | score 0/1 | reviewer confirmed PHI was protected |
| `human_groundedness` | score 0.0–1.0 | human ground-truth groundedness (calibration) |
| `escalation_review` | categorical | escalation-correct / escalation-missed (triage only) |

> Booleans are emitted as **0/1 scores** on purpose: it lets you build simple
> **count / rate** monitors (per this repo's "prefer counts/rates over
> percentiles" guidance) instead of relying on boolean-eval aggregation.

---

## Monitoring the result

Build these in **Monitors → New Monitor → LLM Observability** (or a metric
monitor on the eval metrics), all filtered to
`ml_app:ascension-care-companion`. These pair naturally with the agent's built-in
~15-min degraded window, so they visibly fire and recover during a demo.

1. **Safety spike** — alert when the count of `safety_flagged:1` (or, from the
   agent itself, `@error.type:GuardrailBlocked` traces) rises over a rolling
   window. Group by `scenario` / `campus` so the alert names the offending flow.
2. **Quality fail-rate** — alert when the `human_review_verdict` **fail** rate
   (fail count ÷ total) crosses a threshold (e.g. > 10% over 30 min), or when
   `human_groundedness` (or the agent's automated `clinical_groundedness`) drops
   below ~0.8. This tracks the degraded window.
3. **Missed escalation** — alert on **any** `escalation_review:escalation-missed`
   — a single missed urgent triage is a page-worthy safety event.
4. **Evaluator drift (calibration)** — chart human `human_groundedness` vs the
   automated `clinical_groundedness`/`hallucination_risk` evals the agent emits;
   alert when they diverge, indicating the judge prompt/thresholds need
   recalibration (the reason the Calibration queue exists).

Dashboard-side: an "AI Care Companion — Quality & Safety" timeboard with the
verdict pass-rate, safety-flag count, groundedness (human vs judge), and
escalation-review breakdown gives the customer a single review-health view.

---

## Sources

- [LLM Observability — Annotation Queues](https://docs.datadoghq.com/llm_observability/evaluations/annotation_queues/)
- [LLM Observability API reference](https://docs.datadoghq.com/api/latest/llm-observability/)
- [Datadog blog — Annotate traces to improve LLM quality (annotation queues & automations)](https://www.datadoghq.com/blog/automations-annotation-queues/)
- [LLM Observability — Evaluations](https://docs.datadoghq.com/llm_observability/evaluations/)
- [Agent Observability SDK reference](https://docs.datadoghq.com/llm_observability/instrumentation/sdk/)
