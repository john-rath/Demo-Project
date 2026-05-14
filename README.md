# dd-demo-toolkit

**A modular, production-grade Datadog demo framework for sales engineers**

dd-demo-toolkit provides a turnkey platform for rapidly deploying realistic, industry-vertical demo scenarios with pre-built dashboards, monitors, SLOs, and incident playbooks. Each vertical simulates hundreds of real-world devices and services, complete with correlated failure scenarios to showcase Datadog's AIOps, observability, and RCA capabilities.

---

## Overview

dd-demo-toolkit is built for Datadog Sales Engineers who need to:
- **Demo quickly**: Deploy a full vertical with one command
- **Show real value**: 300+ correlated metrics, traces, and logs per scenario
- **Tell a story**: Pre-built incident scenarios demonstrate Datadog's ability to detect, diagnose, and respond to cascading failures
- **Reuse and extend**: Plugin architecture makes it easy to add new verticals or customize existing ones

Each vertical is a complete simulation:
- **Infrastructure**: Hundreds of simulated devices (IoT, network, application infrastructure)
- **Applications**: Multi-tier services with APM instrumentation
- **Incidents**: Choreographed failure scenarios that build narrative tension
- **Dashboards, Monitors, SLOs**: Pre-built resources that tell the story of your demo

---

## Web UI (optional)

Don't want to hand-edit `.env`? Run the local web UI:

```bash
cd dd-demo-toolkit/
make ui-install          # one-time: python3.13 venv + UI deps
make ui                  # serves http://127.0.0.1:8765
```

Open **http://127.0.0.1:8765** in your browser. The UI today lets you:

