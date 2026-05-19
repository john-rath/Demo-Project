# data_obs — EY Data Streams Monitoring demo

Real Kafka + Datadog Agent + a three-service Python pipeline that lights
up Datadog **Data Streams Monitoring** for the EY CT Consulting demo.
Sits beside the LLM Observability scaffold so the 5/19 narrative can
pivot from one real product UI to the other without showing a generic
custom dashboard.

## Pipeline topology

```
risk-data-ingester
        │  produces → risk-feature-events-raw
        ▼
risk-feature-pipeline
        │  consumes ← risk-feature-events-raw
        │  enriches (concentration %, covenant breach, null_rate_pct)
        │  produces → risk-eval-jobs
        ▼
risk-eval-agent           (shares the LangGraph service name from ey.yaml)
        │  consumes ← risk-eval-jobs
        │  evaluates (F1 falls when upstream null_rate_pct spikes)
        └─ logs the WATCH / PASS decision
```

All three services run `dd-trace-py` with DSM enabled, so the Datadog
Data Streams view auto-discovers the topology, per-pathway latency,
consumer lag, and bytes throughput.

`risk-eval-agent` shares its `DD_SERVICE` with the LangGraph LLM service
declared in [`verticals/finance/overlays/ey.yaml`](../verticals/finance/overlays/ey.yaml).
That means the Service Catalog page for `risk-eval-agent` shows both the
**DSM** pipeline lineage upstream *and* the **LLM Observability** eval
scoring downstream — the lineage view Scott Llewelyn asked for.

## How to run

The demo workers are gated by the `data-obs` docker-compose profile so a
plain `make up` keeps doing what it always did. From the toolkit root:

```bash
make up-data-obs    # kafka + dd-agent + producer + feature-pipeline + eval-agent
make logs-data-obs  # tail the three Python services
make down-data-obs  # stop only the data-obs profile services
```

`make up-data-obs` wraps `op run --env-file=.env -- docker compose --profile data-obs up -d`
so the same 1Password-backed secret-handling policy applies (see the
top-level README § *Handling secrets*).

You'll also want `make up` running in another shell for the OTel
collector + simulator (which is what emits the LLM Obs traces and the
synthetic `finserv.llm_eval.*` gauges). Both compose project namespaces
share the same network because they share `COMPOSE_PROJECT_NAME`.

## What lands in Datadog

| Product UI | What you see | Driven by |
|---|---|---|
| **Data Streams Monitoring** | `risk-data-ingester → risk-feature-pipeline → risk-eval-agent` pathway, per-hop latency, lag | dd-trace auto-instrumentation of `confluent-kafka` with `DD_DATA_STREAMS_ENABLED=true` |
| **APM service map** | Same three services with consume/produce spans, error rates, throughput | Standard APM trace ingestion via the Datadog Agent |
| **Service Catalog (risk-eval-agent)** | Combined view of DSM lineage + LLM Obs evals on one service entity | Shared `DD_SERVICE=risk-eval-agent` across this container and the LLM Obs trace generator |
| **Logs** | `evaluated: {...}` lines per decision, tagged with engagement / counterparty | `DD_LOGS_ENABLED=true` on the agent, log-collection on the worker containers |

## Demo storyline (5/19)

1. Open the **LLM Eval Scorecard** dashboard — F1 across the three
   models looks healthy.
2. Pivot to **Data Streams Monitoring** — show the three-node pathway,
   `risk-eval-agent` as the terminal. Click into per-pathway latency.
3. Increase `DATA_QUALITY_FAULT_PCT` on `risk-feature-pipeline` (env
   var, docker-compose) to ~0.20 — feature pipeline now emits more
   degraded events.
4. Within ~30s the **eval consumer** F1 starts dropping (visible in
   logs, mirrored in the LLM Eval Scorecard gauge for the affected
   model via the toolkit simulator). Watchdog flags it as an anomaly.
5. Land the message: *"Datadog tells you the upstream data is bad
   before the LLM eval score does. Topic 2 ⟶ topic 4 in one breath."*

## Environment variables of note

| Variable | Default | Purpose |
|---|---|---|
| `KAFKA_BOOTSTRAP` | `kafka:9092` | Broker DNS within the compose network |
| `KAFKA_TOPIC_RAW` | `risk-feature-events-raw` | Producer → feature-pipeline |
| `KAFKA_TOPIC_ENRICHED` | `risk-eval-jobs` | Feature-pipeline → eval-agent |
| `PRODUCER_INTERVAL_SEC` | `1.5` | Producer pacing |
| `DATA_QUALITY_FAULT_PCT` | `0.02` | Fraction of enriched events with elevated `null_rate_pct` — turn this up live for the data-quality cascade demo |
| `DD_SERVICE` | per-service | Drives Datadog Service Catalog grouping |
| `DD_DATA_STREAMS_ENABLED` | `true` | Required client-side flag for DSM pathway propagation |
| `DD_TAGS` | injected by compose | Adds `vertical:finance`, `incident_domain:ai-eval-pipeline`, `dd-demo-toolkit:true` to every span / metric |

## Teardown

`make down-data-obs` stops the profile services. To also drop the Kafka
volume (so the next start has clean offsets): `make clean-all`.
