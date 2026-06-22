# Demo Script: "Art of the Possible" — Bunge (v2)

**Duration:** ~45 min + Q&A (trim/extend guide at the end) | **Presenter:** John Rath (Principal Engineer Strategist) + Brian/Steve | **Org:** the `agribusiness` toolkit vertical for the estate/app/SAP scenes; a Datadog demo org with history for the Watchdog/Bits AI scene.

**Room & altitude.** Tiago (VP, economic buyer, reports toward the CFO) needs a **CFO business case**. Eduardo (Sr IT Manager, the champion building global monitoring from scratch) needs to believe Datadog can actually do what he's promising his towers — and he's told us his teams will be *"highly technical, potentially adversarial."* So this is **business-outcome-led, with enough technical proof to arm Eduardo internally**, and a deliberate competitive thread because Dynatrace is in at the same cadence and feeding the same Gartner scorecard.

> Run the live env first: `make ui` → set `DD_DEMO_VERTICAL=agribusiness` → Deploy. That populates the three dashboards (Global KPI, Apps & Data Platform, Infra/Network/SAP) with on-message `agri.*` data so every scene has live panels.

---

## The one-sentence thesis (say it twice — open and close)

> *"Every revenue app you run — Bunge Mobile, BungeServices, myBunge, FRM, BungeAg — sits on one GCP data platform, and today nobody watches that platform end-to-end. We put **one platform and one AI** across all of it, so a stale CME feed or a bad SAP sync is caught **before** a farmer gets a wrong price — and your analysts stop jumping across 28 tools to find out why."*

That is Eduardo's stated goal in our words: *"I don't want to monitor servers — I want to deliver strategic information to the business."*

---

## Narrative arc

