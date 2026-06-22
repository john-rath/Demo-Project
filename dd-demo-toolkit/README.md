# dd-demo-toolkit

Modular Datadog demo framework for Sales Engineers. Pick an industry **vertical**,
optionally layer a customer **overlay**, and the toolkit emits realistic telemetry
(metrics, traces, logs, RUM, LLM Obs, …) and deploys a full asset suite
(dashboards, monitors, SLOs, workflows, notebooks, incidents, cases, services)
into your **own Datadog org** — in minutes.

> **UI-first.** `make ui` is the front door. As an SE you should never need the
> terminal beyond that one command. The `dd-demo` CLI exists for CI and for
> power users authoring overlays — see [Advanced / CLI](#advanced--cli).

---

## Quick start

### Prerequisites

| Tool | Why | Install |
|---|---|---|
| **Docker Desktop** (running) | runs the simulator + supporting stack | <https://www.docker.com/products/docker-desktop/> |
| **1Password CLI** (`op`) | secrets live in 1Password, never in files | `brew install --cask 1password-cli` then `eval "$(op signin)"` (or unlock the desktop app) |
| **Python 3.13** | runs the local UI server | `brew install python@3.13` |
| A **Datadog org** + API/APP keys | the demo deploys here | each SE uses their own org; store the keys in 1Password (below) |

### 1. Configure (one-time)

Store your Datadog keys in 1Password (see [Handling secrets](#handling-secrets)),
then copy the template:

```bash
cp .env.template .env
```

`.env` ships with `op://Employee/Datadog/...` references plus `DD_SITE` and
`DD_DEMO_VERTICAL` defaults — edit the vault/item paths to match yours. **Never
put plain keys in `.env`** (the UI rejects them; corp policy).

### 2. Launch the UI

```bash
make ui-install   # one-time: creates .venv-ui (Python 3.13) + installs deps
make ui           # the only command you need from here on
```

`make ui` checks your environment, builds fresh images, and opens the UI at
**http://127.0.0.1:8765** (loopback only). From there everything is point-and-click:

1. **Configure** — pick a vertical + optional overlay, choose the products to
   demo, set/verify your Datadog credentials.
2. **Simulator** — **Start** to begin emitting telemetry.
3. **Deploy assets** — push dashboards / monitors / SLOs / workflows / … into
   your org.
4. **Status** — see running containers and the toolkit resources live in Datadog.

Demo, then **Tear down** from the Deploy tab when you're done. That's it.

---

## Handling secrets

Corp policy: **real Datadog keys live in 1Password, not on disk.** `.env` holds
only `op://<vault>/<item>/<field>` references, which `op run` resolves into
short-lived env vars at command time (every Make target that touches Datadog
wraps in `op run` automatically). The UI's save endpoint and `docker compose`
both reject plain keys.

One-time 1Password setup:

```bash
# 1. Pick a vault you can write to (Employee is fine):
op vault list

# 2. Create one item holding both keys as fields:
op item create --category 'API Credential' \
  --title 'Datadog' --vault Employee \
  api-key='<paste API key>' \
  app-key='<paste APP key>'

# 3. Point .env at it (matching your vault/item/field names):
#    DD_API_KEY=op://Employee/Datadog/api-key
#    DD_APP_KEY=op://Employee/Datadog/app-key

# 4. Verify resolution (should print the real key, not the op:// literal):
op run --env-file=.env -- env | grep DD_API_KEY
```

Already have a plain-text `.env`? Run `make migrate-secrets` for step-by-step
migration instructions. Editing `.env` by hand, watch for **smart quotes** —
type apostrophes by hand or disable the macOS auto-substitution.

---

## What's in the box

- **Verticals** (`verticals/`): `healthcare`, `finance`, `hospitality`,
  `insurance`, `manufacturing` — each a self-contained scenario with its own
  fleet, services, dashboards, monitors, SLOs, workflows, notebooks, incidents,
  and incident-cascade plugins.
- **Overlays**: additive, customer-specific layers on top of a vertical
  (e.g. `adventhealth`, `bd`, `quest` on `healthcare`) — no forking required.
- **Simulator**: emits OTel metrics/traces/logs and product telemetry on a loop;
  incident plugins choreograph realistic failures for live RCA stories.
- **Optional real stacks** (docker-compose profiles): the Sensing Hospital mock
  app (real Datadog Agent + microservices + edge fleet), a DBM stack, a Data
  Streams Monitoring stack, and a Synthetics private location.

---

## Advanced / CLI

Power users (authoring overlays) and CI use the `dd-demo` CLI and Make targets
directly. The CLI is the engine beneath the UI — anything the UI does, the CLI
can too.

```bash
dd-demo list                                   # discover verticals + overlays
dd-demo setup    --vertical healthcare [--sub-vertical adventhealth]
dd-demo teardown --vertical healthcare         # or --all-verticals
```

Common Make targets (`make help` lists all):

| Target | Purpose |
|---|---|
| `make ui` | **The SE front door** — preflight + launch the web UI |
| `make up` / `make down` | start / stop the simulator stack |
| `make setup` / `make teardown` | deploy / remove resources for the current vertical |
| `make test` | unit + static tests (no credentials needed) |
| `make validate-live` | live Datadog data checks (requires `make up` first) |

---

## Contributing

One repo, **contribute-back via PR**. The ~70–80% who pick a vertical run a
known-good tagged release and never fork; the ~20–30% who build bespoke overlays
branch off `main`, scaffold an overlay, and PR it back so the shared catalog
grows for everyone. CI gates every PR (lint + tests + asset validation) so
`main` stays releasable. (Authoring kit + `CONTRIBUTING.md` are landing as part
of the GA push — see the roadmap.)

---

## Documentation

- **[CLAUDE.md](CLAUDE.md)** — architecture, conventions, and the UI-first principle (§0.6).
- **[STYLE_GUIDE.md](STYLE_GUIDE.md)** — asset-authoring rules; **read before adding any dashboard / monitor / workflow**.
- **[WORKFLOW_ACTIONS.md](WORKFLOW_ACTIONS.md)** — verified Datadog Workflow action IDs.
- **[GA_ROADMAP.md](GA_ROADMAP.md)** — the GA / self-service roadmap and current phase.
