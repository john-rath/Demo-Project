# dd-demo-toolkit — Asset Style Guide & Best Practices

This document captures the conventions, gotchas, and Datadog API quirks
that future contributors (human or AI) **must** follow when authoring
dashboards, monitors, notebooks, SLOs, workflows, plugins, services, and
overlays for this toolkit.

Every rule below traces to a real bug we shipped and had to fix in
production demos. Read this BEFORE creating new assets.

---

## 1. Datadog query language pitfalls

These are the highest-bug-density rules. Get these wrong and your
widget shows "No data" or your monitor refuses to deploy.

### 1.1 Percentile aggregators (`p50:`, `p95:`, `p99:`) require BOTH a distribution metric AND percentile aggregations enabled in Datadog

This is a two-step gotcha that has wasted hours of debugging. Both
conditions must be true for `p95:` / `p99:` to return data:

**Step 1 — the metric must be a distribution.** Gauges and counters
never support percentiles. To make a metric a distribution:
1. Declare it as `type: histogram` in the vertical YAML so the engine
   creates an OTel histogram instrument and calls
   `instrument.record(value)`.
2. Confirm `otel-collector-config.yaml → exporters.datadog.metrics.histograms.mode = distributions`
   so the collector translates OTel histograms to Datadog's
   distribution metric type. (Already configured in this repo.)

**Step 2 — percentile aggregations must be enabled per-metric in
Datadog.** Even after Step 1, `p95:` / `p99:` queries return *no data*
until you enable percentile aggregations on the metric:
- UI: Metrics → Summary → click the metric → Edit → check
  "Include Percentile Aggregations" → Save.
- API: `POST /api/v2/metrics/<metric_name>/all-tags` won't do it; use
  `PUT /api/v1/metric/{name}` with `{"type": "distribution",
  "include_percentiles": true}` (or the equivalent
  `metricsApi.update_metric_metadata` SDK call).

| Metric type | Aggregators that work out of the box | Requires Datadog UI/API config? |
|-------------|--------------------------------------|----------------------------------|
| `gauge`     | `avg:`, `max:`, `min:`, `sum:`, `last:` | No |
| `counter`   | `sum:`, `count:` (with `.as_count()`) | No |
| `histogram` (→ DD distribution) | `count:`, `sum:`, `min:`, `max:`, `avg:` | No |
| `histogram` (→ DD distribution) | `p50:`, `p75:`, `p90:`, `p95:`, `p99:` | **Yes — must enable percentiles** |

**Default rule for sales-engineering demos: do NOT use percentile
aggregators in shipped dashboards / notebooks.** Use `avg:` and `max:`
on gauges and distributions alike. Reasons:
- Works in any Datadog org without per-metric configuration
- No "No data" surprise during a live demo
- `max:` is the more honest "worst-cabinet" view; `avg:` is the typical
- Adding a second formula (`max`) on the same chart gives the visual
  shape of a percentile band without the configuration tax

✅ For KPI scalars: `max:hospital.pyxis.dispense_latency_ms{...}`
✅ For timeseries: two formulas on one widget — `avg:` and `max:` —
   gives a band view and renders without configuration:
```json
"requests": [{
  "formulas": [{"formula": "avg"}, {"formula": "max", "alias": "max"}],
  "queries": [
    {"name": "avg", "query": "avg:hospital.pyxis.dispense_latency_ms{...} by {department}"},
    {"name": "max", "query": "max:hospital.pyxis.dispense_latency_ms{...} by {department}"}
  ],
  "response_format": "timeseries",
  "display_type": "line"
}]
```

❌ `p95:hospital.pyxis.dispense_latency_ms{...}` (gauge)
❌ `p99:hospital.app.latency_ms{...}` — even though `app.latency_ms`
   is a histogram, percentile queries silently return no data unless
   percentiles are enabled in Datadog UI/API.

**Real percentile queries (`p99:`, etc.) ARE supported** — but only
when both Step 1 and Step 2 are satisfied. If a customer-specific
demo needs them:
1. Confirm metric type is `histogram` in YAML.
2. After `dd-demo setup`, enable percentile aggregations in the
   Datadog Metrics Summary for each affected metric, OR script it via
   `PUT /api/v1/metric/{name}`.
3. Test the query in Metrics Explorer before relying on it in a
   dashboard.

### 1.2 `by {dim}` must come BEFORE `.as_count()`

This is non-obvious and the parser error message is unhelpful.

✅ `sum:hospital.pyxis.drawer_open_events_total{tags} by {device_id}.as_count()`
❌ `sum:hospital.pyxis.drawer_open_events_total{tags}.as_count() by {device_id}`

