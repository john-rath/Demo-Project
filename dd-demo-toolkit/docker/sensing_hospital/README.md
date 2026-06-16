# Sensing Hospital — mock environment (real Datadog Agent)

A small, **real** containerized application that emits Agent-collected
telemetry (APM, logs, infrastructure) alongside the toolkit's synthetic OTel
stream. It mirrors the AdventHealth overlay's **on-prem → cloud** care-experience
cascade so the live app and the synthetic fleet tell the same story. Phase 3 +
4 of Version Two.

This is the answer to David's "spin up containers for mock IoT + use the actual
Datadog Agent + support on-prem as well as cloud" ask.

## What runs (`mock-app` profile)

| Service | Role | deployment tag |
|---|---|---|
| `datadog-agent-sh` | **Real Datadog Agent** — APM intake, logs (container collect-all), process/container infra via Docker socket | — |
| `edge-device-fleet` | Config-driven mock IoT fleet (RTLS badges, room-sensing gateways, bedside tablets) posting events | on-prem |
| `care-event-router` | Edge service; first trace hop; forwards to the cloud platform | on-prem |
| `care-experience-platform` | Cloud tier; blocks on the on-prem RTLS resolve (where the cascade surfaces) | cloud |
| `rtls-location-service` | On-prem edge; **cascade root cause** — resolve latency climbs with poll rate | on-prem |
| `remediation-controller` | Phase 4 detect→repair webhook; clamps the RTLS poll rate to recover | cloud |

All Python services are auto-instrumented with `dd-trace-py` (`ddtrace-run`),
so the Agent produces a real **APM service map**, distributed traces, and logs.
`deployment:on-prem` / `deployment:cloud` tags model both worlds on one network.

## Run it

```bash
# Standalone (recommended while iterating):
make build-mock-app
make up-mock-app          # dd-agent + microservices + edge fleet (detached)
make logs-mock-app

# Or ride along with the normal stack by setting the flag in .env:
#   DD_DEMO_MOCK_FLEET=true
make up                   # otel-collector + simulator + mock-app

make down-mock-app        # stop just this stack
```

Requires `op` (1Password) auth like every other Datadog-hitting target — the
Agent's `DD_API_KEY` is resolved by `op run`, never written to `.env`.

## The cascade (on-prem → cloud)

1. `edge-device-fleet` posts device events to `care-event-router` (on-prem).
2. `care-event-router` forwards to `care-experience-platform` (cloud).
3. `care-experience-platform` calls `rtls-location-service` (on-prem) to attach
   a bed/room location — **every ingest blocks on this call**.
4. With `RTLS_AUTO_CASCADE=true` (default), the RTLS poll rate periodically
   drifts up (firmware-config storm), so its resolve latency climbs from ~45ms
   toward ~1.8s. That latency propagates up the trace into the cloud platform
   and back to the device edge — the on-prem→cloud cascade, visible in the
   service map and a single distributed trace.

## Phase 4 — automated repair (detect → repair)

The AdventHealth overlay's auto-remediation **Workflow** (triggered by the
cascade monitors) calls the remediation controller:

```bash
# What the Datadog Workflow step POSTs (on-prem clamp):
curl -XPOST http://remediation-controller:8080/remediate -d '{"target":"on-prem"}'
# Convenience target against the running stack:
make remediate
```

The controller calls `rtls-location-service /admin/poll-rate?rate=60`, the
resolve latency drops, and the whole cascade recovers — a **real control action**
against a running service, not a scripted phase. `target: "cloud"` exercises the
cloud-side remediation path (rate-limit/autoscale hook) so both worlds are
demonstrable.

## Configure per vertical

`fleet_config.yaml` describes the edge fleet per scenario (keyed by
`MOCK_SCENARIO`, defaulting to `DD_DEMO_VERTICAL`). Add a block to re-skin the
same containers for another vertical — no compose changes needed.

## Status

Foundation is built and statically validated (`docker compose config`,
`py_compile`). It has **not** yet been run live against a Datadog org
(`make build-mock-app && make up-mock-app`) — that's the next step and needs
real credentials. Open follow-ups: richer per-vertical scenarios, a cloud-side
remediation that does real work, and wiring the overlay workflow's webhook URL
to `remediation-controller`.
