# Bunge "Art of the Possible" — Handoff (2026-06-22)

Demo prep for the Bunge VP/champion session. Champion **Eduardo Solis** (building
global monitoring from scratch, AIOps = #1); economic buyer **Tiago**; presenter
**John Rath** + Brian/Steve. **Dynatrace** is co-evaluating into a Gartner scorecard;
selection ~Nov 2026.

## What's built (done)

**Toolkit assets — `dd-demo-toolkit/verticals/agribusiness/`** (its own vertical,
env_prefix `agri`; *not* a finance/manufacturing overlay, so no banking/factory
noise on screen):
- **`config.yaml`** — the estate: VMware hosts, **Oracle (DBM)**, 185-site network +
  NDM, AWS/GCP, the **GCP Bunge Data Platform** (the cross-cutting linchpin), the
  **SAP landscape**, and the **FRM pricing** engine. 6 services with a dependency
  graph where **every revenue app → `bunge-data-platform`** (that graph *is* the pitch).
- **3 dashboards** — Global KPI (exec), Apps & Data Platform (cross-cutting),
  Infrastructure/Network/SAP.
- **14 monitors, 5 SLOs** (incl. myBunge 10-min scale-ticket SLA), **6 Service Catalog** entries.
- **Cross-cutting cascade plugin** `plugins/data_platform_cascade.py` — stale CME
  feed → FRM mispricing (4 phases, ~10 min; peaks feed-age ~480s, stale-quote ~12%);
  trips the **CME Feed Staleness** (root) + **FRM Stale-Quote** (symptom) monitors;
  leaves network/SAP/DB/host **untouched** (disjoint, so "rule out the network" is honest).
- **RCA notebook** "Data-Platform Cascade RCA" — the human investigation that **matches
  what Watchdog/Bits AI surface** (the trust-builder for the skeptical towers).
- **SAP modeled to the real Marketplace tiles only:** HANA → **Redpeaks**
  (`redpeaks.hana.*`, no query-level SQL), S/4HANA/NetWeaver → **Redpeaks/Agentil**,
  **SAP Cloud ALM → RapDev** (CPI/integration monitoring), Integration Suite → logs.
  Query-level/"SELECT \*" lives on **Oracle DBM** (DBM ≠ HANA). Left out Sybase/MaxDB/
  BusinessObjects (not in Bunge's stack per briefing).
- **Demo script** `Bunge-art-of-the-possible-demo-script.md` (v2) — scenes mapped to
  Eduardo's domains; AIOps centerpiece; explicit Dynatrace-vs-Watchdog/Bits wedges +
  "tricky questions" prep.

Validates clean: `dd-demo validate --vertical agribusiness` → 0 errors.

## To run the demo (UI-first)
1. `eval "$(op signin)"` — **the op session expired during build; re-auth or `make ui` will stop at the op check.**
2. `make ui` → Configure tab → pick **Global Agribusiness (Bunge)** → Save.
3. **Start the Simulator ~5–10 min before the AIOps scene** (cascade first fires
   ~4–6 min in, runs ~10 min, repeats). Deploy assets from the Deploy tab.
4. **Watchdog/Bits AI panels need a Datadog demo org with history** — the fresh
   simulated env won't have Watchdog insights immediately.

## Remaining / open for the demo
- **App-error counters don't spike in the cascade.** The plugin drives *device*
  metrics (data platform + FRM pricing); the engine emits `agri.app.*` from the
  service sim, which plugins don't move. Demo the **feed-age → stale-quote** charts +
  the **APM dependency map** for "all apps affected," not `agri.app.errors_total`.
  (Making app errors spike = a deeper engine change — scope if wanted.)
- **Not yet built:** the **ServiceNow auto-incident workflow** (Eduardo's enrich →
  open → close loop; needs verified action IDs — see `WORKFLOW_ACTIONS.md`), and the
  **Dynatrace differentiation pack** for the Gartner scorecard (he asked for it).
- **Not yet live-verified** against a real Datadog org (unit-test-only this session).
  John should deploy once and confirm all 3 dashboards populate (no "no data").
- **Nice-to-have polish:** model Eduardo's named KPI **"harvest truck counts per
  factory"** as a metric on the Global dashboard; per-app (myBunge/BungeServices) views.

## Key facts to carry in
- Thesis: one data platform feeds every revenue app; one stale CME feed mis-prices
  all of them at once; nothing watches it today.
- Wedges vs Dynatrace: data breadth (NDM/network at 185 sites, logs, DBM, data
  platform), agentic + NL Bits AI, native ServiceNow loop, zero-config Watchdog,
  explainability. Don't dismiss Davis.
- Business case: 28 tools → ~5; MTTR (Shawn's 59-min rule); protect 10k txns/day +
  the **$190M Viterra synergy at risk without IT visibility**.