The wrong form produces:
```
Rule 'query_expr' matched in its entirety, but it didn't consume all
the text. The non-matching portion of the text begins with 'by {…}'
```

### 1.3 Monitor query alerts do not support logical operators (`||`, `&&`)

A single `query alert` monitor is one threshold. To express "out of
range" or compound conditions, use **two separate monitors** or a
**composite monitor** (`type: "composite"`).

✅ Two monitors:
```yaml
- name: "BACTEC Incubator Too Low"
  query: "avg(last_5m):avg:hospital.bactec.incubator_temp_celsius{...} by {device_id} < 34.8"
- name: "BACTEC Incubator Too High"
  query: "avg(last_5m):avg:hospital.bactec.incubator_temp_celsius{...} by {device_id} > 36.2"
```

❌ `query: "...< 34.8 || ...> 36.2"` — Datadog returns
`{"errors":["The value provided for parameter 'query' is invalid"]}`.

### 1.4 Scalar (`query_value`) widgets need an aggregator if the query has multiple time-bucket samples

Scalar widgets reduce a time series to one number. If you don't tell
Datadog *how* to reduce, results may be empty or surprising.

✅ With explicit aggregator:
```json
{"query": "sum:hospital.pyxis.dispense_events_total{...}.as_count()", "aggregator": "sum"}
{"query": "max:hospital.pyxis.dispense_latency_ms{...}", "aggregator": "max"}
{"query": "sum:hospital.device.online{...}", "aggregator": "last"}
```

`avg:` queries on gauges sometimes resolve without an explicit
aggregator, but always passing one is more reliable.

### 1.5 Notebook timeseries cells require `formulas:` on every request

Without `formulas`, the chart has no curve to render — even though the
query is valid. This was the cause of every "empty notebook" we shipped.

✅ Always:
```yaml
- attributes:
    definition:
      type: "timeseries"
      requests:
        - response_format: "timeseries"
          queries:
            - data_source: "metrics"
              name: "lag"
              query: "avg:hospital.pyxis.sync_lag_to_inventory_ms{...} by {device_id}"
          formulas:                       # ← do not omit
            - formula: "lag"
              alias: "Sync lag (ms)"
          display_type: "line"
  type: "notebook_cells"
```

For ratios/computed values, formulas reference multiple named queries:
```yaml
queries:
  - {name: "errs", query: "sum:hospital.app.errors_total{...}.as_count()"}
  - {name: "reqs", query: "sum:hospital.app.requests_total{...}.as_count()"}
formulas:
  - formula: "errs / reqs * 100"
    alias: "error %"
```

### 1.6 Datadog tags are case-normalized (lowercased) on storage

`device_manufacturer:BD` in the engine is stored and queried as
`device_manufacturer:bd` by Datadog. Most queries handle this
transparently, but **dashboard template variable defaults with
mixed-case literal values may not interpolate reliably**. If you need
to default-filter by manufacturer, prefer:

- Default `*` and let the user select from the dropdown, OR
- Hardcode the lowercase tag value directly into the query
  (`device_manufacturer:bd`) and skip the template variable.

When in doubt, omit the redundant template variable: filtering by
`device_type:pyxis_medstation` already implies `device_manufacturer:BD`
since Pyxis MedStation is BD-only.

### 1.7 Workflow descriptions have a 300-character limit

Datadog Workflow Automation API enforces this. Concise descriptions
(1–2 sentences) are required.

✅ "Self-heal for the BD Pyxis polling storm: clamps inventory poll rate
via pyxis-inventory-api admin, waits for sync-lag drain, verifies
dispense latency recovery, opens BD ticket."

❌ Multi-paragraph description with full incident narrative.

Put the narrative in the parent monitor's `message` field or a linked
notebook instead — those have generous limits.

### 1.8 Cases API status changes use `/status`, not `/close`

The Cases v2 API has several non-obvious quirks:

1. **`POST /api/v2/cases/{id}/close` does not exist.** Sending a request
   to that URL returns 404. Status changes go through the status endpoint:
   ```
   POST /api/v2/cases/{id}/status
   Body: {"data": {"type": "case", "attributes": {"status": "CLOSED"}}}
   ```
   Valid status values: `"OPEN"`, `"IN_PROGRESS"`, `"CLOSED"`.

2. **`PATCH /api/v2/cases/{id}` does not accept `status`.** PATCH only
   accepts `title`, `description`, and `priority`. Status transitions
   MUST go through `POST /api/v2/cases/{id}/status`.

