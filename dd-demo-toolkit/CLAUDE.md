# CLAUDE.md — dd-demo-toolkit

Project notes, assumptions, and scope boundaries captured by Claude while
working in this repository. Keep this file in sync when the project structure
or conventions change.

---

## 1. Project Scope

`dd-demo-toolkit` is a config-driven Datadog demo framework for Sales
Engineers. Each subdirectory under `verticals/` is a self-contained industry
scenario with:

- `config.yaml` — fleet (devices, metrics, ranges), services, locations
- `services.yaml` — Service Catalog entries
- `monitors.yaml` — alert rules
- `slos.yaml` — service-level objectives
- `dashboards.yaml` + `dashboards/*.json` — YAML-defined dashboards plus
  direct-JSON dashboards
- `notebooks.yaml` — investigation notebooks / RCA narratives
- `workflows.yaml` — self-healing & notification workflows
- `incidents.yaml` — scripted incident entries for the Datadog Incidents app
- `cases.yaml` — case-management entries
- `plugins/*.py` — `IncidentPlugin` subclasses that choreograph simulated
  failures in real time

Verticals are discovered dynamically by the CLI (`dd-demo list`,
`dd-demo setup --vertical <name>`) by scanning `verticals/`. There is **no
central registry** — adding or renaming a directory is sufficient to
register or rename a vertical.

Currently shipped verticals: `finance`, `healthcare`, `hospitality` (formerly
`hilton`), `insurance`, `manufacturing`.

---

## 2. Conventions

### Metric namespace
Metrics are namespaced by vertical prefix (e.g. `healthcare.*`,
`finance.*`, `hospitality.*`). The prefix is declared in
`config.yaml → vertical.env_prefix` and is used literally in all metric
names across YAML and JSON resources. **Renaming a vertical therefore
requires a coordinated rename of every occurrence of the namespace.**

### Tags
Every resource is tagged with:

- `vertical:<name>` — for cleanup / filtering
- `dd-demo-toolkit:true` — identifies toolkit-managed resources

Incident cascades and narrative chains additionally use
`incident_domain:*` and `signal_chain:*` tags.

### Location dimensions
`config.yaml → locations.dimensions` defines the Cartesian product of
location tags attached to every device (e.g. `property_type`, `region`).
Plugin code indexes devices by these dimensions; renaming location values
also requires matching updates in plugins and dashboard queries.

### Plugins
- Must subclass `dd_demo_toolkit.simulator.plugins.IncidentPlugin`.
- Are discovered by file-system scan of `verticals/<name>/plugins/*.py`.
- Mutate `device.state[<metric_name>]` each tick; the engine reads those
  values when publishing to OTel.

---

## 3. Hospitality Vertical — Assumptions & Transformation Notes

The `hospitality` vertical was renamed from `hilton` on 2026-04-16. The goal
was to keep the simulation topology and story intact while removing
Hilton-specific branding, so the vertical is reusable for any
hospitality-industry prospect.

### 3.1 Directory rename
`verticals/hilton/` → `verticals/hospitality/`

### 3.2 Metric namespace rename
`hilton.*` → `hospitality.*`
(applied across config, services, monitors, SLOs, workflows, dashboards,
notebooks, plugins, and core simulator modules that emit these metrics).

### 3.3 Env prefix
`config.yaml → vertical.env_prefix: hilton` → `hospitality`
`config.yaml → vertical.name: hilton` → `hospitality`
`config.yaml → vertical.display_name: "Hilton Smart Hotel Demo"` →
`"Smart Hospitality Demo"`

### 3.4 Brand / property-type genericization
The `property_type` location dimension previously carried Hilton brand
names. It now uses tier-based generics, preserving the luxury → select
tiering that drives some dashboard filters:

| Before (Hilton brand) | After (generic tier) |
|-----------------------|----------------------|
| Waldorf Astoria       | Luxury Collection    |
| Conrad                | Premium Resort       |
| Hilton Hotels         | Full Service         |
| DoubleTree            | Upscale Select       |
| Hampton               | Select Service       |
| Hilton Garden Inn     | Extended Stay        |

Snake-case forms used in workflow / notebook / dashboard queries follow:

| Before                | After                |
|-----------------------|----------------------|
| `waldorf_astoria`     | `luxury_collection`  |
| `conrad`              | `premium_resort`     |
| `hilton_hotels`       | `full_service`       |
| `doubletree`          | `upscale_select`     |
| `hampton`             | `select_service`     |
| `hilton_garden_inn`   | `extended_stay`      |

The primary incident target (`Hilton Garden Inn APAC`) is now
`Extended Stay APAC`. The secondary incident target
(`Waldorf Astoria APAC`) is now `Luxury Collection APAC`.

### 3.5 Service / brand-term genericization

