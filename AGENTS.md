# AGENTS.md — build & test guide for coding agents (Bits Code)

Operational instructions for AI coding agents (e.g. Datadog **Bits Code**)
working in this repository: how to install, test, lint, validate, and what
"done" looks like. For deep conventions see
[`dd-demo-toolkit/STYLE_GUIDE.md`](dd-demo-toolkit/STYLE_GUIDE.md) and
[`dd-demo-toolkit/CLAUDE.md`](dd-demo-toolkit/CLAUDE.md).

## Repository layout

This is a monorepo. The main project is the Python package **`dd-demo-toolkit/`**
(a config-driven Datadog demo framework). **Run all build/test/lint commands
from inside `dd-demo-toolkit/`.** CI lives at `.github/workflows/ci.yml`.

```
dd-demo-toolkit/
  dd_demo_toolkit/        # core engine, resource managers, simulator, validation
  dd_demo_toolkit_ui/     # FastAPI web UI (the SE front door)
  verticals/<name>/       # per-industry demo configs (+ overlays/)
  tests/                  # pytest suite (offline, hermetic)
  pyproject.toml          # deps, pytest/coverage config, lint config
```

## Environment & install

- Python **3.12**.
- Install (dev + UI extras): from `dd-demo-toolkit/`:
  ```bash
  python -m pip install --upgrade pip
  pip install -e '.[dev,ui]'
  ```

## Test

- Run the full suite from `dd-demo-toolkit/`:
  ```bash
  pytest
  ```
  This also writes `coverage.xml` (Cobertura) via the configured `addopts`.
- Tests must be **hermetic and offline** — never call the live Datadog API or
  require network/containers. Model new tests on the existing regression tests
  that use in-memory **fake API clients** and `tmp_path`:
  - `tests/test_dashboard_list_grouping.py`
  - `tests/test_monitor_teardown_synthetics.py`
  - `tests/test_fleet_location_distribution.py`
- Async tests use `pytest-asyncio` in **strict** mode — mark each with
  `@pytest.mark.asyncio` (an unmarked async test silently no-ops).
- Put new tests in `dd-demo-toolkit/tests/` named `test_*.py`. When you fix a
  bug, add a regression test that fails before and passes after.

## Lint & format (required — CI enforces)

From `dd-demo-toolkit/`:
```bash
black dd_demo_toolkit dd_demo_toolkit_ui          # auto-format (check: --check)
isort dd_demo_toolkit dd_demo_toolkit_ui          # imports   (check: --check-only)
flake8 dd_demo_toolkit dd_demo_toolkit_ui --max-line-length=100 --extend-ignore=E203,W503
```

## Validate demo assets

Any change under `verticals/` (dashboards, monitors, notebooks, SLOs,
workflows, plugins, overlays) must pass the asset validator with **0 errors**:
```bash
dd-demo validate --vertical <finance|healthcare|hospitality|insurance|manufacturing>
```
Before authoring or editing any such asset, follow
[`STYLE_GUIDE.md`](dd-demo-toolkit/STYLE_GUIDE.md) — every rule there traces to
a real demo bug. Highest-frequency rules:
- No percentile aggregators (`p95:`/`p99:`) on gauge metrics — use `avg:`/`max:`.
- Never invent new tag **keys**; use existing keys with new values.
- Monitor query alerts can't use `||`/`&&` — split into two monitors.
- Metric names must start with the vertical's `env_prefix` (e.g. `hospital.`).

## Guardrails (do not violate)

- **Secrets:** `.env` holds 1Password `op://` references, never plaintext keys.
  Never commit real secrets. CI reads `DD_API_KEY` from a GitHub Actions secret.
- **Docker images bake source at build time** — there's no live mount; changes
  under `verticals/`, `dd_demo_toolkit/`, or `docker/` require an image rebuild
  to take effect at runtime (not relevant for unit tests, which run locally).
- Keep changes minimal and focused; do not reformat unrelated files.

## Definition of done (for a PR)

1. `pytest` passes (from `dd-demo-toolkit/`).
2. `black --check`, `isort --check-only`, and `flake8` are clean.
3. If `verticals/` assets changed: `dd-demo validate --vertical <name>` is clean
   for each affected vertical.
4. New behavior / bug fix has a hermetic regression test.
5. No secrets, no new tag keys, no unrelated churn.
