# Ascension Care Companion — LLM Observability Experiments

A standalone, **agentless** script that runs ~20 **Datadog LLM Observability
Experiments** for Ascension Health's AI Care Companion — a repeatable,
versioned, multi-provider evaluation of clinical answer quality and safety.
Modeled on the "EY Risk Portfolio Eval" experiments (dataset + task +
evaluators, run once per model), but for a clinical-safety domain.

All data is synthetic and Ascension-branded — no customer data, and **no real
provider API keys**. The task *simulates* each model's answer using a per-model
quality profile, so the experiment metrics genuinely differ across providers.

File: [`ascension_experiments.py`](ascension_experiments.py)

## What it shows

- **One dataset, many models.** A single clinical eval dataset
  (`ascension_care_companion_eval_v1`, 20 synthetic records) is run against a
  matrix of 8 model providers × 2 prompt variants (+4 flagship re-runs) = **20
  experiments**, all grouped under one project.
- **The provider tradeoff table.** In LLM Obs → Experiments you get a
  head-to-head grid: decision accuracy, combined F1, safety-phrase recall,
  precision, regulatory-citation recall, clinical groundedness, and escalation
  correctness — per model, per prompt variant, alongside indicative cost.
- **Clinical safety, scored.** Scenarios include cardiac triage (must
  escalate), controlled-substance refusal (must refuse + escalate), and
  prompt-injection (must refuse). Weaker models miss escalations and comply
  with injections more often — and the `escalation_correct` evaluator surfaces
  `missed_escalation` as a first-class category you can filter on.

### The scenarios (dataset)

Medication guidance · discharge instructions · cardiac symptom triage
(escalate) · controlled-substance dosing (refuse + escalate) · prior
authorization · multi-campus / NICU / stroke transfer · care-plan summary ·
prompt injection (refuse). Each record carries machine-checkable eval targets
in `metadata`: `must_mention` (required safety phrases), `expected_escalation`,
and `required_citations` (Ascension policies, e.g. `ECP-19.4`).

### The evaluators

| Evaluator | Type | What it measures |
|---|---|---|
| `exact_match_decision` | score (0/1) | Model's decision label matches the ideal decision |
| `f1_score_combined` | score | Combined precision/recall over decision + mentions + citations |
| `must_mention_recall` | score | Fraction of required safety phrases surfaced |
| `precision_score` | score | Of what the model surfaced, how much was correct |
| `regulatory_citation_recall` | score | Fraction of required Ascension policies cited |
| `clinical_groundedness` | score | Grounded in policy, no fabrication, safe escalation |
| `escalation_correct` | categorical | `correct_escalation` / `missed_escalation` / `over_escalation` / `correct_no_escalation` |

### The provider / experiment matrix

8 models × 2 prompt variants (`grounded`, `concise`) + 4 flagship `grounded`
re-runs = **20 experiments**, named `ascension_care__<model>__<variant>`:

| Provider | Model | $/1M in | $/1M out |
|---|---|---|---|
| openai | gpt-4o | 2.50 | 10.00 |
| openai | gpt-4o-mini | 0.15 | 0.60 |
| anthropic | claude-3-5-sonnet | 3.00 | 15.00 |
| anthropic | claude-3-opus | 15.00 | 75.00 |
| google | gemini-1.5-pro | 1.25 | 5.00 |
| mistral | mistral-large | 2.00 | 6.00 |
| meta | llama-3.1-70b-instruct | 0.90 | 0.90 |
| cohere | command-r-plus | 2.50 | 10.00 |

The `grounded` variant is the production system prompt (retrieval-grounded,
strict escalation). The `concise` variant trades some safety-phrase recall for
brevity, so the prompt axis is visible in the tradeoff view.

## How to run

`ddtrace` (with the Experiments SDK) is the only dependency and is already a
toolkit dep. Agentless — no Datadog Agent, no Docker.

```bash
export DD_API_KEY=<your-key>
export DD_SITE=datadoghq.com          # or us3 / us5 / eu / ap1 / ddog-gov
python ascension_experiments.py                 # all ~20 experiments
```

Options:

```bash
python ascension_experiments.py --limit 4                     # first 4 only (quick smoke)
python ascension_experiments.py --project "My Ascension Eval" # custom project name
```

| Flag / env | Default | Purpose |
|---|---|---|
| `DD_API_KEY` | — (required) | Agentless auth; script exits 2 with a clear error if unset |
| `DD_SITE` | `datadoghq.com` | Datadog site |
| `--project` / `EXPERIMENT_PROJECT` | `Ascension Care Companion Quality` | LLM Obs project the dataset + experiments land in |
| `--limit` | `0` (all) | Run only the first N experiments |
| `EXPERIMENT_DATASET` | `ascension_care_companion_eval_v1` | Dataset name |

The script prints per-experiment progress (`[i/N] ascension_care__… done`).

## Where to view it

**Datadog → LLM Observability → Experiments**, then filter to the project
**`Ascension Care Companion Quality`**.

- The **experiments list** shows all 20 runs (one row per
  `ascension_care__<model>__<variant>`), each with its aggregate evaluator
  scores.
- Open the **compare / tradeoff view** to put providers side by side on any
  metric (F1, groundedness, escalation correctness) — and against cost.
- Drill into any experiment → per-record results to see exactly which
  scenario a model missed (e.g. a `missed_escalation` on cardiac triage).
- The dataset lives under **LLM Observability → Datasets →
  `ascension_care_companion_eval_v1`**.

## Demo talking-track (~3–4 min)

1. **"One dataset, every model."** Open the project. "This is the same 20
   clinical scenarios — triage, discharge, controlled-substance refusal,
   prompt injection — run against every model we're considering. It replaces
   the manual spreadsheet the team keeps today."
2. **The tradeoff view.** Sort by `clinical_groundedness` or `f1_score_combined`.
   "Claude-3.5-Sonnet and GPT-4o lead on grounding; the mini models are cheaper
   but drop safety-phrase recall. Now you're choosing a clinical model with
   data, not vibes."
3. **Cost vs. quality.** "Put groundedness on one axis and cost on the other.
   claude-3-opus is top-tier but 5× the price of Sonnet for a marginal gain —
   the tradeoff view makes that call obvious."
4. **The safety story.** Filter `escalation_correct = missed_escalation`. "Here
   are the exact cases where a weaker model *failed to escalate a cardiac
   emergency* or complied with a prompt-injection. That's the row you never
   want in production — and it's caught before go-live, automatically."
5. **Prompt variants.** Compare `grounded` vs `concise` for one model. "Same
   model, two prompts. The concise prompt saves tokens but loses safety-phrase
   recall — quantified, not guessed."

## Notes

- Separate from both the Docker `dd-demo-toolkit` stack and the streaming
  [`ascension_care_agent.py`](ascension_care_agent.py) (which emits live agent
  *traces*). This script emits *experiments* — the offline, repeatable
  model-comparison surface. They share the `ascension-care-companion` `ml_app`
  so a demo can show live traces and the eval scorecard side by side.
- Task outputs are deterministic per `(model, prompt_variant, record)`, so
  re-runs are stable and trends are meaningful.
- To validate the pure task/evaluator logic offline (no key, no network), the
  functions can be imported and called directly — see the module docstring.
