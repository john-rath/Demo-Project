# Ascension Care Companion — Agentic LLM Observability Demo

A standalone, **agentless** app that streams realistic **agentic** traces to
Datadog **LLM Observability** — no Datadog Agent, no OTel collector, **no
Docker**. Point it at an org with an API key and it drives the full
agentic-monitoring surface, branded for Ascension Health's multi-campus system.

Built for a live CTO walkthrough. All data is synthetic — no customer data.

## What it shows (the full agentic suite)

- **Agent execution graph** — a root `agent` span orchestrating nested
  `llm` / `tool` / `embedding` / `retrieval` spans. This is the "what did the
  agent actually do, step by step" view.
- **Tool calls** — `patient_context_emr` (multi-campus EMR), `rtls_bed_status`,
  `prior_auth_lookup`, `appointment_scheduler`, and a `safety_guardrail`.
- **RAG** — query `embedding` → `retrieval` over Ascension care-guidance KB, with
  document scores.
- **Evaluations** — clinical groundedness, hallucination risk, PHI handling,
  answer relevance, escalation appropriateness (+ prompt-injection-blocked).
- **Cost / tokens / latency** per span, across **model experiments**
  (gpt-4o vs gpt-4o-mini vs claude-3-5-sonnet).
- **Safety & security** — controlled-substance refusals and prompt-injection
  attempts surface as **guardrail-blocked error traces**.
- **A live quality regression** — every ~15 min a ~3 min *degraded* window:
  RAG retrieval slows, hallucination rises, groundedness drops — then recovers.
  Great for showing evaluations (and any monitors on them) firing and healing.

Everything is tagged with `campus`, `scenario`, `model`, `role`, `env`, `phase`
so you can slice the LLM Obs views live.

## Prerequisites

- A Datadog **API key** and your **site** (agentless sends straight to Datadog).
- Either **Python 3.9+** (standalone) or a container runtime (**Podman**,
  nerdctl, Apple `container`, Finch) — Docker not required.

## Run it — standalone (simplest)

```bash
export DD_API_KEY=<your-key>
export DD_SITE=datadoghq.com          # or us3 / us5 / eu / ap1 / ddog-gov
./run.sh                               # continuous; Ctrl-C to stop
# or:
./run.sh --count 200                  # emit 200 traces then exit
./run.sh --interval 2                 # 2s between traces
```

`run.sh` uses your current Python if `ddtrace` is importable, otherwise it
creates a throwaway `.venv-agentic` and installs `ddtrace` for you.

## Run it — containerized, non-Docker (Podman / nerdctl)

```bash
podman build -t ascension-care-agent -f Containerfile .
podman run --rm \
  -e DD_API_KEY=$DD_API_KEY -e DD_SITE=${DD_SITE:-datadoghq.com} \
  ascension-care-agent --count 300
```

(`nerdctl` / Apple `container` / Finch use the same commands. It's a plain OCI
image, so `docker build/run` also works if you ever want it.)

## Configuration (env vars)

| Var | Default | Purpose |
|---|---|---|
| `DD_API_KEY` | — (required) | Agentless auth |
| `DD_SITE` | `datadoghq.com` | Datadog site |
| `DD_LLMOBS_ML_APP` | `ascension-care-companion` | LLM Obs application name |
| `DD_SERVICE` | `ascension-care-companion` | Service tag |
| `DD_ENV` | `prod` | Environment tag |
| `COMPANION_CYCLE_SEC` | `900` | Degrade cycle length (s) |
| `COMPANION_DEGRADED_SEC` | `180` | Degrade window length (s) |

CLI: `--count N` (0 = forever), `--interval S`, `--burst N` (fast initial traces).

## Where to look in Datadog

**LLM Observability** → filter to `ml_app:ascension-care-companion`:
- **Traces** — open a `multi_campus_transfer` trace → see the agent graph fan out
  across EMR + RTLS + scheduler tools, embedding, retrieval, and generation.
- **Evaluations** — groundedness / hallucination / escalation over time; watch
  them dip during a degraded window.
- **Clusters / Topics, Cost, Latency** — slice by `model` and `campus`.
- **Error Tracking** — the `prompt_injection` and `controlled_substance_dosing`
  traces show as `GuardrailBlocked`.

## Suggested CTO walkthrough (~5 min)

1. **Agent graph.** Open a `multi_campus_transfer` trace. "This is the agent's
   actual reasoning path — it looked up the patient's EMR, checked real-time bed
   availability via RTLS across campuses, and scheduled transport, all under one
   traced agent run."
2. **Evaluations.** Show groundedness + hallucination on the eval timeline.
   "Every answer is scored for clinical grounding and hallucination risk —
   automatically."
3. **The regression.** During a degraded window, point at hallucination rising /
   groundedness dropping and the slow `retrieval` span. "Here's a live quality
   regression — retrieval slowed, grounding fell. Your monitors catch this before
   a clinician does."
4. **Safety.** Open the `prompt_injection` trace → `GuardrailBlocked`. "Someone
   tried to jailbreak it into exposing other patients' records. The guardrail
   blocked it and it's fully audited."
5. **Cost / experiments.** Group by `model`. "Same workload across gpt-4o vs
   mini vs Claude — quality and cost side by side, so you pick with data."

## Notes

- **Org:** agentless points at whatever `DD_API_KEY` / `DD_SITE` you set — your
  own sandbox or the demo org. Nothing else to configure.
- This is separate from the Docker `dd-demo-toolkit` stack; it stands alone so
  you can run it anywhere (laptop, jump host) without the container fleet.