3. **Archive body uses `"type": "case"` (SINGULAR)**, not `"cases"`:
   ```
   POST /api/v2/cases/{id}/archive
   Body: {"data": {"type": "case"}}
   ```
   The `type` field is inconsistently named across the API — the close
   endpoint uses `"case"` (singular) in attributes but the create
   endpoint also uses singular `"case"`. When in doubt, use singular.

4. **`list_cases` must be paginated.** The Cases API uses 1-indexed
   pages (`page[number]` starts at 1, not 0). Without pagination,
   teardown only sees the first page and misses older cases.

---

### 1.9 Every dashboard widget must show live data after `make up`

Dashboards with empty charts ruin live demos. Every metric query in every
dashboard must use a metric the simulator actually emits.

**Rule**: all metric names in a dashboard must start with the parent
vertical's `env_prefix` (from `verticals/<v>/config.yaml`). Overlay
dashboards inherit the parent vertical's prefix.

| Vertical | `env_prefix` | Dashboard metrics must start with |
|----------|-------------|-----------------------------------|
| finance | finserv | `finserv.` |
| healthcare | hospital | `hospital.` |
| hospitality | hospitality | `hospitality.` |
| insurance | insurer | `insurer.` |
| manufacturing | mfg | `mfg.` |

**Common violations to avoid**:

1. **`otelcol.*` metrics are valid — but only because the OTel Collector is
   in the stack.** The `dd-demo-otel-collector` container runs in every
   `make up` session and is configured in `otel-collector-config.yaml` to
   export its own self-telemetry (spans exported, refused spans, CPU, memory)
   via a `prometheus` receiver → Datadog pipeline. `otelcol.*` is the only
   approved platform-prefix exception to the env_prefix rule. Do not add
   other non-env_prefix namespaces without a corresponding service in
   docker-compose.

   **`otelcol.*` metric names do NOT carry a `_total` suffix** (as of
   collector v0.87+). The correct names are:

   | Metric | NOT |
   |--------|-----|
   | `otelcol_exporter_sent_spans` | ~~`otelcol_exporter_sent_spans_total`~~ |
   | `otelcol_exporter_sent_metric_points` | ~~`otelcol_exporter_sent_metric_points_total`~~ |
   | `otelcol_receiver_accepted_spans` | ~~`otelcol_receiver_accepted_spans_total`~~ |
   | `otelcol_receiver_refused_spans` | ~~`otelcol_receiver_refused_spans_total`~~ |
   | `otelcol_process_cpu_seconds` | ~~`otelcol_process_cpu_seconds_total`~~ |
   | `otelcol_processor_batch_batch_size_trigger_send` | ~~`otelcol_processor_batch_batch_size_trigger_send_total`~~ |

   Verify the exact name anytime the collector image is upgraded:
   `docker exec dd-demo-otel-collector curl localhost:8888/metrics` (or
   use a sidecar: `docker run --rm --network container:dd-demo-otel-collector alpine sh -c "apk add -q curl && curl -s http://localhost:8888/metrics"`)

2. **`system.*` / `docker.*` / `kubernetes.*`** — require a Datadog Agent
   sidecar; not available in the demo simulator.

3. **`{env_prefix}.app.*` for device-category widgets** — the `.app.*`
   namespace is emitted by named services (e.g. `service_name:mobile-banking-api`),
   not by device simulators (e.g. `device_type:authorization_switch`). Match
   the metric namespace to the device type.

**Automated enforcement**:
- `make test` → `tests/test_dashboard_query_coverage.py` — static, no
  credentials required; fails on any metric that doesn't match env_prefix.
- `make validate` → `tests/test_dashboard_live_data.py` — live Datadog
  API check; fails if any metric has zero data points in the past hour.
  Run this after `make up` to confirm the simulator is emitting every
  metric referenced in every dashboard.

---

## 2. Tag standards (strict)

### 2.1 Auto-injected — never add to YAML manually
- `vertical:<vertical-name>`
- `dd-demo-toolkit:true`

### 2.2 Reusable keys (use existing values; new VALUES are fine, new KEYS are not)
- `team:<role>` — `biomed`, `pharmacy-systems`, `digital-health`, `integration`, `operations`, `facilities`, `clinical-systems`
- `incident_domain:<value>` — e.g. `network-to-device`, `pharmacy-automation`. New value? Fine. New *key*? Don't.
- `signal_chain:<position-name>` — `1-root-cause`, `2-leading-indicator`, `3-symptom`, `4-cascade`, `5-recovery`
- `safety:<level>` — `patient-safety`, `life-safety`
- `compliance:<framework>` — `hipaa`, `dea`, `fda-510k`, `dscsa`
- `audience:<role>` — `executive`, `nursing`, `pharmacy`
- `workflow:<purpose>` — `alert-management`, `auto-remediation`, `compliance-audit`
- `workflow-type:<kind>` — `auto-remediation`, `escalation`, `notification`

