# dd-demo-toolkit visual layer

A local single-user web UI that wraps the dd-demo-toolkit CLI. See the
top-of-repo project plan for the full multi-phase roadmap; this README
describes what ships in **Phase 1**.

## What it does (Phase 1)

- Launches a local FastAPI server bound to `127.0.0.1`.
- Surfaces the toolkit's verticals and overlays via dropdowns
  (wraps `dd_demo_toolkit.config.ConfigLoader`).
- Generates / edits the toolkit's `.env` from a form:
  - Round-trip safe (comments, blank lines, and hand-edited custom
    variables survive).
  - Secrets are masked on read; written at file mode `0o600`.
  - Refuses to write if `.env` isn't covered by `.gitignore`.
- Validates Datadog API + APP keys via the public `/api/v1/validate`
  and `/api/v1/api_key` endpoints. The common failure mode — pasting
  the API key into the app key field — gets its own distinct error.

The Phase 1 UI is vanilla HTML/CSS/JS (no build step). Phase 1.5 swaps
it for a Vite-built React bundle without changing the backend contract.

## What it does NOT do (yet)

- Start / stop the simulator. *(Phase 2)*
- Deploy or tear down assets. *(Phase 3)*
- Tune throughput, volume curves, or custom tags. *(Phase 4 — also requires
  engine-side changes.)*
- Scaffold new sub-verticals or overlays. *(Phase 5)*

## Install

```bash
pip install -e '.[ui]'         # editable, with UI deps
# or, from a built wheel:
pip install 'dd-demo-toolkit[ui]'
```

## Run

```bash
cd dd-demo-toolkit/
dd-demo-ui                     # binds to 127.0.0.1:8765
# open http://127.0.0.1:8765
```

Useful flags:

| Flag | Default | Purpose |
|------|---------|---------|
| `--port` | `8765` | Override the listening port. |
| `--verticals-dir <path>` | `./verticals` or bundled copy | Toolkit verticals tree. |
| `--env-path <path>` | `./.env` | Where to read / write the `.env`. |
| `--insecure-bind` | off | Required to bind to anything other than loopback. The UI has no auth; secrets travel unencrypted over the bind address. |
| `--no-gitignore-check` | off | Skip the `.gitignore` guard when writing `.env`. Use only when running outside a git repo. |
| `-v` | off | DEBUG-level logging. |

Also runnable as `python -m dd_demo_toolkit_ui`.

## Architecture (Phase 1)

```
Browser (vanilla HTML/JS) ──HTTP──▶  FastAPI app (server.py)
                                           │
                                           │  imports
                                           ▼
                                  ConfigLoader (dd_demo_toolkit)
                                  env_manager (read/write .env)
                                  dd_validator (Datadog API check)
```

Endpoints (all under `/api/`):

| Method | Path | Purpose |
|--------|------|---------|
| `GET`  | `/api/health` | Liveness + path echo. |
| `GET`  | `/api/sites` | Sorted list of known Datadog sites (from `DatadogAPIClient.SITE_MAPPING`). |
| `GET`  | `/api/verticals` | All verticals with `display_name` and overlay names pre-joined. |
| `GET`  | `/api/verticals/{name}/overlays` | Overlays for a single vertical. 404 on unknown vertical. |
| `GET`  | `/api/env` | Current `.env` with secrets masked. |
| `POST` | `/api/env` | Update `.env`. See "KEEP_EXISTING" below. |
| `POST` | `/api/env/test` | Validate credentials against Datadog. |

### `KEEP_EXISTING` sentinel

The UI never sees unmasked secrets. When the user clicks Save without
re-typing the API key, the frontend posts the literal string
`__DD_DEMO_UI_KEEP_EXISTING__` (exported as `env_manager.KEEP_EXISTING`)
for that field. The backend resolves it from the on-disk file and
leaves the value untouched.

The same sentinel is honored by `POST /api/env/test` so the user can
test a saved configuration without re-entering keys after a page reload.

## Tests

```bash
pip install -e '.[dev]'
pytest tests/test_ui_*.py
```

The full test suite has no network calls — the `dd_validator` tests mock
`requests.get`. Tests cover the masking contract, round-trip preservation
of hand-edited keys, the `0o600` file mode, the `.gitignore` guard, and
the `KEEP_EXISTING` flow end-to-end through the FastAPI handlers.
