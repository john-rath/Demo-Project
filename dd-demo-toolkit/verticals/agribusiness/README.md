# Agribusiness vertical (`agribusiness`, env_prefix `agri`)

A global agribusiness IT estate, modeled for an **observability-consolidation**
"art of the possible." Flagship scenario: **Bunge** (post-Viterra), building
global monitoring from scratch — 28+ tools → one platform.

## Why its own vertical (not a finance/manufacturing overlay)

An overlay always deploys its **base vertical's** assets too. Bunge on `finance`
would drag in ATMs / SWIFT / retail-banking dashboards; on `manufacturing`,
assembly lines and OEE. Neither is on-message for an agribusiness VP. This is a
standalone vertical so the demo shows **only** Bunge's world — and it's reusable
for future agri prospects (Cargill, ADM, Viterra-adjacent).

## The thesis it's built around

Every revenue app — **Bunge Mobile, BungeServices, myBunge, FRM, BungeAg** —
depends on **one GCP data platform** (SAP + Salesforce + CME market data). A
single data issue (stale CME feed, SAP corruption, failed pipeline) degrades all
of them at once, and nothing watches it today. The service dependency graph in
`config.yaml` encodes that so **AIOps (Watchdog / Bits AI)** can surface it.

## What it models (mapped to Eduardo's stated domains)

| Domain | In the toolkit |
|---|---|
| **Infrastructure** | `agri.host.*` (2,000+ VMware hosts), `agri.cloud.*` (AWS/GCP / GKE) |
| **Network** (185 sites, 500+ links, SolarWinds replacement) | `agri.site.connectivity_pct`, `agri.network.*` (latency, packet loss, throughput, device CPU, interface errors) |
| **Databases — Oracle (DBM)** | `agri.db.*`: query latency, `slow_query_count` (the "SELECT *" story), replication lag, connections. DBM supports Oracle/SQL Server/Postgres/MySQL — **not** HANA. |
| **SAP HANA** | `agri.saphana.*`: memory, CPU, connections, disk, blocked transactions, availability — via **Redpeaks SAP HANA** (Marketplace; agentless single collector, `redpeaks.hana.*`). No query-level SQL: HANA isn't DBM and the collector doesn't expose expensive SQL. |
| **SAP S/4HANA / NetWeaver** | `agri.sapnw.*`: work processes, ABAP response, dumps, IDOC/RFC, batch jobs — via **Redpeaks / Agentil S/4HANA & NetWeaver** (Marketplace). |
| **SAP Cloud ALM** | `agri.sapalm.*`: integration (CPI/AIF/Ariba) message counts + failures, exceptions, jobs — via **RapDev SAP Cloud ALM** (Marketplace; Cloud ALM APIs → OTel). Successor to SAP Solution Manager. |
| **SAP Integration Suite / CPI** | Logs via API (Log Management) — Bunge already ingests these. |
| **GCP Data Platform** (the linchpin) | `agri.dataplatform.*` (CME feed age, pipeline freshness/errors, SAP sync lag) |
| **Revenue apps** | services → APM traces + `agri.app.requests_total / errors_total / latency_ms` |
| **AIOps** | **Live cascade**: `plugins/data_platform_cascade.py` drives a stale-CME-feed → FRM-mispricing incident (root→symptom `signal_chain:` tags) + an **RCA notebook** that matches what Watchdog/Bits surface |

Assets: 3 dashboards (Global KPI, Apps & Data Platform [cross-cutting], and
Infrastructure/Network/SAP), 14 monitors, 5 SLOs (incl. the myBunge
**10-minute scale-ticket** SLA), 6 Service Catalog entries, **1 incident-cascade
plugin + 1 RCA investigation notebook**. SAP views show only signals Datadog
actually collects (verified against the Marketplace + Datadog docs).

## Run it — UI-first (`make ui` is the only command)

```bash
make ui          # preflights, then opens the web UI at http://127.0.0.1:8765
```

Then everything happens in the browser (no terminal):
1. **Configure** — pick the **Global Agribusiness (Bunge)** vertical, set/verify
   your Datadog credentials (`op://` refs), Save. (The UI validates the assets.)
2. **Simulator** — **Start** to emit `agri.*` telemetry.
3. **Deploy assets** — **Deploy** the dashboards / monitors / SLOs / services.
   **Tear down** resets the org.

_Advanced / CI only_ (the CLI is the engine beneath the UI, not the SE path):
`dd-demo validate --vertical agribusiness`, `make up`, `make setup`, `make teardown`.

Pairs with `Bunge-art-of-the-possible-demo-script.md`: Global KPI → the open;
Infrastructure/Network/SAP → Eduardo's infra/network/DB towers; Apps & Data
Platform → the cross-cutting AIOps centerpiece.

## Conventions / guardrails

- Namespace `agri.*`. **No percentile metrics** (team preference) — gauges +
  counters, avg+max "band" charts instead of `p95:`.
- Tags use existing keys only (`incident_domain:`, `signal_chain:`,
  `sla_critical:`) — new values, no new keys. No customer-confidential data.

## Live cascade & RCA notebook (the AIOps centerpiece)

- **`plugins/data_platform_cascade.py`** drives a 4-phase, ~10-minute incident:
  the CME pricing feed goes stale (`agri.dataplatform.pricing_feed_age_sec` →
  ~480s) → FRM mispricing (`agri.pricing.stale_quote_pct` → ~12%), tripping the
  **CME Pricing Feed Staleness** (root, `1-root-cause`) and **FRM Stale-Quote
  Rate** (symptom, `3-symptom`) monitors. It writes `engine.incident_state`
  (`incident_domain: data-platform-freshness`) and leaves site/network/SAP/DB/host
  namespaces **untouched**, so the "rule out the network/DB/SAP" RCA step stays
  honest (4-axis disjoint per CLAUDE.md §9.3).
- **Notebook "Data-Platform Cascade RCA"** walks the human investigation
  (symptom → rule-out → upstream root cause → blast radius → recovery → ROI) and
  ends by showing it **matches what Watchdog + Bits AI surface** — the proof, for
  the skeptical towers, that the AI isn't a black box.
- **Demo timing:** start the Simulator ~5–10 min before the AIOps scene so the
  cascade is active or recently active (first fires ~4–6 min in, runs ~10 min,
  repeats ~10–18 min apart). For Watchdog/Bits AI specifically, use a Datadog
  demo org with history.

## Fast-follow

- **ServiceNow / incident-automation** workflow to demo Eduardo's full AIOps loop
  (ticket enrichment → auto-open → auto-close) once verified action IDs are wired
  (see `WORKFLOW_ACTIONS.md`).
