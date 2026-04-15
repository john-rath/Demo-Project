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

### 2. Deploy Resources + Start Simulator (Docker)

```bash
# Start the full stack (OpenTelemetry Collector + Simulator)
docker compose up

# In a separate terminal, run setup to create Datadog resources
docker compose --profile setup up setup
```

Metrics and logs will begin flowing to Datadog within 30 seconds. Pre-built dashboards will populate automatically.

### 3. Or Use the CLI Directly (Local)

```bash
# Install the package
pip install -e .

# List available verticals
dd-demo list

# Create Datadog resources (dashboards, monitors, SLOs)
dd-demo setup --vertical healthcare

# Start the simulator
dd-demo simulate --vertical healthcare
```

---

## Available Verticals

| Vertical | Devices | Services | Incident Scenario |
|----------|---------|----------|-------------------|
| **Healthcare** | 56+ | 9 | WiFi AP outage cascades to infusion pump failures |
| **Finance** | 309+ | 10 | Database replication lag → payment processing timeout |
| **Manufacturing** | 246+ | 8 | Robot servo degradation → assembly line halt |
| **Insurance** | 290+ | 9 | Catastrophe event triggers claims processing surge |

### Healthcare: Smart Hospital Demo

Multi-floor hospital with medical IoT (patient monitors, infusion pumps, ventilators), network infrastructure, environmental sensors, and clinical application services. The demo includes a phased WiFi outage that cascades to device failures—perfect for showing how Datadog correlates infrastructure and application metrics.

**Key Resources**: 9 services, 6 SLOs, 15+ monitors covering patient safety, device reliability, and network health.

### Finance: Global Financial Services Platform

JPMorgan-scale financial services firm with retail banking, capital markets, wealth management, risk infrastructure, and fraud detection. Multi-region, high-frequency trading, mission-critical payment systems. The incident: database replication lag in the primary region cascading to payment processing timeouts in real-time trading and retail operations.

**Key Resources**: 10 services, 8 SLOs, 20+ monitors for trading latency, payment throughput, fraud detection, and regulatory compliance.

### Manufacturing: Automotive Manufacturing Plant

Tesla/Toyota-scale automotive plant with industrial equipment, PLCs, robots, conveyors, quality systems, environmental controls, and supply chain management. The incident: robot arm servo degradation increasing cycle time, which backs up the conveyor system and triggers a line halt.

**Key Resources**: 8 services, 7 SLOs, 18+ monitors for equipment health, production rate, quality metrics, and predictive maintenance.

### Insurance: Multi-State P&C/Life Insurer

Multi-state property and casualty plus life insurer with policy management, underwriting, telematics, claims processing, fraud detection, and billing systems. The incident: a major weather event (hurricane, hailstorm, tornado) creates a surge in claims submissions that cascades through the system.

**Key Resources**: 9 services, 7 SLOs, 16+ monitors for claims processing, policy renewals, underwriting SLAs, and fraud signals.

---

## CLI Reference

### `dd-demo list`
List all available verticals with descriptions and stats.

```bash
dd-demo list
```

Output:
```
Available Verticals:
  healthcare      Smart Hospital Demo (56 devices, 9 services)
  finance         Global Financial Services Platform (309 devices, 10 services)
  manufacturing   Automotive Manufacturing Plant (246 devices, 8 services)
  insurance       Multi-State P&C/Life Insurer (290 devices, 9 services)
```

### `dd-demo setup`
Create Datadog resources for a vertical: dashboards, monitors, SLOs, service catalog entries, notebooks.

```bash
# Create healthcare demo resources
dd-demo setup --vertical healthcare

# With custom display name
dd-demo setup --vertical finance --display-name "Goldman Sachs Global Operations"

# Save output to file for review
dd-demo setup --vertical manufacturing > setup_output.txt
```

### `dd-demo simulate`
Start the simulator and emit metrics, logs, and traces for a vertical.

```bash
# Start healthcare simulator (metrics every 15 seconds)
dd-demo simulate --vertical healthcare

# Faster emit rate (metrics every 5 seconds)
dd-demo simulate --vertical finance --emit-interval 5

# Run incident after 60 seconds, then terminate after 180 seconds total
dd-demo simulate --vertical manufacturing \
  --incident-delay 60 \
  --run-time 180
```

### `dd-demo teardown`
Remove all resources created by `dd-demo setup` for a vertical.

```bash
dd-demo teardown --vertical healthcare
```

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
│   │   └── plugins/                  # Incident scenarios
│   │       └── wifi_cascade.py
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

When you're done with a demo, remove all resources:

```bash
# Remove healthcare demo resources
dd-demo teardown --vertical healthcare

# This removes all dashboards, monitors, SLOs, notebooks, and service catalog entries
# created by dd-demo setup
```

Alternatively, filter by tag in the Datadog UI: `env:demo` and `vertical:healthcare`.

---

## Contributing

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