### 2.3 Engine-emitted query dimensions (use freely in queries)
`device_id`, `device_type`, `device_manufacturer`, `device_model`,
`device_firmware`, `device_category` (often as `category`),
`battery_powered`, `floor`, `wing`, `department`, `service_name`.

### 2.4 ❌ Do NOT invent these keys
- `sub_vertical:` — overlays are identified by `device_manufacturer:` and/or `incident_domain:` values, not by a new key
- `customer:` — same reason
- `overlay:` — same reason
- `env:` — already handled by Datadog's standard `env` tag
- `severity:` — Datadog has a built-in `priority` field on monitors

### 2.5 Discoverability rule
If you can't filter for a resource using only the keys above, redesign.
Don't add a new key to make filtering easier.

### 2.6 Teardown identification per resource type

Not every Datadog API supports arbitrary tag keys. The table below
documents how each resource manager identifies toolkit-managed resources
at teardown time. **If you add a new resource type, follow the pattern
for its API or pick the closest equivalent.**

| Resource | Identified by | Reason for approach |
|---|---|---|
| Dashboards | `[dd-demo-toolkit:{vertical}]` marker in description | List API does not return tags |
| Monitors | `vertical:{v}` + `dd-demo-toolkit:true` tags | Full tag support |
| Notebooks | Name match against `notebooks.yaml` | API enforces a platform-wide tag-key allowlist; `vertical` and `dd-demo-toolkit` are not on it — injecting them returns 400 |
| SLOs | `vertical:{v}` + `dd-demo-toolkit:true` tags | Full tag support |
| Workflows | `vertical:{v}` + `dd-demo-toolkit:true` tags (server-side filter) | Full tag support |
| Incidents | `vertical:{v}` + `dd-demo-toolkit:true` tags (server-side filter) | Full tag support |
| Cases | Title match against `cases.yaml` | List response does not expose tags; Case Management Projects also cannot be linked to a Datadog Team via any public API endpoint — `PATCH /api/v2/cases/projects/{id}` silently ignores `relationships.team` and `POST` returns 400. **Team ownership must be set manually** in Datadog → Case Management → Settings → \<project\> → Team ownership. |
| Services | N/A — deregistration not supported by the API | Datadog Service Catalog has no delete/deregister endpoint |
| SDS Groups | `[dd-demo-toolkit:vertical:{v}]` marker appended to description | SDS group GET response does not include tags; the manager appends the marker at deploy time so `sds.yaml` descriptions stay clean |
| SDS Rules | `vertical:{v}` + `dd-demo-toolkit:true` tags | Full tag support; tags appear in rule GET response |

For name/title-based resources (notebooks, cases): the manager loads the
vertical's YAML at teardown time and deletes any API object whose
name/title exactly matches a configured entry. This means **renaming a
resource in YAML without a corresponding teardown first will orphan the
old copy** — always teardown before renaming.

---

## 3. Metric naming convention

```
{env_prefix}.<domain>.<metric_name>
```

- `env_prefix` is declared in `verticals/<v>/config.yaml → vertical.env_prefix` (e.g. `hospital`, `finance`, `factory`).
- `domain` groups metrics by area: `device.*` (cross-device-type health), `network.*` (network infra), `app.*` (application/service metrics), plus device-specific domains like `pump.*`, `pyxis.*`, `bactec.*`, `bed.*`.
- Counter metrics MUST end in `_total` (Prometheus convention; the engine treats them appropriately).
- Gauge metrics use suffixed units: `_pct`, `_ms`, `_sec`, `_celsius`, `_dbm`, `_count`, `_pct`, `_ratio`.

✅ `hospital.pyxis.sync_lag_to_inventory_ms` (gauge, ms)
✅ `hospital.pyxis.dispense_events_total` (counter)
❌ `hospital.dispense_latency` (no domain, no unit)
❌ `pyxis_dispense_count` (no prefix, no `_total`)

When adding metrics to an overlay, **reuse the base vertical's prefix**.
Overlays don't get their own metric namespace — they ride the base one.

---

## 4. Dashboard authoring conventions