- Pick the vertical and (optional) sub-vertical overlay from dropdowns
- Enter Datadog credentials, with a **Test connection** button that validates both API and APP keys before saving
- Set `EMIT_INTERVAL`, `DISPLAY_NAME`, OTel endpoint/protocol
- Save to `.env` (written at file mode `0o600`, refuses to write if `.env` isn't gitignored)

Coming in later phases: start/stop the simulator from the browser, deploy/teardown assets, tune throughput and volume curves, scaffold new sub-vertical overlays.

The UI is a thin wrapper — `.env` and the `verticals/` YAML files on disk remain the source of truth, and everything the UI does maps to a CLI command you could run by hand. See [`dd-demo-toolkit/dd_demo_toolkit_ui/README.md`](dd-demo-toolkit/dd_demo_toolkit_ui/README.md) for architecture, endpoint reference, and how to run the test suite.

> The UI binds to `127.0.0.1` only and has no auth — single-user local tool, not a hosted service. Don't expose it on a non-loopback interface without reading the safety notes in the module README.

---

## Quick Start

### 1. Clone and Configure

```bash
# Clone the repository
git clone <repo-url> dd-demo-toolkit
cd dd-demo-toolkit

# Copy and configure environment
cp .env.template .env
# Edit .env with your Datadog credentials:
#   DD_API_KEY=<your-api-key>
#   DD_APP_KEY=<your-app-key>
#   DD_SITE=datadoghq.com (or datadoghq.eu, etc.)
```

### 2. Deploy Resources + Start Simulator (Docker or Colima)

The toolkit runs on any Docker-compatible runtime. [Colima](https://github.com/abiosoft/colima)
is supported as a drop-in alternative to Docker Desktop on macOS — the same
`docker compose` commands below work unchanged. To start Colima with enough
headroom for the simulator stack:

```bash
brew install colima docker docker-compose
colima start --cpu 4 --memory 8
```

`colima start` creates a "colima" Docker context and switches `docker` to it
automatically; the VM persists across reboots, so this only needs to run once.

```bash
# Start the full stack (OpenTelemetry Collector + Simulator)
docker compose up

# In a separate terminal, run setup to create Datadog resources
docker compose --profile setup up setup
```

Metrics and logs will begin flowing to Datadog within 30 seconds. Pre-built dashboards will populate automatically.

#### Including a sub-vertical overlay (e.g. BD on top of healthcare)

Set `DD_DEMO_SUB_VERTICAL` in your `.env` file to layer an overlay on
top of the base vertical. The simulator and setup containers both
honor it.

```bash
# In .env
DD_DEMO_VERTICAL=healthcare
DD_DEMO_SUB_VERTICAL=bd

# Then the same commands deploy + simulate base + overlay
docker compose --profile setup up setup    # deploys healthcare + BD
docker compose up                           # simulates healthcare + BD plugins
```

> **Rebuild after adding/changing the env var.** Both containers read
> `DD_DEMO_SUB_VERTICAL` at runtime, but the Dockerfile CMD that
> appends `--sub-vertical` only changes if the image was built with the
> updated Dockerfile. After upgrading from a pre-overlay image, run
> `docker compose build` to refresh the simulator and setup images.

#### Cleaning up (Docker)

Two profile-scoped services are provided for cleanup. Both are one-shot
containers; neither requires the stack to be running.

```bash
# Remove resources for the vertical in DD_DEMO_VERTICAL (default: healthcare)
docker compose --profile teardown up teardown

# Nuke everything: deletes every resource tagged 'dd-demo-toolkit:true'
# across ALL verticals, including orphans from renamed/removed verticals.
docker compose --profile teardown-all up teardown-all
```

Preview first with `--dry-run`:

```bash
docker compose --profile teardown-all run --rm teardown-all \
  dd-demo teardown --all-verticals --dry-run
```

> **Rebuild after code changes.** The `setup`, `teardown`, and
> `teardown-all` services share the image built by Docker. If you pull
> or edit Python code, rebuild before running the cleanup command:
> `docker compose --profile teardown-all build teardown-all`. A sign
> you're on a stale image is that `--help` inside the container
> doesn't list the `--all-verticals` flag.

### 3. Or Use the CLI Directly (Local)

```bash
# Install the package
pip install -e .

# List available verticals (also lists sub-vertical overlays per vertical)
dd-demo list
dd-demo list --vertical healthcare

# Create Datadog resources (dashboards, monitors, SLOs)
dd-demo setup --vertical healthcare

# Layer a customer/sub-segment overlay on top of the base vertical
dd-demo setup --vertical healthcare --sub-vertical bd

# Start the simulator (with the overlay's plugins active)
dd-demo simulate --vertical healthcare --sub-vertical bd
```

> **What's a sub-vertical?** A reusable, additive overlay that layers
> customer- or segment-specific devices, services, dashboards,
> monitors, and incident plugins onto an existing base vertical
> *without forking it*. The base vertical's metric namespace and tag
> standards are preserved, so overlays cost nothing in dashboard
> rewrites. Healthcare ships with the **BD** overlay — see
> [Sub-Vertical Overlays](#sub-vertical-overlays).

---

## Available Verticals

| Vertical | Devices | Services | Incident Scenario |
|----------|---------|----------|-------------------|
| **Healthcare** | 56+ | 9 | WiFi AP outage cascades to infusion pump failures |
| **Finance** | 309+ | 10 | Database replication lag → payment processing timeout |
| **Manufacturing** | 246+ | 8 | Robot servo degradation → assembly line halt |
| **Insurance** | 290+ | 9 | Catastrophe event triggers claims processing surge |
| **Hospitality** | 240+ | 7 | WiFi client overload → IoT gateway cascade → guest-experience impact |

### Healthcare: Smart Hospital Demo

Multi-floor hospital with medical IoT (patient monitors, infusion pumps, ventilators), network infrastructure, environmental sensors, and clinical application services. The demo includes a phased WiFi outage that cascades to device failures—perfect for showing how Datadog correlates infrastructure and application metrics.

**Key Resources**: 9 services, 6 SLOs, 15+ monitors covering patient safety, device reliability, and network health.

**Available sub-vertical overlays:**

| Overlay | Customer profile | What it adds |
|---------|------------------|--------------|
| `bd` | **Becton Dickinson** | Pyxis MedStation ES (18 cabinets) + BACTEC FX, Rowa Vmax, Phoenix M50, Veritor Plus. New services `pyxis-inventory-api` and `pharmacy-ehr-bridge`. Pyxis inventory-sync polling-storm cascade plugin (Pharmacy / Floor 1 South), 47-widget Pyxis MedStation dashboard, BitsSRE walkthrough notebook, and a poll-rate-limit auto-remediation workflow. Bifurcated from the base WiFi cascade by department, metric namespace, `incident_domain` tag, and a 90-130 tick startup delay. |
| `quest` | **Quest Diagnostics** | Reference-lab software stack instead of hospital IoT: LIMS cluster (Beaker-class), HL7 integration engine cluster (Rhapsody-class), QC rules engine, provider results portal, specimen tracking, courier dispatch, reagent inventory. Lab instruments (Roche cobas chemistry, Sysmex hematology, Roche molecular/PCR, Abbott immunoassay) modeled as *seen via middleware*, plus Inpeco pre-analytic centrifuges + sorters and Sensitech cold-chain refrigeration (real IoT). HL7 engine bad-config-push cascade plugin (Lab / fleet-wide) drives the full chain: routing errors → outbound queue saturation → LIMS back-pressure → specimen backlog → contracted-TAT breach. Two dashboards (Lab NOC overview + HL7 engine detail), a deep RCA notebook that explicitly walks the data-sourcing model (where each metric originates in a real Quest environment), a TAT SLO burn runbook, count-over-count SLOs only (no percentile metrics), and a config-rollback auto-remediation workflow. Bifurcated from the base WiFi cascade and BD Pyxis cascade by department (`Lab`), metric namespace (`hospital.hl7.*` / `hospital.lims.*` / `hospital.tat.*` / `hospital.specimen.*`), `incident_domain:diagnostic-laboratory` tag, and a 90-130 tick startup delay. |

Run with: `dd-demo setup --vertical healthcare --sub-vertical bd && dd-demo simulate --vertical healthcare --sub-vertical bd`

Or for the Quest Diagnostics art-of-the-possible demo: `dd-demo setup --vertical healthcare --sub-vertical quest && dd-demo simulate --vertical healthcare --sub-vertical quest`

### Finance: Global Financial Services Platform

JPMorgan-scale financial services firm with retail banking, capital markets, wealth management, risk infrastructure, and fraud detection. Multi-region, high-frequency trading, mission-critical payment systems. The incident: database replication lag in the primary region cascading to payment processing timeouts in real-time trading and retail operations.

**Key Resources**: 10 services, 8 SLOs, 20+ monitors for trading latency, payment throughput, fraud detection, and regulatory compliance.

### Manufacturing: Automotive Manufacturing Plant

Tesla/Toyota-scale automotive plant with industrial equipment, PLCs, robots, conveyors, quality systems, environmental controls, and supply chain management. The incident: robot arm servo degradation increasing cycle time, which backs up the conveyor system and triggers a line halt.

**Key Resources**: 8 services, 7 SLOs, 18+ monitors for equipment health, production rate, quality metrics, and predictive maintenance.

### Insurance: Multi-State P&C/Life Insurer

Multi-state property and casualty plus life insurer with policy management, underwriting, telematics, claims processing, fraud detection, and billing systems. The incident: a major weather event (hurricane, hailstorm, tornado) creates a surge in claims submissions that cascades through the system.

**Key Resources**: 9 services, 7 SLOs, 16+ monitors for claims processing, policy renewals, underwriting SLAs, and fraud signals.

### Hospitality: Smart Hotel / Multi-Property Portfolio

Global hospitality operator with connected-room IoT, property networks (Meraki APs, Cisco switches), energy/HVAC, revenue intelligence, a reservations portal, a guest loyalty program, ServiceNow ITSM integration, and an AI-powered stay planner. Property brands are genericized into tiers (Luxury Collection, Premium Resort, Full Service, Upscale Select, Select Service, Extended Stay). The incident: an anomalous WiFi client surge degrades AP signal strength, causing IoT gateways to lose connected room devices (smart locks, thermostats, TVs), which Datadog Workflows then auto-remediates via the Meraki API — demonstrating zero-touch resolution.

**Key Resources**: 7 services, 7 SLOs, 14+ monitors covering RevPAR/occupancy, IoT gateway fleet uptime, digital check-in, WiFi, and self-healing automation.

---

## CLI Reference

### `dd-demo list`
List all available verticals with descriptions and stats. With
`--vertical <name>`, also lists sub-vertical overlays available for
that vertical.

```bash
dd-demo list
dd-demo list --vertical healthcare
```

Output (per-vertical view, abbreviated):
```
Smart Hospital Demo
  Name: healthcare
  ...
Sub-vertical overlays:
  • bd  (use --sub-vertical bd)
```

### `dd-demo setup`
Create Datadog resources for a vertical: dashboards, monitors, SLOs, service catalog entries, notebooks. Optionally layer a sub-vertical overlay on top.

```bash
# Create healthcare demo resources
dd-demo setup --vertical healthcare

# Layer the BD (Becton Dickinson) sub-vertical overlay on top — adds
# Pyxis MedStation devices, services, dashboard, monitors, notebook,
# SLOs, workflow, and the Pyxis cascade plugin. Tags inherit from the
# base vertical (vertical:healthcare, dd-demo-toolkit:true) so
# teardown sweeps base + overlay together.
dd-demo setup --vertical healthcare --sub-vertical bd

# With custom display name
dd-demo setup --vertical finance --display-name "Goldman Sachs Global Operations"

# Save output to file for review
dd-demo setup --vertical manufacturing > setup_output.txt
```

### `dd-demo simulate`
Start the simulator and emit metrics, logs, and traces for a vertical. With `--sub-vertical`, the overlay's devices/services are merged into the simulated fleet and the overlay's incident plugins are loaded alongside the base vertical's plugins.

```bash
# Start healthcare simulator (metrics every 15 seconds)
dd-demo simulate --vertical healthcare

# Run with the BD overlay merged in: extra devices (Pyxis, BACTEC,
# Rowa, Phoenix, Veritor), extra services (pyxis-inventory-api,
# pharmacy-ehr-bridge), and the Pyxis inventory-sync cascade plugin
# fire alongside the existing WiFi cascade.
dd-demo simulate --vertical healthcare --sub-vertical bd

# Faster emit rate (metrics every 5 seconds)
dd-demo simulate --vertical finance --emit-interval 5

# Run incident after 60 seconds, then terminate after 180 seconds total
dd-demo simulate --vertical manufacturing \
  --incident-delay 60 \
  --run-time 180
```

### `dd-demo teardown`
Remove resources created by `dd-demo setup`. Runs in two modes: scoped to
one vertical (the default) or a full sweep of every toolkit-managed
resource across all verticals (`--all-verticals`, useful for cleaning up
orphans from renamed / removed verticals).

```bash
# Scoped to a single vertical (prompts for confirmation)
dd-demo teardown --vertical healthcare

# Full sweep across every vertical, including orphans
dd-demo teardown --all-verticals

# Preview first — always safe
dd-demo teardown --all-verticals --dry-run

# Skip the interactive confirmation (CI / scripting)
dd-demo teardown --all-verticals --force
```

Safety: the all-verticals sweep only touches resources carrying the
`dd-demo-toolkit:true` tag (or the `[dd-demo-toolkit:` description
marker for dashboards). Any resource without that marker — customer
dashboards, production monitors, etc. — is never touched.

Exactly one of `--vertical <name>` or `--all-verticals` must be
provided; passing both or neither exits with an error.

---

## Sub-Vertical Overlays

Sub-vertical overlays are reusable, additive customizations that layer
customer- or segment-specific content onto an existing base vertical.
They are how the toolkit ships per-customer art-of-the-possible demos
(BD on top of healthcare today; Medtronic, Stryker, Abbott, etc. on
the same pattern tomorrow) without forking the base.

### Why an overlay instead of a new vertical?

A new vertical means a coordinated rename of the metric namespace,
duplicated dashboards, drifting plugins, and a whole second teardown
target. An overlay sidesteps all of that: it shares the base
vertical's `env_prefix` (e.g. `hospital.*`), its tag standards
(`vertical:healthcare`, `team:`, `incident_domain:`, `signal_chain:`),
and its lifecycle (deploy and teardown follow the base vertical).

### Layout

Each base vertical can host any number of overlays under
`verticals/<vertical>/overlays/`:

```
verticals/healthcare/
  config.yaml                         # base vertical
  monitors.yaml
  ...
  overlays/
    bd.yaml                           # additive simulator config
                                      #   (devices, services merged
                                      #   into base on load)
    bd/                               # overlay resources (each file
                                      #   is optional)
      monitors.yaml
      notebooks.yaml
      slos.yaml
      workflows.yaml
      cases.yaml
      services.yaml                   # Service Catalog entries
      dashboards/
        bd-pyxis-medstation.json
      plugins/
        bd_pyxis_outage.py            # IncidentPlugin subclass
```

The YAML file and the directory are independent; an overlay can be
config-only, resource-only, or both.

### How they merge

`dd-demo setup --vertical <vert> --sub-vertical <name>` does, in order:

1. Loads the base config and merges `<name>.yaml` on top: device
   lists are concatenated per category, top-level `services` is
   concatenated, location dimensions are appended. The `vertical`
   block (name, `env_prefix`, `display_name`) is **never** modified —
   overlays cannot rename the vertical or change the metric namespace.
2. Deploys the base vertical's resources.
3. Deploys the overlay directory's resources (monitors, dashboards,
   notebooks, SLOs, services, workflows, cases) tagged with the base
   vertical's name (`vertical:healthcare`, not `vertical:bd`), so the
   overlay rides on the base vertical's lifecycle.

`dd-demo simulate --vertical <vert> --sub-vertical <name>` merges the
config the same way and additionally loads any
`overlays/<name>/plugins/*.py` modules so their incident scenarios
fire alongside base-vertical incidents.

### Tag standards (strict)

Overlays MUST stay inside the base vertical's existing tag keyspace.
Auto-injected: `vertical:<base>`, `dd-demo-toolkit:true`. Reusable
keys: `team:<role>`, `incident_domain:<value>` (new values fine, key
stays the same), `signal_chain:<position-name>`, `safety:<level>`,
`compliance:<framework>`. Query-side dimensions emitted by the engine
(`device_type`, `device_manufacturer`, `floor`, `wing`, `department`,
`service_name`) are freely usable. **Do not** add overlay-specific
keys like `sub_vertical:`, `customer:`, or `overlay:` — overlays are
identified by *values* under existing keys (e.g.
`device_manufacturer:BD`).

### Bifurcating overlay incidents from base incidents

When an overlay introduces its own incident plugin, it must be
disjoint from any base-vertical plugin so AI-driven RCA tools (Bits
AI SRE) can isolate one story from the other. The BD overlay's Pyxis
cascade is disjoint from the base WiFi cascade along all four axes:

| Axis | Base WiFi cascade | BD Pyxis cascade |
|------|-------------------|------------------|
| Floor / Wing | 3 / East | 1 / South |
| Department | ED / ICU | Pharmacy |
| Metric namespace | `hospital.network.*`, pump signals | `hospital.pyxis.*` only |
| `incident_domain` | `network-to-device` | `pharmacy-automation` |
| Initial idle | 20–40 ticks | 90–130 ticks |

Filtering by `incident_domain:pharmacy-automation` (or by
`device_type:pyxis_medstation`) yields a clean signal chain with zero
overlap with the WiFi story.

### Common commands

```bash
# Discover overlays available for a vertical
dd-demo list --vertical healthcare

# Deploy base + overlay
dd-demo setup --vertical healthcare --sub-vertical bd

# Simulate with overlay (extra devices, services, plugins)
dd-demo simulate --vertical healthcare --sub-vertical bd

# Teardown is NOT overlay-scoped — overlays ride the base lifecycle
dd-demo teardown --vertical healthcare        # removes base + bd
```

### Adding a new overlay

1. Pick the base vertical (e.g. `healthcare`).
2. Create `verticals/<vertical>/overlays/<name>.yaml` for additive
   simulator config (extra device categories, devices, services).
   Use the existing `env_prefix` for all metric names so dashboards
   that already filter by `device_manufacturer:` keep working.
3. Create `verticals/<vertical>/overlays/<name>/` and add any of:
   `monitors.yaml`, `dashboards/*.json`, `notebooks.yaml`, `slos.yaml`,
   `workflows.yaml`, `cases.yaml`, `services.yaml`,
   `plugins/<incident>.py`.
4. If you add an `IncidentPlugin`, make it disjoint from base-vertical
   plugins along spatial, namespace, `incident_domain`, and temporal
   axes (see the BD overlay's plugin docstring for a template).
5. Verify with `dd-demo list --vertical <vertical>` (your overlay
   should appear) and `dd-demo setup --vertical <vertical> --sub-vertical <name> --dry-run`.

---

## Architecture

### Config-Driven Engine

dd-demo-toolkit is driven by YAML configuration files, not code. Each vertical defines:

1. **Devices**: Simulated IoT, network, and infrastructure devices with realistic metric drift
2. **Services**: APM-instrumented applications with service dependencies
3. **Dashboards**: Pre-built visualizations organized by ops persona
4. **Monitors**: Alert rules tied to incident scenarios
5. **SLOs**: Service-level objectives that track during incidents
6. **Plugins**: Choreographed incident scenarios

Example device definition (from healthcare):
```yaml
device_categories:
  medical_iot:
    devices:
      - type: patient_monitor
        manufacturer: Philips
        model: IntelliVue MX800
        count: 24
        metrics:
          - name: "{prefix}.device.battery_pct"
            type: gauge
            range: [5, 100]
          - name: "{prefix}.device.cpu_usage_pct"
            type: gauge
            range: [1, 95]
```

### Plugin System

Incidents are defined as plugins. Each plugin:
- Extends `IncidentPlugin` base class
- Defines incident phases (ramp_up, saturated, outage, recovering)
- Modifies device/service metrics in real-time
- Can be triggered manually or scheduled

Example: Healthcare's WiFi outage plugin shows the temporal lag between AP saturation (phase 2) and pump failures (phase 3), which trains AI/ML models for better RCA.

### Resource Management

The framework automatically creates:
- **Dashboards**: Organized by role (Ops, SRE, Manager)
- **Monitors**: Thresholds tied to SLO targets
- **Notebooks**: Incident runbooks and investigation guides
- **Service Catalog**: Service dependencies and ownership
- **SLOs**: Error rate, latency, and availability SLOs

All resources are tagged consistently for easy discovery and cleanup.

---

## Adding a New Vertical

### Step 1: Create Vertical Directory

```bash
mkdir -p verticals/myvertical/{dashboards,plugins}
```

### Step 2: Define Devices and Services

Create `verticals/myvertical/config.yaml`:

```yaml
vertical:
  name: myvertical
  display_name: "My Vertical Name"
  description: "What this vertical demonstrates"
  env_prefix: myapp
  emit_interval_sec: 15

device_categories:
  infrastructure:
    devices:
      - type: server
        manufacturer: Dell
        count: 50
        metrics:
          - name: "{prefix}.server.cpu_pct"
            type: gauge
            range: [1, 100]
          - name: "{prefix}.server.memory_mb"
            type: gauge
            range: [1024, 65536]

services:
  - name: api
    description: "REST API"
    dependencies: [database]
  - name: database
    description: "Primary database"
    dependencies: []
```

### Step 3: Define APM and Monitoring

Create `verticals/myvertical/services.yaml`:

```yaml
services:
  - name: api
    language: python
    framework: fastapi
    traces:
      - endpoint: /health
        latency_p99_ms: 10
    dependencies:
      - name: database
        latency_p99_ms: 50
```

Create `verticals/myvertical/monitors.yaml`:

```yaml
monitors:
  - name: "API Error Rate High"
    type: metric_alert
    query: "avg:trace.web.request.errors{service:api}"
    threshold: 0.05
    severity: critical
```

### Step 4: Create an Incident Plugin

Create `verticals/myvertical/plugins/my_incident.py`:

```python
from dd_demo_toolkit.simulator.plugins import IncidentPlugin

class MyIncident(IncidentPlugin):
    """Describes your incident scenario."""
    
    def setup(self, devices, services):
        """Initialize incident state."""
        pass
    
    def tick(self, phase_num, tick_in_phase):
        """Modify metrics in real-time."""
        # phase_num: 0, 1, 2, ... (your incident phases)
        # tick_in_phase: ticks elapsed in current phase
        pass
    
    def get_phases(self):
        """Return list of (phase_name, ticks_in_phase) tuples."""
        return [
            ("ramp_up", 5),
            ("outage", 10),
            ("recovery", 5),
        ]
```

### Step 5: Build Dashboards (Optional)

Dashboards can be created via:
- **Code**: Define JSON in `verticals/myvertical/dashboards/`
- **UI**: Build in Datadog and export JSON, commit to repo

### Step 6: Test and Deploy

```bash
# Validate configuration
dd-demo validate --vertical myvertical

# Deploy resources
dd-demo setup --vertical myvertical

# Test simulator
dd-demo simulate --vertical myvertical --run-time 60
```

---

## Project Structure

```
dd-demo-toolkit/
├── dd_demo_toolkit/                    # Main Python package
│   ├── __init__.py
│   ├── cli.py                         # Click CLI entry points
│   ├── config.py                      # YAML config loader and validator
│   ├── resource_manager.py            # Datadog API resource creation
│   └── simulator/
│       ├── __init__.py
│       ├── engine.py                  # Main simulation engine
│       ├── device.py                  # Device class (emits metrics)
│       ├── service.py                 # Service class (APM traces)
│       └── plugins.py                 # IncidentPlugin base class
│
├── verticals/                          # Industry verticals
│   ├── healthcare/
│   │   ├── config.yaml               # Device/service definitions
│   │   ├── services.yaml             # APM configuration
│   │   ├── monitors.yaml             # Alert rules
│   │   ├── slos.yaml                 # SLO definitions
│   │   ├── notebooks.yaml            # Incident runbooks
│   │   ├── dashboards/               # Dashboard JSON
│   │   │   ├── overview.json
│   │   │   ├── devices.json
│   │   │   └── services.json
│   │   ├── plugins/                  # Base-vertical incident scenarios
│   │   │   └── wifi_cascade.py
│   │   └── overlays/                 # Sub-vertical overlays (additive)
│   │       ├── bd.yaml               # additive simulator config
│   │       ├── bd/                   # additive resources
│   │       │   ├── monitors.yaml
│   │       │   ├── notebooks.yaml
│   │       │   ├── slos.yaml
│   │       │   ├── workflows.yaml
│   │       │   ├── cases.yaml
│   │       │   ├── services.yaml
│   │       │   ├── dashboards/
│   │       │   │   └── bd-pyxis-medstation.json
│   │       │   └── plugins/
│   │       │       └── bd_pyxis_outage.py
│   │       ├── quest.yaml            # Quest Diagnostics additive config
│   │       └── quest/                # Quest overlay resources
│   │           ├── monitors.yaml
│   │           ├── notebooks.yaml
│   │           ├── slos.yaml
│   │           ├── workflows.yaml
│   │           ├── cases.yaml
│   │           ├── services.yaml
│   │           ├── dashboards/
│   │           │   ├── quest-lab-noc-overview.json
│   │           │   └── quest-hl7-engine-detail.json
│   │           └── plugins/
│   │               └── quest_hl7_config_cascade.py
│   │
│   ├── finance/
│   ├── manufacturing/
│   └── insurance/
│
├── tests/                             # Unit tests
│   ├── __init__.py
│   ├── test_config.py               # Config loader tests
│   ├── test_simulator.py            # Engine tests
│   └── test_verticals.py            # Vertical validation
│
├── Dockerfile                        # Docker build
├── docker-compose.yaml               # Docker Compose orchestration
├── otel-collector-config.yaml        # OpenTelemetry Collector config
├── .env.template                     # Configuration template
├── pyproject.toml                    # Python project metadata
├── requirements.txt                  # Python dependencies
└── README.md                         # This file
```

---

## Resource Types

When you run `dd-demo setup --vertical <name>`, the following resources are created in Datadog:

### Dashboards
- **Overview**: High-level KPIs (error rate, latency, throughput)
- **Devices**: Infrastructure health, device-level metrics
- **Services**: APM traces, dependency graph, service-level SLIs
- **Incidents**: Timeline and metrics during incident playback

### Monitors
- **Threshold alerts**: CPU, memory, error rate
- **Anomaly detection**: Baseline deviation for metrics
- **Composite monitors**: Multi-metric conditions (e.g., "DB lag AND timeout rate > 5%")
- **Event-based**: Incident start/end, deployment markers

### SLOs
- **Availability**: Uptime percentage
- **Latency**: P99 response time
- **Error rate**: % of requests that error
- **Compliance**: Regulatory or internal SLAs

### Service Catalog
- Service ownership and contacts
- Dependencies and critical paths
- On-call schedules
- Runbooks and playbooks

### Notebooks
- **Incident Timeline**: Annotated metric graphs during incident
- **RCA Guide**: Step-by-step investigation procedure
- **Runbook**: Manual remediation steps

All resources are tagged with `env:demo` and vertical-specific tags for easy discovery and cleanup.

---

## Teardown

When you're done with a demo, remove resources. Pick the mode that
matches what you want to clean up.

### Scoped to one vertical

Removes everything tagged `vertical:<name>` for the named vertical.
This includes any sub-vertical overlay resources, since overlays are
intentionally tagged with the base vertical's name and ride on its
lifecycle.

```bash
# Local CLI (also removes any deployed overlays for this vertical)
dd-demo teardown --vertical healthcare

# Docker
docker compose --profile teardown up teardown     # uses $DD_DEMO_VERTICAL
```

### Full environment sweep (`--all-verticals`)

Deletes every resource tagged `dd-demo-toolkit:true` regardless of
vertical. Use this when you want to wipe the Datadog environment clean,
including orphans from renamed or removed verticals that the scoped
teardown can no longer find:

```bash
# Local CLI
dd-demo teardown --all-verticals

# Docker (one-shot container)
docker compose --profile teardown-all up teardown-all
```

Always preview with `--dry-run` first — it prints exactly what would be
deleted and then exits without touching anything:

```bash
dd-demo teardown --all-verticals --dry-run

docker compose --profile teardown-all run --rm teardown-all \
  dd-demo teardown --all-verticals --dry-run
```

### Safety guarantees

- `--all-verticals` only matches resources with the `dd-demo-toolkit:true`
  tag (or the `[dd-demo-toolkit:` description marker for dashboards).
  Customer-owned monitors, dashboards, and SLOs without that marker are
  never touched.
- Exactly one of `--vertical <name>` or `--all-verticals` is required;
  passing both or neither exits with a validation error.
- `--force` skips the interactive confirmation prompt (the Docker
  services pass `--force` so the one-shot containers don't hang waiting
  for stdin — that's why you should dry-run first).

### Finding resources in the Datadog UI

- Any vertical: filter by tag `dd-demo-toolkit:true`.
- Specific vertical: filter by tag `vertical:<name>` (e.g. `vertical:healthcare`).

---

## Contributing

### Style guide (read first)

Before authoring any new dashboard, monitor, notebook, SLO, workflow,
plugin, service, or sub-vertical overlay, read
[**dd-demo-toolkit/STYLE_GUIDE.md**](dd-demo-toolkit/STYLE_GUIDE.md). It
captures the Datadog query gotchas, tag standards, naming conventions,
layout patterns, and incident-bifurcation rules that prevent the
production-demo bugs we've shipped before.

Highlights of the highest-bug-density rules:

- Percentile aggregators (`p95:`, `p99:`) work only on histogram
  metrics, not gauges. Use `max:` for KPI strips.
- `by {dim}` must come BEFORE `.as_count()`, not after.
- Monitor query alerts do not support `||`. Split into two monitors.
- Notebook timeseries cells require `formulas:` on every request or
  the chart renders empty.
- Workflow descriptions have a 300-character limit.
- Never invent new tag keys; use existing values under existing keys.
- New incident plugins must be 4-axis disjoint (spatial, metric
  namespace, `incident_domain` tag, temporal).

The style guide includes a pre-commit checklist (§11) — run through it
before opening a PR with new assets.

### Adding a New Vertical

See [Adding a New Vertical](#adding-a-new-vertical) above.

### Improving an Existing Vertical

1. Clone the repo
2. Edit YAML configs in `verticals/<name>/`
3. Test with `dd-demo validate --vertical <name>`
4. Submit a PR with:
   - Updated configs
   - New dashboard JSON (if applicable)
   - Updated incident plugin (if needed)
   - Test coverage for new scenarios

### Reporting Issues

If a vertical doesn't deploy or simulate correctly:
1. Run `dd-demo validate --vertical <name>` to check configuration
2. Check `dd-demo setup --verbose` for API errors
3. Review simulator logs: `dd-demo simulate --verbose --run-time 30`
4. Open an issue with:
   - Vertical name
   - Error message
   - Datadog site (US/EU/etc.)

---

## Troubleshooting

### Docker Compose Fails to Start

**Problem**: `docker compose up` fails or containers don't stay running.

**Solution**:
1. Ensure `.env` is populated with valid `DD_API_KEY` and `DD_APP_KEY`
2. Check Docker daemon is running: `docker ps`
3. Review logs: `docker compose logs simulator`
4. Verify Datadog credentials: `curl -H "DD-API-KEY: $DD_API_KEY" https://api.datadoghq.com/api/v1/validate`

### No Metrics Appear in Datadog

**Problem**: Simulator runs but no metrics visible in Datadog.

**Solution**:
1. Check OpenTelemetry Collector is healthy: `curl http://localhost:13133/`
2. Verify OTEL_EXPORTER_OTLP_ENDPOINT is set correctly
3. Review collector logs: `docker compose logs otel-collector | grep error`
4. Ensure DD_API_KEY is valid and has metric write permission

### Dashboard Won't Load

**Problem**: Dashboards appear empty or show "No Data".

**Solution**:
1. Wait 2-3 minutes for metrics to arrive and be indexed
2. Check monitor query matches metric name: `avg:hospital.device.battery_pct{*}`
3. Verify time range in dashboard includes current data
4. Manually emit test metric: `dd-demo simulate --run-time 60`

### Incident Plugin Doesn't Trigger

**Problem**: Ran simulator with `--incident-delay` but incident doesn't occur.

**Solution**:
1. Check plugin file exists: `verticals/<name>/plugins/*.py`
2. Verify plugin class extends `IncidentPlugin`
3. Run with verbose logging: `dd-demo simulate --verbose`
4. Manually test plugin: `python -m dd_demo_toolkit.cli simulate --vertical <name> --incident-delay 5 --run-time 120`

---

## Performance and Scale

- **Metrics emission**: 1000–5000 metrics/minute per vertical depending on device count and emit interval
- **Memory**: ~200 MB per simulator instance
- **CPU**: ~1 CPU core per vertical
- **Network**: ~10 Mbps outbound to Datadog (uncompressed OpenTelemetry)

For production use, compress OpenTelemetry data and batch requests in the collector configuration.

---

## License

[Your License Here]

---

## Support

For questions or issues, contact Datadog Sales Engineering or open an issue in this repository.
