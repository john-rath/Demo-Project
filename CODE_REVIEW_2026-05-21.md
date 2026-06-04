# dd-demo-toolkit — Comprehensive Code Review

**Review date:** 2026-05-21
**Reviewer:** Claude Code (Opus 4.7, 1M context) — parallel sub-agent audit across security, dependencies, code quality, and ship-readiness
**Branch:** `main` @ `cdca102` (with ~10 uncommitted modifications)
**Goal:** assess readiness to ship the toolkit to the broader Datadog SE org as a self-serve demo platform.

---

## Executive summary

The toolkit is **substantively complete and well-architected** for its purpose. The secret-handling story (1Password `op://` indirection, plain-secret rejection in `env_manager`, mode-0o600 writes, gitignore guard, compose `:?` overrides) is genuinely well-designed. YAML loading uses `safe_load` universally; no `shell=True`, no string-interpolated subprocess argv, no XSS in the UI. Five verticals ship with full asset bundles, three sub-vertical overlays exist (BD, Quest, EY), and the recent UI + data-obs work materially advance the demo story.

That said, **the toolkit is not yet ready for broad SE-org rollout.** Roughly 10–12 weeks of focused work remain across four blocking areas:

1. **Distribution and CI** — no CI, no published artifacts, README still says `git clone <repo-url>` (literal placeholder).
2. **Demo correctness** — multiple verticals query metrics, dimensions, or namespaces that the engine never emits. Several SLOs and workflows will silently produce no data.
3. **Auth on the local UI** — zero CSRF/Origin protection. Any malicious local webpage can drive `POST /api/processes/teardown-all/start`. Relies entirely on the 127.0.0.1 bind, which DNS rebinding can defeat.
4. **Architectural coupling** — `simulator/rum.py` and `simulator/llm_obs.py` emit hospitality-shaped telemetry into every vertical's run.

There are **no Critical-severity findings** (no committed secrets, no RCE, no data exfil paths). The dominant risk profile is "embarrassing in front of a customer," not "compromise."

---

## 1. Security findings

### High (fix before sharing with the SE org)