### 4.1 Layout pattern
1. **Header note** — full width, vivid_blue, 1–2 lines describing scope
2. **KPI strip** — 6 `query_value` widgets, 2 wide × 2 tall, total width 12
3. **Root-cause lane** — colored note divider (yellow), 2 wide timeseries
4. **Leading-indicator / Symptom lane** — divider (vivid_orange), 2 wide timeseries
5. **Mechanics / Compliance** — neutral divider (white), 3-up smaller widgets
6. **Cascade trace** — divider (vivid_purple), service-level latency/error widgets
7. **Health (CPU/Memory/Disk/Battery)** — 4 small timeseries side-by-side
8. **Sub-fleet** — additional manufacturer/device coverage if applicable

### 4.2 Widget patterns

**KPI scalar (`query_value`):**
```json
{
  "title": "<descriptive>",
  "type": "query_value",
  "requests": [{
    "formulas": [{"formula": "query1"}],
    "queries": [{
      "data_source": "metrics",
      "name": "query1",
      "query": "<aggregator>:<metric>{<filters>}",
      "aggregator": "<sum|avg|max|min|last>"
    }],
    "response_format": "scalar",
    "conditional_formats": [
      {"comparator": ">", "value": <crit>, "palette": "white_on_red"},
      {"comparator": ">", "value": <warn>, "palette": "white_on_yellow"},
      {"comparator": "<=", "value": <warn>, "palette": "white_on_green"}
    ]
  }],
  "autoscale": true,
  "precision": 0
}
```

**Timeseries:**
```json
{
  "title": "<metric description with unit>",
  "type": "timeseries",
  "requests": [{
    "formulas": [{"formula": "query1"}],
    "queries": [{
      "data_source": "metrics",
      "name": "query1",
      "query": "<aggregator>:<metric>{<filters>} by {<dim>}"
    }],
    "response_format": "timeseries",
    "display_type": "line"   // or "bars" for counts, "area" for cumulative
  }],
  "markers": [
    {"value": "y = <threshold>", "display_type": "error dashed", "label": "Critical"}
  ]
}
```

### 4.2b Dashboard widget — queries/formulas format requires `response_format`

The Datadog dashboard API validates timeseries requests against `anyOf` schemas:
- **Legacy format**: `{"q": "<metric>", "display_type": "line"}`
- **New format**: `{"queries": [...], "formulas": [...], "response_format": "timeseries"}`

A request that has `queries` but is **missing `response_format`** matches neither schema
and is rejected with `is not valid under any of the given schemas`. This is a silent
schema mismatch — the error message doesn't say "missing response_format".

✅ Minimal single-query timeseries request:
```json
{
  "queries": [{"data_source": "metrics", "name": "query1", "query": "avg:metric{tag}"}],
  "response_format": "timeseries",
  "display_type": "line"
}
```

✅ With explicit formula (needed for multi-query or aliasing):
```json
{
  "queries": [{"data_source": "metrics", "name": "query1", "query": "avg:metric{tag}"}],
  "formulas": [{"formula": "query1", "display_type": "line"}],
  "response_format": "timeseries"
}
```

❌ Missing `response_format` — rejected by API despite having `queries` and `display_type`:
```json
{
  "queries": [...],
  "display_type": "line",
  "on_right_yaxis": false
}
```

**Also**: `on_right_yaxis` at the request root is a legacy field; omit it in new-format requests
(its default is `false`, so removing it has no effect).

### 4.2c `query_value` widgets do not support `suffix`

The `suffix` field is **not** in the Datadog `query_value` widget schema and causes a 400.
Use `custom_unit` (for a unit label after the number) or `unit` (auto) instead, or omit it.

❌ `"suffix": "%"` — API returns 400 Invalid widget definition.
✅ `"custom_unit": "%"` — or omit if the metric name implies the unit.

### 4.3 Template variables
Always include at least:
```json
[
  {"name": "floor", "prefix": "floor", "default": "*"},
  {"name": "wing", "prefix": "wing", "default": "*"},
  {"name": "department", "prefix": "department", "default": "*"}
]
```
Do not add `manufacturer` or `device_type` template vars unless the
dashboard genuinely needs to be retargeted across them. Hardcoded
filters in the queries are clearer for purpose-built dashboards.

### 4.4 Layout type
Always `"layout_type": "ordered"` and `"reflow_type": "fixed"`. The
engine teardown matches dashboards by description marker
(`[dd-demo-toolkit:<vertical>]`) — **don't remove or modify the
description** beyond appending narrative.

---

## 5. Monitor authoring conventions

