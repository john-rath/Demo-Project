# Version Two — Phased Project Plan

**Initiative:** "Version two" of the dd-demo-toolkit — big UI enhancements alongside an
enhanced **AdventHealth** healthcare overlay.
**Branch:** `version-two` (local + `origin/version-two`).
**Source notes:** David, re: the AdventHealth presentation.
**Authored:** 2026-06-15.

---

## 1. Intent (what David asked for)

1. **AdventHealth healthcare overlay** — a new sub-vertical overlay on top of the existing
   `healthcare` vertical. "Sensing hospital" is AdventHealth's own vernacular and stays.
2. **Reframe to end-user experience** — tell the story from the **patient and care-provider**
   perspective (service quality), *not* the traditional infrastructure-monitoring lens.
3. **Drop staffing** — remove staffing aspects from the AdventHealth story; focus on service
   quality and the areas already discussed with the customer.
4. **Newer capabilities** — add **Bits AI detection** and **EuD (End-User Devices)** monitoring.
   *(David's note said "EUM" but clarified it means EuD / end-user devices.)*
5. **Infra Ops story** — automated repair / self-healing, with support for **on-prem as well as
   cloud** services.
6. **Fleet automation** — expand on the agents Jeff added toward **mock IoT devices**, likely by
   **spinning up containers**.
7. **Full mock environment** — a containerized mock app combined with the existing synthetic OTel
   ingestion, **easy to configure per vertical**. ("A potent combination.")
8. **Easier UI** — a product/feature picker, e.g. a list of **checkboxes** for the Datadog
   products we want to demonstrate.

> **Open question for tomorrow's presentation:** is the deliverable the *plan/roadmap* itself, or a
> working AdventHealth demo slice? This doc assumes the former and scopes the build as a multi-phase
> effort. If a live slice is needed tomorrow, Phase 1 (overlay narrative + a few assets) is the
> minimum viable cut — flag it and we'll fast-track.

---

## 2. Where we are today (current-state findings)

- **Config-driven verticals + overlays.** Five verticals ship (`finance`, `healthcare`,
  `hospitality`, `insurance`, `manufacturing`). `healthcare` already has two overlays as concrete
  models: **`bd`** (Becton Dickinson / Pyxis pharmacy automation) and **`quest`** (Quest
  Diagnostics). AdventHealth becomes a third overlay — additive, no fork.
  - Overlay layout: `verticals/healthcare/overlays/<name>.yaml` (device/service config) +
    `overlays/<name>/{monitors,slos,notebooks,workflows,cases,services}.yaml` + `dashboards/*.json`
    + `plugins/*.py`.
- **Staffing exists in the base.** `verticals/healthcare/config.yaml:625-652` defines a
  `staffing_system` device and `hospital.staffing.*` metrics. Overlays are **additive only**
  (config-merge concatenates; it cannot delete base metrics). So "remove staffing" = the
  AdventHealth overlay tells a staffing-free story (its dashboards/monitors/notebooks simply don't
  surface staffing), and — if we want it gone from emission during AdventHealth runs — we gate base
  staffing behind a flag. **Design decision in Phase 1.**
- **Mock IoT is synthetic today.** Devices are Python metric emitters (gaussian drift +
  plugin-injected anomalies) → OTel → Datadog. There is **no real container per device**. Real
  containers exist only for the **DBM stack** (`authorization-db`, `datadog-agent-pp`,
  `authorization-db-worker`) and the **DSM/data-obs stack** (Kafka + workers + dbt + llm-experiment).
- **Container toggling is profile + env-flag based.** `make up` activates the `dbm` profile when
  `DD_DEMO_DBM=true` (or sub-vertical `payment-processor`) — see `Makefile:33-41`. This is the
  proven pattern for adding a new opt-in **mock-fleet** profile.
- **OTel ingestion** runs through `otel-collector-config.yaml` (OTLP gRPC/HTTP → Datadog exporter).
  Jeff already added a Datadog **Fleet Automation** visibility extension to the collector config.
- **Self-healing is simulated.** Cascade plugins cycle phases via a shared `cascade-state/phase.json`;
  there is no real remediation loop. Workflows (`workflows.yaml`) send notifications, not fixes.
- **On-prem vs cloud has no real split today** — only `DD_SITE` selects the Datadog endpoint.
- **UI = FastAPI + vanilla JS**, served on `127.0.0.1:8765` via `make ui`. Four tabs: **Configure,
  Simulator, Deploy assets, Status**. Config is persisted to `.env` through
  `dd_demo_toolkit_ui/env_manager.py` (`MANAGED_KEYS`); the frontend lives in
  `dd_demo_toolkit_ui/static/{index.html,app.js}`; backend endpoints in `server.py`.

---

## 3. Cross-cutting guardrails (apply to every phase)

These are non-negotiable house rules — each traces to a past demo bug:

- **Read `STYLE_GUIDE.md` before authoring any dashboard/monitor/notebook/SLO/workflow/plugin.**
- **Read `WORKFLOW_ACTIONS.md` before touching any `workflows.yaml`** — unknown `actionId` → 400.
- **No percentile metrics** in this toolkit — prefer counts / rates / gauges. (Percentile
  aggregators only work on histogram metrics and have repeatedly misbehaved here.)
- **Overlay tagging is strict:** stay in the `hospital.*` namespace, reuse existing tag keys
  (`team`, `incident_domain`, `signal_chain`, device dimensions). **No new tag keys** like
  `sub_vertical:`/`customer:`/`overlay:` — identify AdventHealth by `device_manufacturer:` /
  `incident_domain:` values.
- **Plugins must be 4-axis disjoint** (spatial, namespace, incident_domain, temporal) from base +
  other overlay plugins, so Bits AI SRE can isolate one story from another.
- **`make build && make setup`** after editing anything under `verticals/` or `docker/` — assets are
  baked into the image, not volume-mounted.
- **Secrets policy:** `.env` holds `op://` references only; `env_manager.py` rejects plain secrets —
  keep that rejection. Don't add `env_file: .env` to compose services that consume DD keys.
- **Teardown** sweeps base + overlay via `vertical:healthcare` + `dd-demo-toolkit:true` tags — verify
  new assets clean up.

---

## 4. Phased plan

### Phase 0 — Foundations (this session)
**Goal:** branch + plan in place, decisions recorded.
- [x] Create `version-two` branch (local + remote).
- [x] Author this plan.
- [ ] Confirm tomorrow's deliverable scope (plan vs. live slice) with David/John.

### Phase 1 — AdventHealth overlay: the patient/care-provider story
**Goal:** a staffing-free, service-quality narrative that's demo-ready on its own.
**Depends on:** Phase 0.
- New overlay scaffold modeled on `bd`/`quest`:
  `verticals/healthcare/overlays/adventhealth.yaml` + `overlays/adventhealth/`.
- Reframe metrics/assets around **patient & care-provider experience** (e.g. care-team
  notification latency, alert acknowledgement time, patient-engagement/communication signals,
  service-quality SLOs) — counts/rates/gauges only, `hospital.*` namespace.
- **Staffing decision:** AdventHealth assets surface no staffing; decide whether to gate base
  `hospital.staffing.*` emission behind a flag for AdventHealth runs.
- New cascade plugin (`adventhealth_*_cascade.py`) — 4-axis disjoint, patient-experience impact arc.
- Assets: dashboards, monitors, SLOs, notebooks, services, workflows, cases.
- **Deliverable:** `dd-demo setup --vertical healthcare --sub-vertical adventhealth` deploys a clean,
  staffing-free, patient-experience demo.

### Phase 2 — EuD (End-User Devices) + Bits AI detection
**Goal:** layer in the "newer capabilities."
**Depends on:** Phase 1.
- **EuD:** model patient/clinician end-user devices and surface device-experience signals. Note:
  `simulator/rum.py` is currently hospitality-coupled — decide between decoupling it for healthcare
  RUM/EuD or emitting EuD signals via the standard device path.
- **Bits AI detection:** arrange the cascade + monitors so Bits AI SRE can detect and isolate the
  AdventHealth story (the 4-axis disjointness from Phase 1 is the enabler); add anomaly-style
  monitors where appropriate.
- **Deliverable:** EuD experience widgets + a Bits-AI-detectable incident in the AdventHealth demo.

### Phase 3 — Containerized mock environment + fleet automation
**Goal:** the "full mock environment" — real containers for mock IoT/agents, configurable per vertical.
**Depends on:** can start in parallel with Phase 1/2; integrates after.
- New `docker/<mock-fleet>/` image(s) + compose services, gated by a new opt-in **profile + env flag**
  (`DD_DEMO_MOCK_FLEET=true`), mirroring the DBM `--profile dbm` pattern (`Makefile:33-41`).
- Make the mock app **config-driven per vertical** so the same containers re-skin across verticals.
- Wire containerized **Datadog Agents** into the fleet to light up **Fleet Automation** (collector
  extension already present).
- **Deliverable:** `DD_DEMO_MOCK_FLEET=true make up` stands up a real mock fleet alongside the
  synthetic OTel stream.

### Phase 4 — Infra Ops: automated repair (on-prem + cloud)
**Goal:** the self-healing / automated-repair story across both deployment models.
**Depends on:** Phase 3.
- Move from purely simulated phases toward a real(istic) remediation loop driven by workflows
  (respect `WORKFLOW_ACTIONS.md`).
- Introduce an **on-prem vs cloud** modeling axis (deployment dimension / service grouping) so the
  story explicitly spans both. Define the modeling approach (tag value vs. env-driven topology).
- **Deliverable:** a demonstrable "detect → repair" arc that works for an on-prem and a cloud service.

### Phase 5 — UI: product/feature checkbox picker
**Goal:** an easier UI for choosing what to demonstrate.
**Depends on:** mostly independent; can run in parallel from the start.
- Add a product/feature multi-select (checkboxes) to the **Configure** tab
  (`static/index.html` + `static/app.js` `renderProducts()`).
- Persist selections to `.env` via a new `MANAGED_KEYS` entry in `env_manager.py`; new `server.py`
  endpoint(s).
- Drive deploy/asset filtering from the selection so only chosen products' assets deploy.
- **Deliverable:** check products in the UI → only those products' assets deploy.

### Phase 6 — Integration, verification, docs, demo dry-run
**Goal:** ship-quality V2.
**Depends on:** Phases 1-5.
- End-to-end run; STYLE_GUIDE / WORKFLOW_ACTIONS compliance pass; teardown verification.
- Update `README.md`, `dd-demo-toolkit/CLAUDE.md` (new overlay §6 entry, mock-fleet profile, UI),
  `.env.template`.
- Demo rehearsal of the AdventHealth narrative end-to-end.

---

## 5. Sequencing & parallelism

- **Critical path for the demo narrative:** Phase 1 → 2.
- **Infra track (parallelizable):** Phase 3 → 4.
- **UI track (parallelizable):** Phase 5.
- Everything converges in Phase 6.

## 6. Open questions / decisions to confirm
1. Tomorrow's deliverable: roadmap presentation, or a live AdventHealth slice?
2. Staffing: gate base `hospital.staffing.*` emission behind a flag, or just omit from AdventHealth assets?
3. EuD: decouple `simulator/rum.py` for healthcare, or emit EuD via the standard device path?
4. Automated repair: real remediation loop, or richer simulated phases for the demo?
5. On-prem vs cloud: tag-value modeling vs. env-driven topology split?
6. Mock-fleet scope: how many device types / which "potent combination" matters most for AdventHealth?
