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
| `datadog-agent-sh` | **Real Datadog Agent** — APM intake, DogStatsD, logs (container collect-all), process/container infra via Docker socket | — |
| `care-portal` | **Web frontend** (RUM-instrumented); host `:8800`; proxies same-origin `/api/*` into the cascade | cloud |
| `edge-device-fleet` | Config-driven mock IoT fleet (RTLS badges, room-sensing gateways, bedside tablets) posting events | on-prem |
| `care-event-router` | Edge service; first trace hop; forwards to the cloud platform | on-prem |
| `care-experience-platform` | Cloud tier; blocks on the on-prem RTLS resolve (where the cascade surfaces) | cloud |
| `rtls-location-service` | On-prem edge; **cascade root cause** — resolve latency climbs with poll rate | on-prem |
| `remediation-controller` | Phase 4 detect→repair webhook; clamps the RTLS poll rate to recover | cloud |

All Python services are auto-instrumented with `dd-trace-py` (`ddtrace-run`),
so the Agent produces a real **APM service map**, distributed traces, and logs.
`deployment:on-prem` / `deployment:cloud` tags model both worlds on one network.

## Telemetry this actually produces (via the real Agent)

| Signal | Status | Source |
|---|---|---|
| APM / distributed traces / service map | ✅ real | `ddtrace-run` on every service; real HTTP between them |
| Logs (trace-correlated) | ✅ real | Agent container-collect-all + `DD_LOGS_INJECTION` |
| Infrastructure: container / process / host metrics | ✅ real | Agent via Docker socket + `/proc` + cgroups + process-agent |
| APM-derived metrics (hits/errors/latency) | ✅ real | generated from traces |
| **Custom app metrics (DogStatsD)** | ✅ real | `metrics.py` → Agent `:8125`; `care.rtls.*`, `care.platform.*`, `care.router.*`, `care.portal.*` |
| **RUM** (sessions/views/resources/actions/replay) | ✅ ready | `care-portal` loads the RUM browser SDK; needs `DD_RUM_APPLICATION_ID` + `DD_CLIENT_TOKEN` set, and traffic on the page (see Synthetics below) |

## RUM frontend (`care-portal`)

`care-portal` serves a real browser page instrumented with the Datadog RUM
Browser SDK. Credentials are injected at runtime via `/config.js` from
`DD_RUM_APPLICATION_ID` + `DD_CLIENT_TOKEN` (resolved by `op run`; the
client token is public-by-design but stored as an `op://` ref per policy) —
never baked into the image. `allowedTracingUrls` links each RUM session to the
backend APM trace through `care-portal → care-event-router →
care-experience-platform → rtls-location-service`. Open it at
`http://localhost:8800` after `make up-mock-app`.

If the RUM env vars are unset the page still works — RUM just stays dormant.

## Generating traffic with Datadog Synthetics (next step)

RUM needs a real browser to load the page; a `curl` won't produce RUM data.
The intended traffic source is a **Datadog Synthetic browser test** hitting
`care-portal`. Because the app runs locally, public Synthetic managed
locations can't reach it — so this needs a **Synthetics Private Location
worker** (a `datadog/synthetics-private-location-worker` container) that
executes the test against the local URL, OR a public tunnel. That piece is
proposed but not yet built; it will also flip Synthetics to "available" in the
UI product picker.

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