### 5.1 Naming
- Format: `[Vertical] Domain Name` or `[Vertical/Sub] Domain Name`
  - `[Healthcare] WiFi Channel Utilization Critical`
  - `[Healthcare/BD] Pyxis Inventory Poll-Rate Storm`

### 5.2 Required tags (in YAML — `vertical:` and `dd-demo-toolkit:` are auto-added)
- `team:<role>` (existing values only)
- `incident_domain:<value>` if part of a cascade narrative
- `signal_chain:<position-name>` if part of a cascade narrative
- `safety:<level>` if patient/life-safety relevant
- `compliance:<framework>` if regulatory

### 5.3 Priority
- **1** — safety, SLO breach, life-safety, immediate action required
- **2** — warning, operational impact, action within hours
- **3** — informational, trending issue
- **4–5** — diagnostic, baseline drift

### 5.4 Message guidelines
- Lead with an emoji for visual scan: 🚨 critical, ⚠️ warning, 🔋 battery, 📡 network, 🌡️ temp, 💊 pharmacy, 🤖 robotics, 🔥 service error, 🐢 latency, ⏱ stale, 👥 multi-user workflow, 📈 throughput
- One sentence: WHAT happened, WHERE (use template vars like `{{floor.name}}`).
- Optional second clause: pointer to upstream/downstream signal to investigate.

✅ "🚨 Pyxis dispense p95 latency >2.5s on Floor {{floor.name}} {{wing.name}} {{department.name}}. Nurses experiencing delays at the cabinet. Check upstream sync lag and pharmacy-ehr-bridge."

### 5.5 Query construction
- Use `avg(last_5m):` or `sum(last_5m):` time aggregator — match window to phenomenon (5m for transients, 15m for trends).
- `by {dim}` clause for per-entity alerting (one alert per cabinet vs. fleet-wide).
- Single-direction threshold only. For range checks, two monitors.
- For ratio thresholds: `sum:errors / sum:requests * 100 > 5` — always include `* 100` if expressing as %.

---

## 6. SLO conventions

```yaml
- name: "<Service or Workflow> Availability"
  description: "<one sentence: what does this protect, why does it matter>"
  type: metric
  target: <99.9 | 99.5 | 95.0>
  timeframe: "30d"   # 7d, 30d, 90d
  query:
    numerator: "sum:<good_events>.as_count()"
    denominator: "sum:<all_events>.as_count()"
  tags:
    - "team:<role>"
    - "incident_domain:<value>"  # if part of a cascade
```

- Targets: `99.9` for tier-1 services, `99.5` for fleet uptime, `95.0` for SLA-style workflows.
- Numerator/denominator both as counts (`.as_count()`).
- Don't create SLOs without an aligned monitor — SLOs are for tracking; monitors are for alerting.

---

## 7. Workflow conventions

> **Before authoring or editing any workflow YAML, read
> [WORKFLOW_ACTIONS.md](WORKFLOW_ACTIONS.md).** It captures the canonical
> Datadog Workflow Automation payload shape, the verified-action-ID
> catalog, and the discovery procedure for unknown actions. Every rule
> there traces to a real 400 / silent-no-op bug. The summary in this
> section is a quick reference, not a substitute.

### 7.1 Structure
```yaml
- name: "[Vertical] <Action> <Object>"
  description: "<≤300 chars>"
  trigger:
    type: monitor
    monitor_tags:
      - "incident_domain:<value>"
      - "signal_chain:1-root-cause"
  steps:
    - name: gather_context        # always first
      type: datadog_query
    - name: <action>              # the actual remediation
      type: http_request
    - name: wait_for_<state>      # let the system settle
      type: sleep
    - name: verify_<state>        # confirm the action worked
      type: datadog_query
    - name: notify_<channel>      # always last
      type: slack_message
  tags:
    - "team:<role>"
    - "incident_domain:<value>"
    - "workflow-type:auto-remediation"
```

### 7.2 Step types in use
- `datadog_query` — read metrics
- `http_request` — call external API (with `{{vault.<token>}}` for secrets)
- `slack_message` — notify
- `datadog_incident` — declare incident
- `sleep` — wait
- `condition` — branch on values
- `datadog_case` — create/update case

### 7.3 Trigger by monitor tags, not monitor names
Names change. Tags are stable. Always trigger via `monitor_tags`.

### 7.4 Action IDs
Every YAML `type:` must resolve to a Datadog-registered `actionId` or
Datadog rejects the entire workflow create (`400 spec is invalid`).
The toolkit's `_TYPE_TO_ACTION_ID` map (in
`dd_demo_toolkit/resources/workflows.py`) only contains verified IDs;
unknown types fall through to no-op so deploys never break.