Bunge is integrating Viterra and building global observability from **28 fragmented tools → ~5**, with a **$190M synergy target explicitly at risk without IT visibility**. The art of the possible: one platform unifying the estate (servers, **185 sites / 500+ links**, AWS + GCP, SAP, databases) and the revenue apps on top — then **Watchdog + Bits AI** turning that unified data into automated answers, enriched ServiceNow tickets, and prediction. End state: fewer tools, faster MTTR (Shawn's **59-minute rule**), protected revenue + synergies, and observability that feeds the business.

**Pain points (their words):**
1. **"Each region has a different solution… part of our job is to choose the better platform."** — 28 tools, building from scratch, global + regional dashboards. (Eduardo)
2. **"AIOps is the main thing we are bringing for this new year."** — ticket enrichment, auto-open/close ServiceNow incidents, noise reduction, prediction, MTTR. (Eduardo, #1)
3. **"It's really about the applications that are making money."** — the GCP data platform feeding every revenue app; a data issue hits them all at once, unwatched. (Shawn / cross-cutting)
4. **"They don't trust that one tool can oversee everything."** — the towers (Network/Cloud/DevOps) are skeptical; arm Eduardo to win them. (Luciano)

---

## Open — business outcomes (4 min)
**Purpose:** Earn the demo by anchoring to Eduardo's goal and the CFO case, not features.

**Talking points:**
- "Eduardo's framed this clearly — this isn't about monitoring servers, it's about delivering strategic information to the business. And Tiago, the version of this that reaches the CFO is simpler: protect the Viterra synergies and the 10,000 transactions a day, with fewer tools and faster recovery. Did I get that right? What would *you* add?" *(Let Tiago talk — discovery is half the meeting.)*
- "I'm going to show breadth on purpose, because your towers each own a piece and the real prize is connecting them. Stop me anywhere it maps to your scorecard."

---

## Scene 1 — One platform, built from scratch (Infrastructure) (6 min)
**Eduardo's domain:** Infrastructure — global standardization, 28→5, global+regional dashboards, KPIs like *harvest truck counts per factory*.
**Live on:** agribusiness **Global KPI** dashboard + Host Map.

**Navigation:** Infrastructure → **Host Map** (group by `region` / `cloud_provider`); then the **Global KPI** dashboard (revenue-app availability, data-platform freshness, sites online, pricing freshness).

**Talking points:**
- "This is the single pane you're building — 2,000+ servers, VMware on-prem, AWS and GCP, all one data model. The Viterra estate lands *here*, not in a 29th tool."
- "You mentioned global and regional dashboards, and KPIs like harvest truck counts per factory. That's exactly this pattern — one global rollup, drill to region, and business KPIs sit next to infra on the same data. The GCP integration you saw on March 31 that you liked — that ease is the point."
- *(Consolidation case for the CFO):* "Every tile here is a tool you retire. 28 → ~5 is license **and** the people-time of stitching reports by hand."

**Transition:** "You've got 185 sites and 500+ links — let's go where most APM tools can't follow."

---

## Scene 2 — Network across 185 sites (Network) (6 min)
**Eduardo's domain:** Network — 500+ SA links, 185 harvest sites, **SolarWinds replacement**, NDM (Luciano's key vote), *"correlate network devices with syslog + infra metrics"* (landed March 31).
**Live on:** Infra/Network/SAP dashboard (link latency, packet loss, throughput, site connectivity by region) + **Network → Network Map** and **Network Devices (NDM)** in a demo org.

**Talking points:**
- "Your eval is explicitly infrastructure **and** network. Here's site-to-site and site-to-GCP flow, plus device-level NDM with syslog correlated to infra metrics — the exact thing your team called out as compelling."
- "Harvest is the test: when a small silo loses connectivity, grain doesn't move. You'll see *which site, which link, which device* in one view — minutes, not a cross-team bridge call."
- **Competitive wedge (network is a Dynatrace soft spot):** "This is worth pressure-testing across both vendors on your scorecard. Device-level and network-flow monitoring at 185 sites + 500 links — and replacing SolarWinds including IPAM — is a real gap for APM-first platforms. Ask both: *can your AI see the network device layer, or just the app?*"

**Transition:** "Seeing it is step one. Here's the part nobody watches today — the data platform underneath every app."

---

## Scene 3 — The cross-cutting data platform + DBM (Database) (7 min) ★ the reveal
**Eduardo's domain:** Database (SAP/Oracle), Jefferson's *"SELECT \*"* slow-query pain, *"bad query surfaced immediately in context."* **Plus the unique cross-cutting thesis.**
**Live on:** **Apps & Data Platform** dashboard (the dependency view) + the Infra/Network/SAP dashboard's DBM section.

**Navigation:** Apps & Data Platform dashboard → show the Bunge Data Platform health (pricing-feed freshness, SAP sync lag, pipeline errors) and the apps depending on it; then Databases → **DBM** for the "SELECT \*" story on Oracle.

**Talking points:**
- "Here's the linchpin: the GCP data platform — SAP, Salesforce, CME market data — feeds Bunge Mobile, BungeServices, myBunge, FRM, BungeAg. One stale CME feed or a failed pipeline job mis-prices contracts in **all** of them at once. Today there's no commercial observability watching this layer. This is the highest-leverage thing we can give you."
- **DBM / Jefferson's scenario:** "Jefferson mentioned a `SELECT *` quietly degrading things. Watch — that's surfaced immediately *in context*: the slow query, the host, the impacted service. On DBM we get query-level detail and explain plans on Oracle/SQL Server/Postgres."
- **SAP accuracy (be precise — Pedro & Alan will check):** "On SAP we're honest about the seams. HANA health, blocked transactions, replication via the **Redpeaks** collector; the S/4HANA/NetWeaver app layer via **Redpeaks/Agentil**; your Integration Suite/CPI flow via **logs today** plus **SAP Cloud ALM** for message monitoring — that's the duplicate-sales-order class of failure Pedro already caught with MuleSoft. Query-level SQL diagnostics come from DBM on the supported databases, not HANA — we won't pretend otherwise."

**Transition:** "Now the part you said matters most this year — let the AI do the work."

---

## Scene 4 — AIOps: Watchdog + Bits AI (AI) (9 min) ★★ centerpiece & the Dynatrace wedge
**Eduardo's #1:** *"AIOps is the main thing… analyze the ticket, enrich it, open the incident when necessary, close it when necessary… analysts see only real tickets… prediction."* Thiago needs **MTTR** for the CFO case; Shawn's **59-min rule**.
**Live on:** a Datadog demo org with history (Watchdog insights + Bits AI). Pre-stage one data-platform anomaly.

**Navigation & beats:**
1. **Watchdog** (zero-config detection): "Nobody set a threshold for this. Watchdog flagged the data-platform pricing-feed drift on its own — and **correlated** it to FRM pricing latency and Bunge Mobile errors. That cross-domain link — data → app → revenue — is the whole game."
2. **Watchdog RCA / Impact:** show the correlated root cause + blast radius across services/infra.
3. **Bits AI (generative + agentic):** ask in plain language — *"What's impacting grain producers' pricing right now and what changed?"* — Bits returns the root cause, the affected apps, and a suggested action, **citing the signals it used**. "Any analyst — including your regional teams — can ask this in plain language. They don't need to know which of 28 tools to open."
4. **The ServiceNow close-loop (Eduardo's exact ask):** show Workflow Automation enrich and **auto-create a ServiceNow incident** with full context, and auto-resolve when the signal clears — human-in-the-loop where you want control.
5. **Noise reduction:** collapse an alert storm into the single correlated issue. "Analysts see one real incident, not fifty."
6. **Prediction (his word):** **Watchdog Forecasts** — predict a harvest-season capacity/connectivity breach *before* it happens.

**How this puts Dynatrace on the back foot (talk tracks — evidence, not bravado):**
- **Breadth of the data the AI sees.** "Davis is good causal AI — but our AI runs on one dataset that includes **network devices (NDM), logs at scale, DBM, RUM, security, *and* the GCP data platform**. For Bunge specifically, an AI that can't see the 185-site network layer or the data platform can't correlate data→app→revenue. Ask both vendors to do *this* correlation live."
- **Agentic + natural language for distributed teams.** "Bits AI isn't just RCA — it's an AI SRE that investigates autonomously and answers in plain language, which matters when you've got regional teams of mixed seniority. That lowers the skill barrier your towers are worried about."
- **The ServiceNow loop + ease.** "Your AIOps asks — enrich, auto-open, auto-close in ServiceNow, predict — are native here, and Watchdog needs **zero configuration** to start. That speaks to your 'ease of implementation' criterion and Shawn's agent-consolidation constraint: one agent, AI on by default."
- **Explainability (for the AI skeptics — Shawn flagged leadership is cautious on AI):** "This isn't a black box. Watchdog shows the correlated evidence; Bits cites its sources; you keep humans in the loop on actions. That's how you sell AI internally without losing the towers' trust."

**Transition:** "All of that exists to protect the things that make money."

---

## Scene 5 — Protect the revenue apps (APM + RUM + SLOs) (6 min)
**Shawn:** *"the applications that are making money."* myBunge's **10-minute scale-ticket SLA**; FRM pricing; Bunge Mobile cash bids.
**Live on:** Apps & Data Platform dashboard + APM/RUM in a demo org.

**Talking points:**
- "A single transaction end to end — Bunge Mobile cash bid → FRM pricing → the data platform → SAP — with logs right beside it, and the *user's* real experience via RUM. No more 'blame the network' bridge calls; the trace says who's at fault."
- "myBunge commits a 10-minute scale-ticket SLA to producers. Here it is as an **SLO** — a business promise measured automatically. That's the bridge to the CFO: reliability as a number, not a vibe."

**Transition:** "Let me bring it back to the business."

---

## Close — the CFO business case + next steps (4 min)
**Summary (the thesis again):**
> "One platform instead of 28 — consolidation savings for the CFO. One AI — Watchdog + Bits — that cuts MTTR toward Shawn's 59-minute bar and protects the 10,000 transactions a day and the $190M in Viterra synergy that's at risk without this visibility. And it watches the data platform under every revenue app, which nobody does today. That's the art of the possible."

**Next-step ask (Eduardo asked for exactly this):**
> "Three things to make this real for your Gartner scorecard and the CFO: (1) **tower-by-tower technical deep-dives** — bring your skeptics, we'll bring engineers and take the hard questions; (2) a scoped **global POC** on one real slice — the data platform → FRM → Bunge Mobile chain is the highest-impact; (3) **MTTR/ROI case studies and a Datadog-vs-Dynatrace differentiation pack** you can drop straight into your scoring model. Which unblocks you first?"

---

## "Tricky questions" prep (Luciano said to expect them)
- **"One tool can't oversee everything."** → Show the live cross-domain correlation (Scene 4); breadth is the point — NDM + logs + DBM + data platform + apps on one dataset.
- **"How is your AI different from Davis?"** → Don't dismiss Davis. Wedge on: data breadth (network/data-platform/logs), agentic + NL Bits AI, native ServiceNow close-loop, zero-config/ease, explainability. Offer the differentiation pack rather than litigating live.
- **"Leadership is cautious on AI."** (Shawn) → Explainable + human-in-the-loop + cite-sources; start with enrichment/noise-reduction (low risk, fast ROI) before auto-remediation.
- **"We don't want a 4th agent."** (Shawn) → One Datadog Agent; Ansible-deployable (they already use Ansible in NA); consolidate, don't add.
- **"SAP depth?"** (Pedro/Alan) → The honest Marketplace map (Redpeaks/Agentil/RapDev + DBM + logs); don't overclaim HANA query-level.
- **"CMDB / we don't know what we have."** (Alan) → Resource Catalog + tag-based inventory as a *byproduct* of deploying the agent — observability helps the asset-inventory gap.

## Pivot options
- **All-in on business outcomes/AI?** Expand Scene 4 + the data-platform reveal; compress 1–2.
- **Network tower drives?** Deepen Scene 2 (NDM + syslog + SolarWinds/IPAM replacement) for Luciano.
- **Data-science thread (Bart/Hai)?** The data-platform scene extends to data quality feeding GCP ML — "garbage in, garbage out" caught proactively.

## Time-check guide
- **Trim to 30:** Open + Scene 1 + Scene 3 (data platform) + Scene 4 (AIOps) + Close. Network/Apps as one-line mentions.
- **At ~22 min** be entering Scene 4 — protect ≥9 min for it; it's the deal.
- **Extend to 60:** add Scene 5 depth (deployment + error tracking), a live Bits AI Q&A, and a tower-specific drill.
- Reserve the last 5 for the next-step ask.

## Pre-flight checklist
- [ ] `make ui` → `DD_DEMO_VERTICAL=agribusiness` deployed; all three dashboards populated (no "no data")
- [ ] Demo org with Watchdog insights + Bits AI enabled; one data-platform anomaly pre-staged + correlated to FRM/Bunge Mobile
- [ ] A ServiceNow demo target (or recorded fallback) for the auto-incident beat
- [ ] Backup screenshots for every scene (network/AI scenes especially)
- [ ] Brian/Steve briefed on the close + next-step ask
- [ ] One agribusiness / large-merger peer story ready for credibility

---

### Scene → Eduardo's domains (quick map)
| Scene | Eduardo domain / source | Live asset |
|---|---|---|
| 1 One platform | Infrastructure (28→5, global+regional, harvest-truck KPIs) | Global KPI dashboard, Host Map |
| 2 Network/185 sites | Network (500+ links, SolarWinds, NDM — Luciano) | Infra/Network/SAP dashboard, Network Map/NDM |
| 3 Data platform + DBM | Database (SAP/Oracle, Jefferson's SELECT\*) + cross-cutting thesis | Apps & Data Platform dashboard, DBM |
| 4 Watchdog + Bits AI | **AIOps #1** (enrich, ServiceNow, noise, prediction, MTTR) | Demo org (Watchdog/Bits) |
| 5 Revenue apps | "apps that make money" (Shawn); 10-min SLA | Apps & Data Platform, APM/RUM/SLO |