| Before                       | After                          |
|------------------------------|--------------------------------|
| `hilton-com` (service)       | `reservations-portal`          |
| "Hilton.com" (display)       | "Reservations Portal"          |
| "Hilton Honors" / "Honors"   | "Guest Loyalty Program" / "Loyalty" |
| `hilton-honors` (id form)    | `guest-loyalty`                |
| `hilton-ai-stay-planner`     | `ai-stay-planner`              |
| `demo.display_name: "Hilton"`| `"Hospitality"`                |
| `hilton.service-now.com`     | `hospitality.service-now.com`  |
| `edge-mgmt.hilton.com`       | `edge-mgmt.hospitality.demo`   |
| `payments.hilton.com`        | `payments.hospitality.demo`    |

All other service names already used generic terms and were unchanged:
`property-engagement-platform`, `connected-room-service`,
`revenue-management-engine`, `servicenow-integration`,
`loyalty-rewards-api`, `guest-wifi-portal`.

### 3.6 Core simulator modules touched

Two modules in `dd_demo_toolkit/simulator/` contained hardcoded Hilton
branding and, although loaded unconditionally by the engine for every
vertical, realistically only make sense in the hospitality demo:

- `simulator/rum.py` — RUM (Real User Monitoring) submitter. Emits
  `hospitality.rum.*` metrics (formerly `hilton.rum.*`), view titles,
  property types, and loyalty tiers. Page titles / flow narratives were
  made brand-neutral.
- `simulator/llm_obs.py` — LLM Observability submitter for the AI stay
  planner. Prompt templates, RAG docs, and span attributes were rewritten
  to drop Hilton-specific property names and phrasing. `ml_app` renamed
  to `ai-stay-planner`.
- `utils/otel.py` — lone docstring comment updated.

### 3.7 Things intentionally NOT changed

- The overall incident topology, phase timing, and narrative (WiFi client
  overload → IoT gateway cascade → guest-experience impact → self-healing
  via Meraki API). Only the location labels and branding changed.
- Other verticals (`finance`, `healthcare`, `insurance`,
  `manufacturing`) — out of scope.
- Dashboard widget layouts, chart types, and formula structures.
- SLO targets, monitor thresholds, and time windows.
- Plugin phase durations and metric drift behaviour.
- The fact that `rum.py` and `llm_obs.py` are loaded globally by the
  engine; they still emit `hospitality.*` metrics for every vertical.
  Fixing that coupling is a broader refactor and was left out.

---

## 4. Cleanup / teardown fix (2026-04-16)

Demo users reported that "many monitors and notebooks survive each run".
Root cause lived in `dd_demo_toolkit/utils/dd_api.py`:

- `list_monitors()` and `list_notebooks()` (and `list_dashboards()`) made a
  single `GET` request and returned only the first page of the Datadog
  API response. The teardown managers filter the returned list client-
  side by the `vertical:<name>` tag, so any resource past page 1 was
  invisible to teardown and survived indefinitely. Every subsequent
  deploy created a new batch, compounding the problem.
- `list_monitors(tag=...)` also incorrectly sent the tag as the `name`
  query parameter (which filters by monitor name, not by tag). The
  correct parameter on `/api/v1/monitor` is `monitor_tags`.

Fix:

- `list_monitors()` now pages via `page` + `page_size=1000` (the max),
  accepts both list-shaped and dict-shaped API responses, and returns
  `{"monitors": [<all pages>]}`.
- `list_notebooks()` now pages via `start` + `count=100` (the max) and
  returns `{"data": [<all pages>], ...}`.
- `list_dashboards()` now pages via `start` + `count=100` and returns
  `{"dashboards": [<all pages>], ...}`.
- `list_monitors(tag=...)` now sends `monitor_tags` (the correct
  parameter). The call-site in `MonitorManager.teardown` doesn't
  currently use `tag`, but the latent bug is now fixed.

All three loops terminate when they receive a short (< page_size) page,
with a defensive extra empty request when the total is an exact
multiple of the page size.

Scope-limits on this fix: `list_workflows`, `list_incidents`, and
`list_cases` also lack pagination but use *server-side* tag filters and
(per the demo scenarios) stay well below the default page. They were
left alone to keep this change focused on the reported symptom. Same
for `list_slos` (default `limit=1000`).

Verification: `/sessions/intelligent-nifty-babbage/verify_pagination.py`
exercises all three endpoints plus `MonitorManager.teardown` and
`NotebookManager.teardown` against fake paged responses — confirming
that 2,500 monitors and 350 notebooks are all deleted (not just the
first page).

---

## 5. Working-on-this-project tips

- After any vertical rename, run a case-insensitive grep for the old name
  across the whole repo — dashboards JSON, YAML, Python plugins, core
  simulator code, and README table rows all need to agree.
- `dd-demo list` is the fastest way to confirm a vertical is discovered
  and parseable. `dd-demo setup --vertical <name> --dry-run` validates
  that all resource YAML/JSON files parse end-to-end.
- The `env_prefix` in `config.yaml` must match the literal metric
  namespace used everywhere else (it is *not* templated at deploy time —
  the prefix was historically inlined into every query string).