To add a new action type: see "How to discover a new action ID" in
[WORKFLOW_ACTIONS.md](WORKFLOW_ACTIONS.md) — fastest path is the
Datadog UI "Edit JSON Spec" trick (~30s).

### 7.5 Step connections use `outboundEdges` objects
Not `outEdges`, not a list of strings. The manager builds these
automatically (sequential wiring); override per-step with `out_edges:`
in YAML for non-linear flows. Details in WORKFLOW_ACTIONS.md.

---

## 8. Notebook conventions

### 8.1 Structure
1. Markdown header — symptom report, diagnostic gap statement
2. Step 1: Confirm symptom (timeseries with markers)
3. Step 2: Rule out the obvious-but-wrong lane (cabinet/device-level health)
4. Step 3: Walk upstream (root-cause-adjacent metrics with markers showing thresholds)
5. Step 4: Confirm upstream service is choking (error rate, latency)
6. Step 5: Disambiguation table — show why this story is NOT another cascade
7. Step 6: Remediation timeline (poll rate / sync lag / latency on one chart)
8. **ROI / Business Impact** section (see §8.4)
9. Follow-ups list

### 8.2 Time range
`time_range: "1h"` for live demos, `"4h"` for post-mortems, `"1d"` for trend analyses.
Valid `live_span` values: `1m, 5m, 10m, 15m, 30m, 1h, 4h, 1d, 2d, 1w, 1mo, 3mo, 6mo, 1y, alert`.

### 8.3 Every timeseries cell needs `formulas`
See §1.5. This is the #1 cause of empty notebooks.

### 8.4b Notebook `type` valid values
`postmortem, runbook, investigation, documentation, report, workspace, threat_hunting`.
Any other value (e.g. `executive_report`) causes a 400 API error on create.

### 8.4 ROI section requirements (customer-facing notebooks)
Required sub-sections:
- **What just happened, in operations terms** — fleet size, durations
- **Per-incident ROI table** — manual MTTR vs. automated MTTR with quantified deltas (nurse-minutes, doses, dollars)
- **Annualized scaling table** — single hospital → regional → national
- **Strategic value beyond direct labor** — sentinel-event prevention, compliance ($/avoided), retention/HCAHPS, vendor leverage
- **Bottom line** — one sentence on payback

Use real industry numbers where possible: nurse loaded cost ~$70/hr,
biomed/IT ~$110/hr, sentinel event $200K–$1M, RN turnover $40K–$80K.

---

## 9. Plugin authoring conventions

### 9.1 Subclass and required methods
```python
from dd_demo_toolkit.simulator.plugins import IncidentPlugin

class MyIncidentCascade(IncidentPlugin):
    def __init__(self): ...
    def on_tick(self, tick_count, fleet, engine): ...
    def get_incident_name(self) -> str: ...
    def reset(self) -> None: ...   # optional but recommended
```

### 9.2 Phase model (4 phases recommended)
1. **drift_up** / **ramp_up** — slow-rising root-cause metric only
2. **saturated** / **upstream_saturation** — leading-indicator metrics climb
3. **outage** / **impact** — user-visible symptoms peak
4. **recovering** — workflow remediation, signals decay

Each phase 6–12 ticks (~2–3 minutes at 15s/tick).

### 9.3 Bifurcation rules (CRITICAL for AI-driven RCA)
When adding a new plugin, it must be **disjoint from every existing
plugin in the same vertical** along all four axes:

1. **Spatial** — different floor + wing + department
2. **Metric namespace** — zero metric overlap (use sub-domain like `pyxis.*` if needed)
3. **`incident_domain` tag** — different value
4. **Temporal** — initial idle ≥ 50 ticks more than other plugins; inter-event idle ≥ 30 ticks more

Document the disjointness in the plugin's docstring as a 4-row table
(see `bd_pyxis_outage.py`).

### 9.4 State publication
Always write phase to `engine.incident_state[<plugin_key>]` so other
subsystems and dashboards can read it:
```python
engine.incident_state[<plugin_key>] = {
    "phase": phase,
    "phase_tick": phase_tick,
    "incident_domain": "<your-domain>",
    "signal_chain_root": "<short-name>",
    "department": ..., "floor": ..., "wing": ...,
}
```

