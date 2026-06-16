"""
FastAPI app for the dd-demo-toolkit visual layer.

Phase 1 endpoints (config + env):
  GET  /api/health
  GET  /api/verticals                    -> [{"name", "overlays": [...]}]
  GET  /api/verticals/{name}/overlays    -> ["bd", "quest", ...]
  GET  /api/sites                        -> ["datadoghq.com", ...]
  GET  /api/env                          -> {values, non_compliant_secret_keys}
  POST /api/env                          -> writes .env (rejects plain secrets)
  POST /api/env/test                     -> {ok, api_key_ok, app_key_ok, error}

Phase 2 + 3 endpoints (process control + log streaming):
  GET  /api/processes                    -> [{name, state, ...}]
  GET  /api/processes/{name}/status      -> single status dict
  POST /api/processes/{name}/start       -> start the named process
  POST /api/processes/{name}/stop        -> signal it to exit
  GET  /api/processes/{name}/logs        -> SSE stream of stdout lines

Plus a static mount at `/` that serves the (vanilla, no-build) UI from
``dd_demo_toolkit_ui/static/``.

Construction follows the FastAPI app-factory pattern (``build_app(...)``)
so that tests can hand a tmp_path-based ``UIConfig`` without monkeypatching
globals. The CLI entrypoint in ``cli.py`` builds the production app from
real paths and hands it to uvicorn.
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from dd_demo_toolkit.config import ConfigError, ConfigLoader
from dd_demo_toolkit.utils.dd_api import DatadogAPIClient

from . import env_manager
from .dd_validator import (
    ReferenceResolutionError,
    resolve_secret_reference,
    validate_credentials,
)
from .process_supervisor import (
    AlreadyRunningError,
    EnvNotResolvedError,
    NotRunningError,
    ProcessSupervisor,
    ProcessSupervisorError,
    UnknownProcessError,
)

logger = logging.getLogger(__name__)


# Catalog of Datadog products/features a demo can showcase. Surfaced by
# GET /api/products and rendered as a checkbox grid in the Configure tab.
# The user's selection is persisted to DD_DEMO_PRODUCTS (comma-separated)
# so the demo's intended scope is captured for setup and downstream asset
# filtering. The one product with a real container toggle today is
# Database Monitoring: selecting it also flips DD_DEMO_DBM (the frontend
# derives that from the selection), which `make up` reads to start the
# DBM stack. Other entries record intent for now.
#
# `default` marks the products pre-checked on first load (no DD_DEMO_PRODUCTS
# in .env yet) — the core observability story most demos open with.
PRODUCT_CATALOG: List[Dict[str, Any]] = [
    {"key": "apm", "label": "APM & Distributed Tracing", "group": "Core observability",
     "description": "Service traces, flame graphs, and the on-prem→cloud cascade map.", "default": True},
    {"key": "logs", "label": "Log Management", "group": "Core observability",
     "description": "Correlated service and container logs.", "default": True},
    {"key": "infra", "label": "Infrastructure Monitoring", "group": "Core observability",
     "description": "Host, container, and device fleet health.", "default": True},
    {"key": "dbm", "label": "Database Monitoring", "group": "Core observability",
     "description": "Postgres query performance. Also starts the DBM container stack.",
     "default": False, "drives_flag": "DD_DEMO_DBM"},
    {"key": "rum", "label": "Real User Monitoring", "group": "Digital experience",
     "description": "Frontend/web session performance and errors.", "default": False},
    {"key": "eud", "label": "End-User Devices (EuD)", "group": "Digital experience",
     "description": "Patient/clinician device experience — app launch, crashes, on-device network.", "default": True},
    {"key": "synthetics", "label": "Synthetic Monitoring", "group": "Digital experience",
     "description": "Scripted API and browser checks.", "default": False},
    {"key": "npm", "label": "Network Monitoring", "group": "Infrastructure",
     "description": "Network flows and device connectivity.", "default": False},
    {"key": "profiler", "label": "Continuous Profiler", "group": "Infrastructure",
     "description": "Code-level CPU/memory profiling.", "default": False},
    {"key": "dsm", "label": "Data Streams Monitoring", "group": "Infrastructure",
     "description": "Kafka/queue pipeline lag and throughput.", "default": False},
    {"key": "csm", "label": "Cloud Security Management", "group": "Security",
     "description": "Misconfig and runtime security signals.", "default": False},
    {"key": "llmobs", "label": "LLM Observability", "group": "AI",
     "description": "LLM app traces, evals, and quality.", "default": False},
    {"key": "bits", "label": "Bits AI / Watchdog", "group": "AI",
     "description": "AI-driven detection and root-cause isolation across the disjoint cascade.", "default": True},
]

# Keys that map a selected product to a real .env toggle the stack reads.
PRODUCT_DRIVEN_FLAGS: Dict[str, str] = {
    p["key"]: p["drives_flag"] for p in PRODUCT_CATALOG if p.get("drives_flag")
}


@dataclass(frozen=True)
class UIConfig:
    """Where the UI server looks on disk for toolkit state.

    These are explicit (not auto-discovered inside the server) so that:
      - tests can point at a tmp_path,
      - the CLI can resolve them once at startup and log them clearly,
      - a packaged-install user can override via flags without env var games.
    """
    verticals_dir: Path
    env_path: Path
    # Directory the `docker compose` invocations run from. Must contain
    # docker-compose.yaml and (typically) .env. Defaults in the CLI to
    # env_path.parent.
    project_dir: Path
    # Where to serve static files from. Defaults to the bundled vanilla
    # UI under this package. Phase 1.5 will switch the default to a
    # `web/dist/` produced by `npm run build`.
    static_dir: Path
    # When False, the .gitignore guard in env_manager is bypassed. ONLY
    # set this when env_path is under a tmp_path with no git context — the
    # CLI always sets it True. Tests flip it off so they don't have to
    # build a fake .gitignore tree.
    require_gitignore: bool = True
    # If False, the process-supervisor endpoints aren't mounted. Tests
    # turn this off when they want to exercise the Phase 1 surface
    # without spinning up docker. The CLI always sets it True.
    enable_supervisor: bool = True


# --- Pydantic request models -------------------------------------------------


class EnvWriteRequest(BaseModel):
    """Body for POST /api/env.

    All fields are optional; only those present in the request are written.
    Use ``env_manager.KEEP_EXISTING`` as a value to preserve the on-disk
    value for a masked secret (the UI does this automatically when the
    user leaves a secret field at its masked display value).
    """
    DD_API_KEY: Optional[str] = None
    DD_APP_KEY: Optional[str] = None
    DD_SITE: Optional[str] = None
    DD_DEMO_VERTICAL: Optional[str] = None
    DD_DEMO_SUB_VERTICAL: Optional[str] = None
    DD_DEMO_PRODUCTS: Optional[str] = None
    DD_DEMO_DBM: Optional[str] = None
    EMIT_INTERVAL: Optional[str] = None
    DISPLAY_NAME: Optional[str] = None
    OTEL_EXPORTER_OTLP_ENDPOINT: Optional[str] = None
    OTEL_EXPORTER_OTLP_PROTOCOL: Optional[str] = None


class EnvTestRequest(BaseModel):
    """Body for POST /api/env/test.

    Caller may pass ``KEEP_EXISTING`` for either secret to mean "use what's
    currently on disk." That's how the UI lets the user click "Test
    connection" without re-typing keys after a page reload.
    """
    DD_API_KEY: str = Field(..., min_length=1)
    DD_APP_KEY: str = Field(..., min_length=1)
    DD_SITE: str = Field(..., min_length=1)


# --- App factory -------------------------------------------------------------


def build_app(cfg: UIConfig) -> FastAPI:
    """Construct a FastAPI app bound to the given UIConfig.

    The config is captured in the closures of the route handlers. We
    deliberately do NOT stash it on `app.state` — that pattern makes the
    config implicitly mutable from anywhere and we've been bitten by that
    in other Python web codebases. Closure + frozen dataclass = no
    accidental rewrites.
    """
    supervisor: Optional[ProcessSupervisor] = None
    if cfg.enable_supervisor:
        supervisor = ProcessSupervisor(project_dir=cfg.project_dir)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        # Startup: nothing to do (supervisor is lazy — handles created
        # on first start/status call).
        yield
        # Shutdown: politely stop any running children so the docker
        # stack doesn't dangle when the user Ctrl-Cs the UI server.
        if supervisor is not None:
            await supervisor.shutdown()

    app = FastAPI(
        title="dd-demo-toolkit UI",
        version="0.1.0",
        lifespan=lifespan,
        # We bind to 127.0.0.1 by default in cli.py, so CORS isn't a
        # security concern. We still leave it off (no CORSMiddleware) so
        # accidental remote use surfaces as an obvious browser error
        # rather than silently working. Phase 5+ may revisit.
    )

    config_loader = ConfigLoader(str(cfg.verticals_dir))

    # ----- Health & metadata -------------------------------------------------

    @app.get("/api/health")
    def health() -> Dict[str, Any]:
        return {
            "ok": True,
            "version": "0.1.0",
            "verticals_dir": str(cfg.verticals_dir),
            "env_path": str(cfg.env_path),
            "env_exists": cfg.env_path.exists(),
        }

    @app.get("/api/sites")
    def sites() -> List[str]:
        # Sorted for stable UI rendering. Reuses the toolkit's authoritative
        # map so a new region added to the toolkit shows up here automatically.
        return sorted(DatadogAPIClient.SITE_MAPPING.keys())

    @app.get("/api/products")
    def products() -> List[Dict[str, Any]]:
        """Catalog of demonstrable Datadog products for the checkbox picker.

        Static catalog (see PRODUCT_CATALOG). The frontend renders these as
        a grouped checkbox grid, pre-checks `default: true` entries when
        DD_DEMO_PRODUCTS is unset, and persists the selection back to
        DD_DEMO_PRODUCTS on save.
        """
        return PRODUCT_CATALOG

    # ----- Verticals & overlays ---------------------------------------------

    @app.get("/api/verticals")
    def list_verticals() -> List[Dict[str, Any]]:
        """List verticals with their overlay names + display_name.

        We pre-join overlays here so the UI can render its two-step
        dropdown (vertical → overlay) without a second round-trip per
        vertical. Loading config.yaml for each just to grab display_name
        is cheap (single-digit ms for 5 verticals) and keeps the UI
        responsive.
        """
        result: List[Dict[str, Any]] = []
        for name in config_loader.list_verticals():
            display_name = name  # fallback
            try:
                cfg_yaml = config_loader.load_vertical(name)
                display_name = cfg_yaml.get("vertical", {}).get("display_name", name)
            except ConfigError as e:
                # Don't crash the list because one vertical's YAML is broken.
                # Surface it via a marker so the UI can flag it instead.
                logger.warning("failed to load %s/config.yaml: %s", name, e)
                display_name = f"{name} (config error)"
            result.append({
                "name": name,
                "display_name": display_name,
                "overlays": config_loader.list_overlays(name),
            })
        return result

    @app.get("/api/verticals/{name}/overlays")
    def list_overlays(name: str) -> List[str]:
        # Validate the vertical actually exists so a typo returns 404 instead
        # of an empty list (which would mask the bug).
        if name not in config_loader.list_verticals():
            raise HTTPException(status_code=404, detail=f"unknown vertical: {name}")
        return config_loader.list_overlays(name)

    # ----- .env management ---------------------------------------------------

    @app.get("/api/env")
    def get_env() -> Dict[str, Any]:
        """Return the current `.env` with secrets masked + a compliance list.

        Response shape:
            {
              "values": {DD_API_KEY: "op://..." or "*****abcd", ...},
              "non_compliant_secret_keys": ["DD_API_KEY", ...]  # plain values
                  in .env that should be migrated to op:// references
            }

        ``non_compliant_secret_keys`` powers the migration banner in the
        UI. Empty list means everything's compliant (or unset).
        """
        return {
            "values": env_manager.read_env(cfg.env_path, mask=True),
            "non_compliant_secret_keys":
                env_manager.non_compliant_secret_keys(cfg.env_path),
        }

    @app.post("/api/env")
    def post_env(req: EnvWriteRequest) -> Dict[str, Any]:
        # Drop fields the caller didn't include. Pydantic gives us None
        # for omitted Optional fields; we treat None as "don't touch".
        incoming = {k: v for k, v in req.model_dump().items() if v is not None}
        try:
            env_manager.write_env(
                cfg.env_path,
                incoming,
                require_gitignore=cfg.require_gitignore,
            )
        except env_manager.PlainSecretRejected as e:
            # Policy violation: plain secret. 400 with the verbatim
            # message so the UI can surface "use op://..." guidance.
            raise HTTPException(status_code=400, detail=str(e))
        except ValueError as e:
            # gitignore guard / unmanaged-key guard. 400, not 500: it's a
            # client problem the user can fix.
            raise HTTPException(status_code=400, detail=str(e))
        # Same envelope shape as GET — keeps the frontend simple.
        return {
            "values": env_manager.read_env(cfg.env_path, mask=True),
            "non_compliant_secret_keys":
                env_manager.non_compliant_secret_keys(cfg.env_path),
        }

    @app.post("/api/env/test")
    def post_env_test(req: EnvTestRequest) -> Dict[str, Any]:
        """Validate credentials against Datadog.

        Resolution order for each secret field:
          1. ``KEEP_EXISTING`` → read the on-disk value (which may itself
             be a reference; see step 3).
          2. A plain string (transitional case) → use as-is.
          3. A secret reference (``op://...``) → shell out via
             ``resolve_secret_reference`` to get the real value.

        The resolved plain value lives only on the stack for the duration
        of this handler — it never gets written, logged, or returned in
        the response.
        """
        on_disk = env_manager.read_env(cfg.env_path, mask=False)

        def _resolve(value: str, key: str) -> str:
            if value == env_manager.KEEP_EXISTING:
                v = on_disk.get(key)
                if not v:
                    raise HTTPException(
                        status_code=400,
                        detail=f"{key} requested KEEP_EXISTING but no value on disk",
                    )
                value = v
            # Whether it came from the request or from disk, if it's a
            # reference, resolve it. resolve_secret_reference is a no-op
            # for plain values, so the transitional case still works.
            try:
                return resolve_secret_reference(value)
            except ReferenceResolutionError as e:
                raise HTTPException(
                    status_code=400,
                    detail=f"Could not resolve {key}: {e}",
                )

        api_key = _resolve(req.DD_API_KEY, "DD_API_KEY")
        app_key = _resolve(req.DD_APP_KEY, "DD_APP_KEY")
        site = req.DD_SITE  # site is never masked or a reference

        result = validate_credentials(api_key, app_key, site)
        return {
            "ok": result.ok,
            "api_key_ok": result.api_key_ok,
            "app_key_ok": result.app_key_ok,
            "error": result.error,
        }

    # ----- Process control (Phase 2 + 3) -------------------------------------

    def _require_supervisor() -> ProcessSupervisor:
        # build_app() may run with enable_supervisor=False for tests;
        # in that mode the routes below return 503 rather than 500
        # so callers know the feature is intentionally off.
        if supervisor is None:
            raise HTTPException(
                status_code=503,
                detail="process supervisor disabled in this UIConfig",
            )
        return supervisor

    def _supervisor_error_to_http(e: ProcessSupervisorError) -> HTTPException:
        # 404 for unknown process name; 409 for state conflicts (already
        # running, not running); 400 for env-not-resolved (user can fix
        # by relaunching via `make ui`); 500 for anything else.
        if isinstance(e, UnknownProcessError):
            return HTTPException(status_code=404, detail=str(e))
        if isinstance(e, (AlreadyRunningError, NotRunningError)):
            return HTTPException(status_code=409, detail=str(e))
        if isinstance(e, EnvNotResolvedError):
            return HTTPException(status_code=400, detail=str(e))
        return HTTPException(status_code=500, detail=str(e))

    @app.get("/api/processes")
    async def list_processes() -> List[Dict[str, Any]]:
        """Status of every named process the supervisor knows about.

        Reconciles long-running services against actual Docker state first,
        so the UI reflects containers started from the terminal or make targets.
        """
        sup = _require_supervisor()
        await sup.reconcile_long_running()
        return sup.status_all()

    @app.get("/api/processes/{name}/status")
    async def process_status(name: str) -> Dict[str, Any]:
        sup = _require_supervisor()
        try:
            await sup.reconcile(name)
            return sup.status(name)
        except ProcessSupervisorError as e:
            raise _supervisor_error_to_http(e)

    @app.post("/api/processes/{name}/start")
    async def process_start(name: str) -> Dict[str, Any]:
        sup = _require_supervisor()
        try:
            return await sup.start(name)
        except ProcessSupervisorError as e:
            raise _supervisor_error_to_http(e)

    @app.post("/api/processes/{name}/stop")
    async def process_stop(name: str) -> Dict[str, Any]:
        sup = _require_supervisor()
        try:
            return await sup.stop(name)
        except ProcessSupervisorError as e:
            raise _supervisor_error_to_http(e)

    @app.get("/api/processes/{name}/logs")
    async def process_logs(name: str, request: Request) -> StreamingResponse:
        """SSE stream of stdout lines for the named process.

        Replays the bounded backlog first, then streams live lines. Closes
        cleanly when the child process exits OR when the client disconnects
        (detected via request.is_disconnected()).

        Event shape: ``data: <line>\\n\\n`` per the SSE spec. Lines are
        JSON-escaped so embedded newlines / quotes don't break the format.
        """
        sup = _require_supervisor()
        try:
            sup._validate_name(name)  # raises if unknown
        except ProcessSupervisorError as e:
            raise _supervisor_error_to_http(e)

        async def event_stream() -> AsyncIterator[bytes]:
            # Send a comment frame on connect so any reverse proxy that
            # buffers output flushes its initial window. Browsers ignore
            # `:` comment lines per the SSE spec.
            yield b": connected\n\n"
            try:
                async for line in sup.subscribe_logs(name):
                    # Periodically check the client; otherwise a tab close
                    # leaves the generator hanging until the next line.
                    if await request.is_disconnected():
                        return
                    # JSON-escape via repr → quote-strip, since the only
                    # thing SSE forbids in `data:` payloads is a real
                    # newline. repr's output is safe ASCII.
                    escaped = (
                        line
                        .replace("\\", "\\\\")
                        .replace("\n", "\\n")
                        .replace("\r", "\\r")
                    )
                    yield f"data: {escaped}\n\n".encode("utf-8")
            except asyncio.CancelledError:
                # Client closed the connection.
                return
            # End-of-stream marker so the frontend knows the process exited.
            yield b"event: end\ndata: \n\n"

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                # Disable buffering on common proxies (nginx, etc).
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                # SSE clients reconnect automatically; tell them not to.
                # The new connection would replay the backlog anyway, so
                # auto-reconnect would just duplicate buffered lines.
                "Connection": "keep-alive",
            },
        )

    # ----- Status (Phase 4: live environment state) --------------------------

    @app.get("/api/status/containers")
    async def status_containers() -> Dict[str, Any]:
        """Real Docker container state via `docker compose ps`, regardless of who started them."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "compose", "ps", "--format", "json",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(cfg.project_dir),
            )
            stdout, stderr = await proc.communicate()
        except FileNotFoundError:
            return {"containers": [], "error": "docker not found on PATH"}

        if proc.returncode != 0:
            err = stderr.decode("utf-8", errors="replace").strip()
            return {"containers": [], "error": err or "docker compose ps failed"}

        containers: List[Dict[str, Any]] = []
        for line in stdout.decode("utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                containers.append({
                    "service": obj.get("Service", ""),
                    "name": obj.get("Name", ""),
                    "state": obj.get("State", ""),
                    "health": obj.get("Health", ""),
                    "status": obj.get("Status", ""),
                })
            except json.JSONDecodeError:
                pass

        return {"containers": containers, "error": None}

    @app.get("/api/status/datadog")
    async def status_datadog() -> Dict[str, Any]:
        """Counts of toolkit-managed resources currently deployed in Datadog.

        Filters by vertical tag where the API supports it (monitors, SLOs,
        workflows). Dashboards are matched by their description marker since
        the dashboards API doesn't return tags. Notebooks are matched by the
        team:dd-demo-* metadata tag injected at create time.

        Falls back to total org counts (unfiltered) for any resource type
        where tag-based filtering fails, so the UI always shows something
        useful even when the vertical isn't set.
        """
        def _fetch() -> Dict[str, Any]:
            try:
                client = DatadogAPIClient()
            except ValueError as e:
                return {
                    "monitors": None, "dashboards": None,
                    "notebooks": None, "slos": None, "workflows": None,
                    "vertical": None, "error": str(e),
                }

            # Use the vertical tag for server-side filtering where the API
            # supports it. Falls back to dd-demo-toolkit:true if unset.
            on_disk = env_manager.read_env(cfg.env_path, mask=False)
            vertical = on_disk.get("DD_DEMO_VERTICAL") or ""
            tag_filter = f"vertical:{vertical}" if vertical else "dd-demo-toolkit:true"

            counts: Dict[str, Any] = {"vertical": vertical or None}
            errors: List[str] = []

            # Monitors — API supports server-side tag filtering.
            try:
                resp = client.list_monitors(tag=tag_filter)
                counts["monitors"] = len(resp.get("monitors", []))
            except Exception as e:
                counts["monitors"] = None
                errors.append(f"monitors: {e}")

            # Dashboards — API doesn't return tags; match by description marker.
            try:
                resp = client.list_dashboards()
                all_dash = resp.get("dashboards", [])
                # Primary: description marker scoped to vertical.
                marker = f"[dd-demo-toolkit:{vertical}]" if vertical else "[dd-demo-toolkit:"
                counts["dashboards"] = sum(
                    1 for d in all_dash
                    if marker in (d.get("description") or "")
                )
                # Fallback: any dd-demo-toolkit marker (catches cross-vertical orphans).
                if counts["dashboards"] == 0 and vertical:
                    counts["dashboards"] = sum(
                        1 for d in all_dash
                        if "[dd-demo-toolkit:" in (d.get("description") or "")
                    )
            except Exception as e:
                counts["dashboards"] = None
                errors.append(f"dashboards: {e}")

            # Notebooks — API doesn't support our tag keys; match by the
            # team:dd-demo-* metadata tag injected at notebook create time.
            try:
                resp = client.list_notebooks()
                all_nb = resp.get("data", [])
                if vertical:
                    counts["notebooks"] = sum(
                        1 for n in all_nb
                        if f"team:dd-demo-{vertical}" in
                        (n.get("attributes", {}).get("metadata", {}).get("tags") or [])
                    )
                else:
                    counts["notebooks"] = sum(
                        1 for n in all_nb
                        if any(
                            t.startswith("team:dd-demo-")
                            for t in (n.get("attributes", {}).get("metadata", {}).get("tags") or [])
                        )
                    )
            except Exception as e:
                counts["notebooks"] = None
                errors.append(f"notebooks: {e}")

            # SLOs — filter by vertical tag in the tags array.
            try:
                resp = client._request("GET", "/api/v1/slo")
                all_slos = resp.get("data", [])
                counts["slos"] = sum(
                    1 for s in all_slos
                    if tag_filter in s.get("tags", [])
                )
            except Exception as e:
                counts["slos"] = None
                errors.append(f"slos: {e}")

            # Workflows — API supports server-side tag filtering.
            try:
                resp = client.list_workflows(tag_filter=tag_filter)
                counts["workflows"] = len(resp.get("data", []))
            except Exception as e:
                counts["workflows"] = None
                errors.append(f"workflows: {e}")

            counts["error"] = "; ".join(errors) if errors else None
            return counts

        return await asyncio.get_event_loop().run_in_executor(None, _fetch)

    # ----- Static UI ---------------------------------------------------------

    if cfg.static_dir.exists():
        # Mount the static dir. The trailing-slash route is so that `/`
        # hits index.html — StaticFiles can do this with `html=True`,
        # which also makes it serve index.html for 404s (SPA-style),
        # convenient when we swap to React.
        app.mount(
            "/",
            StaticFiles(directory=str(cfg.static_dir), html=True),
            name="static",
        )
    else:
        # No bundled UI: serve a minimal index that explains the situation.
        # Most useful during the React-build transition in Phase 1.5.
        @app.get("/")
        def fallback_index() -> FileResponse:  # pragma: no cover
            raise HTTPException(
                status_code=500,
                detail=f"static dir not found: {cfg.static_dir}",
            )

    return app
