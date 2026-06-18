# GA & Self-Service Roadmap

**Status: GA push in progress (Phase 0).** This is the durable, in-repo companion
to the approved working plan — it tracks how `dd-demo-toolkit` becomes
self-service for the SE org. Update it as phases land.

## Guiding decisions

- **UI-first** — `make ui` is the single SE front door (see [CLAUDE.md](CLAUDE.md) §0.6).
  The `dd-demo` CLI is the engine beneath it and the CI / power-user interface.
- **"90%" target = Datadog product modules** (APM, RUM, Logs, DBM, LLM Obs,
  Synthetics, NPM, Profiling, CSM/CWS, Cloud Cost, DSM, Error Tracking, …),
  measured by `dd-demo coverage`. Not "more verticals."
- **Distribution = one repo, contribute-back via PR.** Pickers (~70–80%) run a
  known-good tagged release; power users (~20–30%) branch, scaffold an overlay,
  and PR it back. CI gates every PR so `main` stays releasable.
- **Each SE uses their own Datadog org/sandbox** → no multi-tenant locking; we
  keep only `--upsert` idempotency for a clean demo→reset→redemo loop.

## Phases

### Phase 0 — Foundation, UI-first principle & repo hygiene — *in progress*
- [x] Codify UI-first in `CLAUDE.md` §0.6 + UI-first `README.md` quick-start
- [x] Consolidate `Makefile` front door; fix `.venv`→`.venv-ui`; rename `validate`→`validate-live`
- [x] Single-source version (`pyproject` dynamic ← `dd_demo_toolkit.__version__`)
- [x] This roadmap + capability→UI audit (below)
- [ ] Reconcile repo slug (open decision — below)

### Phase 1 — Dummy-simple for SEs (GA core) — *not started*
Validation framework (`dd_demo_toolkit/validation/`), `dd-demo validate` + pre-`setup`
gate, `doctor` preflight wired into `make ui`, upsert-by-name idempotency, UI
front-door endpoints (`/api/doctor`, `/api/validate`, `/api/coverage`) + streamed
per-resource progress, guided 1Password bootstrap, dev volume mount.

### Phase 2 — Product-coverage framework + contribute-back — *not started*
`products/catalog.yaml` + per-vertical `products.yaml`; a `ProductModule` abstraction
that makes `DD_DEMO_PRODUCTS` load-bearing; refactor `rum.py`/`llm_obs.py` to it;
`dd-demo coverage`; `dd-demo new-overlay` scaffolder; `CONTRIBUTING.md` / `CODEOWNERS` / PR template.

### Phase 3 — Automation: CI/CD via GitHub Actions — *not started*
`ci.yml` (lint / test / validate-verticals / compose-config / build — no secrets),
`release.yml` (tag → wheel + GHCR image + GitHub Release), `nightly-smoke.yml`
(live setup → metrics-present → teardown against a sandbox org). Pickers consume a
tagged release image/wheel via `docker-compose.release.yaml`.

### Phase 4 — Product roadmap to ~90% (post-GA, ongoing) — *not started*
Wave 1 parity (RUM / LLM Obs / DBM / Error Tracking across all verticals); Wave 2
simulatable (NPM, Profiling, Cloud Cost, Data Observability upload); Wave 3
container-heavy (CSM/CWS, ASM, CI Visibility). Each an independent PR scored by `dd-demo coverage`.

### Phase 5 — Cloud / AWS (stretch) — *not started*
ECS/Fargate simulator MVP (`terraform/envs/mvp-fargate` + `cloud-up`/`cloud-down`
`workflow_dispatch` via AWS OIDC), then a full EKS lift for the Sensing Hospital mock
app (Datadog Operator + RDS/ElastiCache), reusing `k8s/sensing-hospital/`.

## Capability → UI surface audit

Per [CLAUDE.md](CLAUDE.md) §0.6, every SE-facing capability must be reachable from
`make ui`. Current state and tracked gaps:

| Capability | UI surface today | Gap / lands in |
|---|---|---|
| Pick vertical + overlay | Configure tab ✓ | — |
| Choose products | Configure tab ✓ (captured, not yet load-bearing) | wired in Phase 2 |
| Set / verify credentials | Configure tab + Test connection ✓ | — |
| Start / stop simulator | Simulator tab ✓ | — |
| Deploy assets | Deploy tab ✓ | — |
| Teardown / teardown-all | Deploy tab ✓ | — |
| Status (containers + DD resources) | Status tab ✓ | — |
| Preflight / environment doctor | ✗ | Phase 1 — `/api/doctor` + Preflight card |
| Validate assets locally | ✗ | Phase 1 — `/api/validate` + Deploy-tab gate |
| Product coverage matrix | ✗ | Phase 2 — `/api/coverage` |
| Browse catalog (what each demo shows) | partial (dropdowns) | Phase 2 — richer catalog |
| Guided 1Password bootstrap | partial (cred entry; `op item create` is terminal) | Phase 1 |
| Scaffold a new overlay | ✗ (power-user; `dd-demo new-overlay`) | Phase 2 — at least a UI trigger |

## Open decisions

- **Repo slug / GA home.** `pyproject.toml [project.urls]` points at
  `github.com/DataDog/dd-demo-toolkit`, but the current git remote is
  `github.com/john-rath/Demo-Project`. Decide the GA repo home **before** wiring CI
  (GHCR image names, release tags, AWS OIDC trust). Mitigation: CI workflows will
  reference `${{ github.repository }}` to stay slug-agnostic; reconcile the
  `pyproject` URLs once the home is chosen.