### 9.5 Device mutation helper
Support both `DeviceProfile` (dataclass) and `dict` device shapes:
```python
def _set_state(self, device, metric, value):
    state = getattr(device, "state", None)
    if state is None and isinstance(device, dict):
        state = device.setdefault("state", {})
    if state is not None:
        state[metric] = value
```

---

## 10. Sub-vertical overlay rules

### 10.1 Hard constraints
- Do NOT modify the base vertical's `vertical:` block (name, env_prefix, display_name).
- Do NOT change the metric namespace prefix.
- Do NOT introduce new tag KEYS — only new VALUES under existing keys.
- Do NOT fork base resources to "tweak" them — extend with new resources instead.

### 10.2 Layout
```
verticals/<base>/
  overlays/
    <sub>.yaml                # additive simulator config
    <sub>/
      monitors.yaml
      dashboards/*.json
      notebooks.yaml
      slos.yaml
      services.yaml
      workflows.yaml
      cases.yaml
      plugins/*.py
```

### 10.3 Identification
Overlays are identified at query/filter time by:
- `device_manufacturer:<BD|Medtronic|Stryker|...>`
- `incident_domain:<pharmacy-automation|cardiac-monitoring|...>`
- `device_type:<pyxis_medstation|...>`

NOT by `sub_vertical:` or `customer:` — those keys do not exist.

### 10.4 Lifecycle
Overlay resources tag with the BASE vertical's `vertical:<base>` so a
single `dd-demo teardown --vertical <base>` removes both. There is no
overlay-only teardown.

---

## 11. Pre-commit checklist (run before adding a new asset)

- [ ] Metric names follow `{prefix}.<domain>.<name>` format with units in the suffix
- [ ] Counter metrics end in `_total`
- [ ] No new tag KEYS introduced (only values under existing keys)
- [ ] Datadog query syntax: `by {dim}` before `.as_count()`
- [ ] No `p95:`/`p99:` on gauge metrics
- [ ] No `||` / `&&` in monitor query alert queries
- [ ] All notebook timeseries cells have `formulas:` populated
- [ ] All scalar widgets have `aggregator:` set explicitly
- [ ] No `suffix:` field on `query_value` widgets (use `custom_unit:` or omit)
- [ ] Dashboard timeseries requests using `queries:` have `response_format: "timeseries"` and no legacy `on_right_yaxis` at root
- [ ] SLO metric queries use `.as_count()` on both numerator and denominator
- [ ] Notebook `type:` is one of: `postmortem, runbook, investigation, documentation, report, workspace, threat_hunting`
- [ ] **After editing any file under `verticals/`, run `make build` before `make setup`** — the `verticals/` directory is baked into the Docker image at build time (no live volume mount). `make setup` alone re-deploys whatever was in the image when it was last built, silently deploying stale content and making the live dashboard look unchanged.
- [ ] Workflow descriptions ≤ 300 characters
- [ ] If adding a plugin: disjoint from existing plugins along all 4 axes (spatial, namespace, incident_domain, temporal)
- [ ] If customer-facing notebook: includes ROI / Business Impact section
- [ ] Resource type validates with `dd-demo setup --vertical <v> --dry-run`
- [ ] If adding SDS resources: group create uses `/api/v2/sensitive-data-scanner/config/groups` (not `/config/scanning-groups`) and the payload must include `relationships.configuration.data.id` (the root config ID from GET). No fingerprinting — the SDS v2 API is stateless.

---

## 12. Where to look when something breaks

| Symptom | Likely cause | Where to check |
|---------|--------------|----------------|
| Widget shows "No data" | Wrong aggregator on metric type, or template var interpolation issue | §1.1, §1.6 |
| Notebook chart empty | Missing `formulas:` | §1.5 |
| Monitor 400 on deploy | `\|\|` operator, or unknown metric | §1.3, §5.5 |
| Dashboard 400 on deploy | `by {dim}.as_count()` order, or invalid query | §1.2 |
| Workflow 400 "description exceeds 300" | Description too long | §1.7 |
| AI/Bits SRE pulls in unrelated cascade signal | Plugin not bifurcated | §9.3 |
| Teardown leaves orphans | Wrong tagging, missing `dd-demo-toolkit:true` | `CLAUDE.md` §4 |
| Overlay deploy uses wrong vertical name | `vertical_name=` not threaded through manager | `CLAUDE.md` §6.6 |

---

## 13. Living document

When you hit a new bug class — *file the fix here* before closing the
ticket. The whole point of this document is that future contributors
shouldn't repeat the same investigation.

Last updated: 2026-06-03 (dashboard data coverage directive — §1.9; cases API quirks — §1.8).