| ID | Finding | Location | Fix |
|----|---------|----------|-----|
| **H1** | Web UI has zero auth on `:8765`. `POST /api/processes/{name}/start\|stop` (spawns `docker compose up`) and `POST /api/env` (writes `.env`) accept any request. 127.0.0.1 bind is bypassable via DNS rebinding from a malicious page. | [server.py:131-454](dd-demo-toolkit/dd_demo_toolkit_ui/server.py:131), [cli.py:55-115](dd-demo-toolkit/dd_demo_toolkit_ui/cli.py:55) | Add a per-launch random `X-DD-UI-Token` header check + `Host: 127.0.0.1:8765` header validation. |
| **H2** | `POST /api/env` has no CSRF protection — drive-by browser visit can swap `DD_DEMO_VERTICAL`, OTel endpoint, etc. | [server.py:244-268](dd-demo-toolkit/dd_demo_toolkit_ui/server.py:244) | Same per-launch token; reject requests missing the header. |
| **H3** | `op read <value>` argv is passed without `--` separator. Reference like `op://Employee/Datadog/api-key?--account=x` could trigger CLI option parsing in the `op` binary. | [dd_validator.py:86-91](dd-demo-toolkit/dd_demo_toolkit_ui/dd_validator.py:86) | `subprocess.run(["op", "read", "--", value], ...)`. |
| **H4** | `cp .env.template .env` leaves the file at `0o644` (umask default). `env_manager.py` writes at `0o600` correctly, but the README's recommended copy command does not. | [env_manager.py:333](dd-demo-toolkit/dd_demo_toolkit_ui/env_manager.py:333) | Make targets that touch `.env` should `chmod 600 .env`; document in README. |
| **H5** | Multiple compose services have `env_file: - .env` despite the CLAUDE.md rule. Today the `:?` overrides one section below mask any literal `op://...` injected via `env_file`, but a new maintainer adding a service without the override would leak. | [docker-compose.yaml:34-35, 62-63, 82-83, 102-103](dd-demo-toolkit/docker-compose.yaml:34) | Split `.env` into `.env.secrets` (never `env_file`'d) and `.env.public`; CI test that greps every service for a matching `:?` override. |
| **H6** | `process_supervisor._validate_env_resolved` only checks `DD_API_KEY` / `DD_APP_KEY`. `DD_CLIENT_TOKEN` (in `SECRET_KEYS`) is not checked — silent op:// pass-through if used. | [process_supervisor.py:447](dd-demo-toolkit/dd_demo_toolkit_ui/process_supervisor.py:447), [env_manager.py:53](dd-demo-toolkit/dd_demo_toolkit_ui/env_manager.py:53) | Iterate `SECRET_KEYS` instead of a hard-coded pair. |
| **H7** | OTel collector exposes zpages on `0.0.0.0:55679` (container-internal only) and `13133` health-check is host-mapped. Neither requires auth. | [otel-collector-config.yaml:44](dd-demo-toolkit/otel-collector-config.yaml:44), [docker-compose.yaml:8-10](dd-demo-toolkit/docker-compose.yaml:8) | Drop `13133:13133` host mapping; bind zpages to `127.0.0.1`. |
| **H8** | Datadog Agent has read-only Docker socket mount. Standard dd-agent practice, but document as accepted risk. | [docker-compose.yaml:180](dd-demo-toolkit/docker-compose.yaml:180) | Document; remove from any future hosted variant. |

### Medium

| ID | Finding | Location | Fix |
|----|---------|----------|-----|
| **M1** | All container base images use floating tags; `otel-contrib:latest` is the worst offender. | [docker-compose.yaml:3](dd-demo-toolkit/docker-compose.yaml:3), all Dockerfiles | Digest-pin (`@sha256:...`) externally-built images. |
| **M2** | `requests==2.31.0` — predates CVE-2024-35195 fix (Session.verify bypass). Toolkit does not set `verify=False`, so impact is theoretical. | [requirements.txt:2](dd-demo-toolkit/requirements.txt:2) | Bump to `>=2.32.3`. |
| **M3** | `protobuf==4.25.2` — predates CVE-2024-7254 fix (pure-Python parser DoS, patched in 4.25.5). `grpcio==1.62.1` lacks 3.13 wheels. | [requirements.txt:11-12](dd-demo-toolkit/requirements.txt:11) | `protobuf>=4.25.8`, `grpcio>=1.68`. |
| **M4** | `pyyaml==6.0.1` — no known CVEs but two patches behind 6.0.2. | [requirements.txt:3](dd-demo-toolkit/requirements.txt:3) | `pyyaml>=6.0.2`. |
| **M5** | `dbt-core==1.9.0` strict pin — CVE patches in 1.9.x don't apply. | [data_obs/dbt_runner/Dockerfile:27](dd-demo-toolkit/data_obs/dbt_runner/Dockerfile:27) | `dbt-core>=1.9,<1.10`. |
| **M6** | Three Dockerfiles (`data_obs/Dockerfile`, `data_obs/dbt_runner/Dockerfile`, `data_obs/llm_experiments/Dockerfile`) run as root. Only the top-level Dockerfile creates a `simulator` user. | (those files) | Add a non-root user to each. |
| **M7** | `.dockerignore` lists `tests/` but not `.venv-ui/`, `__pycache__`, `.pytest_cache/`, `*.egg-info/`. Build context leak risk is small because COPY paths are explicit, but tighten anyway. | [.dockerignore](dd-demo-toolkit/.dockerignore) | Add the missing patterns. |
| **M8** | SSE stream is unauthenticated and unbounded. Mitigated only by 127.0.0.1 bind (same exposure as H1). | [server.py:374-430](dd-demo-toolkit/dd_demo_toolkit_ui/server.py:374) | Token gating (H1 fix). |
| **M9** | `llm-experiment` service is NOT gated behind the `data-obs` profile, so `make up` for healthcare starts an EY-branded LLM experiment container. Also requires `DD_APP_KEY` which previous `make up` did not. | [docker-compose.yaml:319-340](dd-demo-toolkit/docker-compose.yaml:319) | Add `profiles: [data-obs]`. |
| **M10** | `risk_eval_experiment.py` synthesizes fake PII strings ("Account 9876543210") and sends them to LLMObs. By-design demo, but document that real customer data must never be substituted. | [risk_eval_experiment.py:262](dd-demo-toolkit/data_obs/llm_experiments/risk_eval_experiment.py:262) | Add header comment + README warning. |
| **M11** | Python `hash()` used for "deterministic" seeding. Randomized per-process under `PYTHONHASHSEED` — not actually reproducible. | [risk_eval_experiment.py:216](dd-demo-toolkit/data_obs/llm_experiments/risk_eval_experiment.py:216) | Use `hashlib.sha256` or set `PYTHONHASHSEED=0` in container. |

### Low (hygiene)

L1 `_REFERENCE_RE` permits attribute-syntax `op://` URIs (`env_manager.py:95`) — tighten regex. L2 hand-rolled gitignore matcher doesn't implement negations. L3 SSE end-event lacks structured payload. L4 `/api/env/test` has no rate limit — local DoS on `op` agent or Datadog. L5 `DatadogAPIClient` errors don't leak keys today but `__repr__` discipline is worth codifying. L6 Postgres uses `dbt`/`dbt` (container-network only, fine). L7 Kafka runs without auth + auto-create topics (container-network only, fine). L8 `EnvWriteRequest` doesn't constrain `DD_SITE` at write time (only at test time).

**No committed secrets** — `git log -p -- .env` is empty, `.gitignore:2` covers `.env`, and history is clean.

---

## 2. Bugs and demo-correctness issues

### Bugs that will show up on stage

1. **Finance SLOs filter by a non-existent `operation:` dimension.**
   [verticals/finance/slos.yaml:29-30, 64-65](dd-demo-toolkit/verticals/finance/slos.yaml:29) query `finserv.app.requests_total{service_name:...,operation:authorize_payment}`, but the engine emits this counter with only `service_name` ([engine.py:760](dd-demo-toolkit/dd_demo_toolkit/simulator/engine.py:760): `svc_attrs = {"service_name": service_name}`). Denominator is 0 → "no data" SLO.
   **Fix:** add `operation` to the counter attributes, or remove the filter.

2. **Finance workflows query a non-existent metric namespace.**
   `verticals/finance/config.yaml:5` declares `env_prefix: finserv`, but `verticals/finance/workflows.yaml` lines 12, 40, 61, 66, 108, 119 query `finance.payment.*`, `finance.fraud.*`, `finance.trading.*`, `finance.market.*`. Every `datadog_query` step returns empty data; downstream condition steps silently misbehave.
   **Fix:** rename to `finserv.*` (or add an alias prefix).

3. **Insurance monitors and dashboards reference metrics the simulator never emits.**
   [verticals/insurance/monitors.yaml:8, 35](dd-demo-toolkit/verticals/insurance/monitors.yaml:8), [verticals/insurance/dashboards/command-center.json:46, 73, 190](dd-demo-toolkit/verticals/insurance/dashboards/command-center.json:46), and SLOs use `insurer.claims.processing_ms`, `insurer.claims.fnol_to_assignment_sec`, `insurer.adjuster.available`. Config declares short names like `avg_processing_ms`. Engine normalizes to `insurer.<device_type>.<short>`. Several metric names don't exist anywhere.
   **Fix:** rename queries to the engine-emitted shape, or extend the config to produce the expected metric names.

4. **Many workflow steps deploy as `noop`.**
   `dd_demo_toolkit/resources/workflows.py:64-73` still has `# unverified` placeholders for `http_request`, `sleep`, `datadog_incident`, `datadog_case` — all used in real workflow YAMLs (finance, hospitality, insurance). Steps fall through to `com.datadoghq.core.noop`. The workflow deploys and looks like it works, but the action does nothing.
   **Fix:** finish the verified-action-ID catalog; add a CI assert that no step resolves to `noop`.

5. **Style-guide §1.1 percentile violations on gauges.**
   The user's memory explicitly flags this class of bug. Confirmed violations:
   - [healthcare/overlays/bd/monitors.yaml:62, 90](dd-demo-toolkit/verticals/healthcare/overlays/bd/monitors.yaml:62) — `p95:hospital.pyxis.dispense_latency_ms` and `p95:hospital.pyxis.witness_countersign_latency_ms`, declared as `type: gauge` in [bd.yaml:113, 184](dd-demo-toolkit/verticals/healthcare/overlays/bd.yaml:113).
   - [healthcare/overlays/bd/workflows.yaml:48](dd-demo-toolkit/verticals/healthcare/overlays/bd/workflows.yaml:48) — same pattern in a verify step.
   - [healthcare/dashboards/noc-overview.json:752](dd-demo-toolkit/verticals/healthcare/dashboards/noc-overview.json:752) — `p95:hospital.app.latency_ms`.
   - [insurance/monitors.yaml:8, 35](dd-demo-toolkit/verticals/insurance/monitors.yaml:8) + insurance notebook entries (31, 70, 143).
   **Fix:** swap to counts/rates/gauges (per user preference in memory) or convert offending metrics to distributions.

6. **Style-guide §2 — invented tag keys (77 occurrences in 13 files).**
   `business_unit:`, `sla_critical:`, `tier:` are all used across finance assets; `team:dd-demo-<vertical>` is mass-injected by `dashboards.py:92` with a value pattern not in the documented value list. These violate the "never invent new tag keys" rule.
   **Fix:** drop the invented keys, or fold their values under existing keys (e.g. `incident_domain:`).

### Bugs in code paths

- **`process_supervisor._wait_for_exit` race.** [process_supervisor.py:549](dd-demo-toolkit/dd_demo_toolkit_ui/process_supervisor.py:549): `stop()` + `_grace_timer` can race when the user issues a second stop while shutdown is in progress; `_kill_group` may try to signal a recycled PID. Add `h.proc.returncode is None` guard inside `_kill_group` ([:469-475](dd-demo-toolkit/dd_demo_toolkit_ui/process_supervisor.py:469)).
- **Asymmetric log-drop in `_broadcast`.** [process_supervisor.py:514-522](dd-demo-toolkit/dd_demo_toolkit_ui/process_supervisor.py:514): slow subscribers drop lines, but the line *is* in `h.log_buffer`, so replay-on-reconnect shows it. Confusing UX; document or unify.
- **Wide `except Exception` in engine init.** [engine.py:341-364](dd-demo-toolkit/dd_demo_toolkit/simulator/engine.py:341) swallows LLM Obs + RUM init errors at `warning` level. A typo in `OTEL_EXPORTER_OTLP_ENDPOINT` silently disables them.
- **Pagination scope-limits.** CLAUDE.md §4 documents `list_workflows`, `list_incidents`, `list_cases`, `list_slos` were left un-paginated because they "stay below the default page." Demos hitting busy Datadog orgs can blow past this — especially `list_slos` (default 1000).
- **Private-attr mutation in dataset normalization.** [risk_eval_experiment.py:370](dd-demo-toolkit/data_obs/llm_experiments/risk_eval_experiment.py:370) reads `dataset._records` — a ddtrace bump can break silently.

### Inefficiencies

- `Manager.deploy_all` is O(N²) over resource types (`manager.py:102`).
- `_create_instruments` re-normalizes device configs (`engine.py:518`).
- Synchronous tick loop with no per-tick duration metric — insurance fleet (200 adjuster mobile × locations) can drift at 1s intervals (`engine.py:606-628`).
- No `requests.Session` reuse in `dd_api.py:_request` — TLS handshake per page, multiplied across paged calls.

---

## 3. Dependencies

### Surface
- **No lockfile anywhere** (`uv.lock`, `poetry.lock`, `requirements.lock` all absent). A fresh `pip install -e .` and a `pip install -r requirements.txt` produce different transitive trees today.
- **Drift between `requirements.txt` (pinned) and `pyproject.toml` (floor-only)** for the same packages.
- **Duplicated `ddtrace>=2.18.0`** in `data_obs/requirements.txt` and `data_obs/llm_experiments/requirements.txt`.
- **Risk-list packages missing from declarations:** `openai`, `anthropic`, `pydantic`, `cryptography`, `jinja2` — all arrive transitively. The LLM experiment runner especially should declare its LLM SDK directly.
- **Python-version inconsistency:** main `Dockerfile` is 3.12; `data_obs/*` Dockerfiles are 3.13; `pyproject.toml` says `>=3.10`. `grpcio==1.62.1` lacks 3.13 wheels (not currently installed in the 3.13 containers, but a footgun).

### Action items
- Make `pyproject.toml` the single source of truth; delete the top-level `requirements.txt` or generate it via `pip-compile`.
- Add `[project.optional-dependencies].data-obs` and `.llm-experiments` extras; Dockerfiles install `pip install '.[data-obs]'`.
- Commit a `uv.lock` (or pip-tools output) for reproducible SE-laptop builds.
- Digest-pin all four external compose images, especially replacing `otel-contrib:latest`.
- Bump risk-list packages: `protobuf>=4.25.8`, `requests>=2.32.3`, `pyyaml>=6.0.2`, `fastapi>=0.115`, `uvicorn>=0.30`.

---

## 4. Architecture / ship-readiness

### What's shipped (concrete)

**5 base verticals + 3 overlays.** Healthcare (8 dashboards, BD + Quest overlays), finance (1 dashboard, EY overlay with LLM eval), hospitality (8 dashboards), insurance (1), manufacturing (1). Each base vertical has the full asset bundle (`config.yaml`, `services.yaml`, `monitors.yaml`, `slos.yaml`, `notebooks.yaml`, `workflows.yaml`, `incidents.yaml`, `cases.yaml`).

**CLI.** `dd-demo list/setup/simulate/teardown` with `--vertical`, `--sub-vertical`, `--all-verticals`, `--dry-run`, `--force`.

**Web UI** (FastAPI + vanilla HTML/JS). Phase 1-3 complete: env editing, credential validation, vertical/overlay pickers, **process supervisor for start/stop/deploy/teardown via SSE log streaming**.

**Docker + Make.** `make up/setup/teardown/teardown-all/ui` with `op run` resolution; `check-op` preflight; profile-gated `data-obs` stack (mostly — see M9).

**1Password-backed secrets.** Enforced end-to-end via `env_manager.SECRET_KEYS`, compose `:?` guards, and `CLAUDE.md §0.5`. The repo's `.env` on disk is `0o600`.

**Cleanup story.** Pagination fix in `utils/dd_api.py` (per CLAUDE.md §4); `--all-verticals` sweep keyed off `dd-demo-toolkit:true` marker (§5).

**Workflow action catalog** (`WORKFLOW_ACTIONS.md` + `_TYPE_TO_ACTION_ID` map). Discovery procedure documented.

**data_obs stack.** Producer / feature-pipeline / eval-consumer Kafka pipeline with dd-trace + DSM; Postgres-backed dbt loop emitting OpenLineage; continuous LLM-Obs experiment (gpt-4.5 vs gpt-4o-mini, 3-min loop).

**Tests.** 5 files, 86 test functions, 1,376 lines. **Heavily skewed to UI** (4/5 files); core engine, resource managers, plugin discovery, and `dd_api.py` pagination have effectively no coverage.

### In-flight work (uncommitted)

```
M Makefile
M data_obs/dbt_runner/Dockerfile
M data_obs/dbt_runner/run_loop.sh
M data_obs/llm_experiments/risk_eval_experiment.py     +40 lines
M dd_demo_toolkit_ui/process_supervisor.py             +28 lines
M dd_demo_toolkit_ui/static/index.html                 +25 lines
M docker-compose.yaml                                  +64 lines
M verticals/finance/dashboards/command-center.json
M verticals/finance/notebooks.yaml
M verticals/finance/slos.yaml
```
Two active threads: **(a) data-obs / EY 5/19 demo finalization**, **(b) UI Phase 2-3 wrap-up (process supervisor + SSE log pane)**. Both need to be committed and reviewed before any rollout.

### Blockers (prioritized)

1. **No CI.** No `.github/workflows/`, no PR gating, no `dd-demo list` smoke per vertical, no JSON schema check on dashboards. YAML typo → SE finds out on stage.
2. **No CODEOWNERS / CONTRIBUTING / SUPPORT / on-call doc / issue templates.** README §Support is "contact Datadog Sales Engineering" with no channel, owner, or SLA.
3. **No distribution mechanism.** README still says `git clone <repo-url>` (literal placeholder, README line ~217). No internal PyPI, no signed Datadog Docker image, no install script. 200 SEs will each build their own image.
4. **Test coverage gap.** 4/5 test files target the UI. Engine, resource managers, and the pagination fix are untested in-repo (the fix was verified by a one-off script outside the repo per CLAUDE.md §4).
5. **`simulator/rum.py` and `simulator/llm_obs.py` load globally and emit hospitality-shaped telemetry for every vertical.** `engine.py:340-361`. Explicitly deferred in CLAUDE.md §3.7 but blocking for cross-vertical demos.
6. **No per-vertical walkthrough / talk-track doc.** Only `data_obs/README.md` (EY storyline) qualifies. Healthcare, finance base, hospitality, insurance, manufacturing have no scripts.
7. **Self-telemetry absent.** No dogstatsd from the toolkit itself — we won't know which SEs run which vertical or what fails in the field.
8. **README repo-URL placeholder + `pyproject.toml` URLs point at `github.com/DataDog/dd-demo-toolkit`.** Confirm/replace.
9. **Asset parity asymmetry.** Healthcare + hospitality ship 8 dashboards each; finance, insurance, manufacturing ship 1. Finance customer demo will feel thin next to healthcare.
10. **`llm-experiment` not profile-gated** (also M9 in security).

Nice-to-have: hospitality genericization (CLAUDE.md §3 lists the rename debt), plugin-discovery validation pass, paginate the remaining `list_*` calls.

---

## 5. Proposed phased project plan

### Status: completed scope (checkmark format)

- [x] CLI: list/setup/simulate/teardown with overlay support
- [x] 5 base verticals + 3 overlays (BD, Quest, EY)
- [x] Resource managers: dashboards, monitors, SLOs, notebooks, services, workflows, incidents, cases
- [x] Simulator engine + plugin system (4-axis disjoint enforcement)
- [x] Sub-vertical overlay config-merge + plugin discovery
- [x] 1Password-backed secret handling end-to-end
- [x] Web UI Phase 1-3 (env editing, credential validation, simulator panel, deploy panel, SSE logs)
- [x] Cleanup / teardown pagination fix; `--all-verticals` sweep
- [x] Workflow action-ID catalog + payload-shape doc
- [x] Docker + Make targets with `op run` wrapping
- [x] `data_obs` stack: Kafka pipeline + dbt + OpenLineage + continuous LLM experiment
- [x] Style guide + workflow-actions reference docs
- [x] MIT license

### In flight (uncommitted; finish + commit in Phase 1)

- [ ] data-obs / EY 5/19 demo finalization (dbt runner, LLM experiment refinement, finance dashboard/SLO/notebook polish)
- [ ] UI process supervisor enhancements + index.html updates

### Phase 1 — Make it cloneable (2 weeks; blocking)

| Item | Size | Notes |
|------|------|-------|
| Fix `<repo-url>` placeholder; reconcile `pyproject.toml` URLs | S | README ~line 217 |
| Profile-gate `llm-experiment` behind `data-obs` | S | docker-compose.yaml:319 |
| Commit in-flight data-obs + UI work behind reviewed feature commits | S | |
| `.github/workflows/ci.yml`: lint, pytest, `docker compose build`, `dd-demo list` smoke, dashboard JSON schema check | M | |
| CONTRIBUTING.md, CODEOWNERS, SUPPORT.md, .github/ISSUE_TEMPLATE/{bug,feature,new-vertical}.yml | M | Name owners; pick Slack channel |
| `dd-demo validate --vertical <name>` (imports every plugin, parses every YAML/JSON) | S | Wire into CI |

### Phase 2 — Demo correctness (3–4 weeks; blocking)

| Item | Size | Notes |
|------|------|-------|
| Fix finance SLO `operation:` dimension mismatch | S | Either emit `operation` attr or drop filter |
| Fix finance workflow `finance.*` → `finserv.*` namespace | S | Mass-rename or env-prefix alias |
| Fix insurance monitors/dashboards metric names | M | Several phantom metrics |
| Verify all workflow action IDs; assert no step resolves to `noop` in CI | M | |
| Fix §1.1 percentile-on-gauge violations | S | Per user memory: prefer counts/rates |
| Remove invented tag keys (`business_unit`, `sla_critical`, `tier`, `team:dd-demo-*`) | M | |
| Decouple `simulator/rum.py` + `llm_obs.py` from global load — opt-in via `features:` in `config.yaml` | L | CLAUDE.md §3.7 owns this |
| Add core-engine + resource-manager unit tests; codify pagination contract in-repo | M | |
| Backfill dashboards in finance / insurance / manufacturing for parity | M | |

### Phase 3 — Security hardening (1–2 weeks; blocking)

| Item | Size | Notes |
|------|------|-------|
| Add per-launch random `X-DD-UI-Token` + `Host:` header check | M | Closes H1, H2, M8 |
| `subprocess.run(["op", "read", "--", value], ...)` | S | H3 |
| Mode-0600 on `cp .env.template .env` paths | S | H4 |
| Iterate `SECRET_KEYS` in `_validate_env_resolved` | S | H6 |
| Bump pinned deps (`requests`, `protobuf`, `pyyaml`) + digest-pin compose images | S | M1–M5 |
| Non-root user in `data_obs/*` Dockerfiles | S | M6 |
| Drop `13133` host mapping; zpages → 127.0.0.1 | S | H7 |
| Split `.env` into `.env.secrets` + `.env.public`; CI assert `:?` override on every DD-key-consuming service | M | H5 |
| Replace `hash()` with `hashlib.sha256` in `risk_eval_experiment.py` | S | M11 |

### Phase 4 — Distribution (2 weeks; blocking)

| Item | Size | Notes |
|------|------|-------|
| Publish versioned Docker image to a Datadog internal registry; tag on `git tag v*` | M | |
| Publish wheel to internal PyPI | S | |
| `install.sh` one-liner: Brew + Colima + op + clone + `make ui-install` | S | |
| Tag `v0.1.0` with `CHANGELOG.md` | S | |
| Consolidate deps: `pyproject.toml` as single source; commit `uv.lock` | M | |

### Phase 5 — Demo enablement (3 weeks; blocking)

| Item | Size | Notes |
|------|------|-------|
| `verticals/<name>/WALKTHROUGH.md` per vertical (10-min talk track, click path, "wow" moment, AIOps story) | M | |
| 5-minute Loom per vertical | S | |
| Sub-vertical authoring tutorial (BD/Quest/EY → step-by-step) | S | |

### Phase 6 — Operate at scale (4 weeks; nice-to-have, after rollout starts)

| Item | Size | Notes |
|------|------|-------|
| Self-instrument toolkit via dogstatsd to a Datadog internal org | M | which vertical/overlay; setup/teardown durations; failure stacks |
| UI Phase 4-5: throughput tuning, overlay scaffolding | M | per UI README backlog |
| Quarterly demo-data freshness job | S | |

### Phase 7 — Polish (rolling; nice-to-have)

- Hospitality location-dimension genericization (CLAUDE.md §3 debt)
- Plugin-discovery hardening (validation pass)
- Paginate remaining `list_workflows` / `list_incidents` / `list_cases` / `list_slos`
- `requests.Session` reuse in `dd_api.py`
- Cache normalized device configs in `_create_instruments`

### Totals

- **Phases 1–5 are gating** for broad SE-org rollout.
- **Estimated effort:** ~10–12 weeks single-engineer; ~5–6 weeks parallelized across two.
- **Phases 6–7** ride after rollout begins.

---

## Appendix: positive observations

- The op:// secret-handling chain (UI rejection, compose `:?` overrides, file-mode enforcement, gitignore guard) is genuinely thoughtful and pre-empts the most common SE-tooling secret-leak failure modes.
- The `--all-verticals` orphan-sweep mechanism (CLAUDE.md §5) is well-designed: tag-keyed, dry-run-supporting, customer-resource-safe.
- The 4-axis disjoint plugin rule (CLAUDE.md §9.3, §6.5) is the right architectural primitive for sub-vertical overlays.
- The workflow action-ID catalog discipline (read `WORKFLOW_ACTIONS.md` before authoring) addresses a real pain point that would otherwise recur on every new workflow.
- `STYLE_GUIDE.md` translates past production-demo bugs into rules. The instinct to encode lessons-learned is right; the gap is in CI enforcement.

The work is good. The rollout-readiness gap is real but tractable.
