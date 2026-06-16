# AdventHealth "Sensing Hospital" — Demo Runbook

Live bring-up for the Version Two demo. Everything is committed on
`version-two` and statically validated; this is the first run against a real
Datadog org.

## 0. Prerequisites
- `op` (1Password CLI) signed in — `eval "$(op signin)"`. All Datadog-hitting
  targets resolve `.env` `op://` refs via `op run`.
- Docker running with enough headroom for ~17 small containers.
- `.env` set with `DD_API_KEY` / `DD_APP_KEY` as `op://` refs and `DD_SITE`.

## 1. Deploy the AdventHealth overlay assets (dashboards/monitors/etc.)
```bash
# In .env: DD_DEMO_VERTICAL=healthcare, DD_DEMO_SUB_VERTICAL=adventhealth
make build && make setup
```
Creates the AdventHealth dashboards (incl. the EuD dashboard), monitors, SLOs,
the RCA notebook, services, the auto-remediation workflow, and cases — tagged
`vertical:healthcare` + `incident_domain:care-experience`.

Sanity-check without deploying: `dd-demo setup --vertical healthcare
--sub-vertical adventhealth --dry-run` (expects 28 resources, 0 errors).

## 2. Bring up the real mock app (real Datadog Agent)
```bash
make build-mock-app
make up-mock-app
open http://localhost:8800        # the RUM-instrumented care portal
make logs-mock-app                # watch the mesh
```
Within a minute you should see, in Datadog:
- **APM service map** — care-portal → care-summary-api → {patient-context,
  clinical-alerts→notification}; and edge → router → (Redis Stream) →
  consumer → care-experience-platform → {rtls, patient-context, clinical-alerts}.
- **Infra** — containers/processes for every service + Postgres + Redis.
- **DBM** — `sensing-postgres` query metrics/samples.
- **Custom metrics** — `care.*` (rtls/platform/router/portal/consumer/...).
- **Logs** — trace-correlated, from every container.

## 3. RUM + Synthetics (browser traffic)
RUM needs a real browser. Either open `http://localhost:8800` yourself, or use
the Synthetic private location:
```bash
# One-time: create a Private Location in Datadog, paste its config + id into .env
#   DATADOG_PRIVATE_LOCATION_CONFIG=... , DD_SYNTHETICS_PRIVATE_LOCATION_ID=pl:...
make up-synthetics
make synthetics-create            # browser test on care-portal + API test
```
RUM creds (`DD_RUM_APPLICATION_ID`, `DD_CLIENT_TOKEN`) are set on the Configure
tab of `make ui` (or directly in `.env`).

## 4. The demo arc
1. Open the **care portal** → RUM sessions begin; show the RUM→APM link.
2. Show the **service map** / a distributed trace spanning on-prem→cloud.
3. The cascade self-drives: `rtls-location-service` poll rate climbs
   (`RTLS_AUTO_CASCADE=true`) → resolve latency up → cloud platform + device
   experience degrade. Point at the AdventHealth dashboards + EuD view; let
   **Bits AI** isolate it to the on-prem RTLS root cause.
4. **Automated repair:** `make remediate` (or the workflow webhook) clamps the
   poll rate → latency recovers. Real detect→repair, on-prem and cloud targets.

## 5. Teardown
```bash
make down-mock-app
make down-synthetics
make synthetics-delete
make teardown                     # removes the healthcare + AdventHealth assets
```

## Notes / known follow-ups
- First live run may surface image-build or Agent-connectivity issues — none of
  this has run end-to-end yet.
- `k8s/sensing-hospital/` is the EKS lift target (needs the image in ECR + the
  Datadog Operator).
- Data Observability is **not** wired (dbt artifacts aren't uploaded).
