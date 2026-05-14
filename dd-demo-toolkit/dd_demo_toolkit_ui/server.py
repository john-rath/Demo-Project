"""
FastAPI app for the dd-demo-toolkit visual layer.

Phase 1 endpoints:
  GET  /api/health
  GET  /api/verticals                    -> [{"name", "overlays": [...]}]
  GET  /api/verticals/{name}/overlays    -> ["bd", "quest", ...]
  GET  /api/sites                        -> ["datadoghq.com", ...]
  GET  /api/env                          -> {DD_API_KEY: "****abcd", ...}
  POST /api/env                          -> writes .env, returns the new
                                            masked state. Body values may
                                            be the literal sentinel
                                            ``KEEP_EXISTING`` for masked
                                            fields.
  POST /api/env/test                     -> {ok, api_key_ok, app_key_ok, error}

Plus a static mount at `/` that serves the (vanilla, no-build) UI from
``dd_demo_toolkit_ui/static/``. Phase 1.5 will swap that for a Vite-built
React bundle; nothing else changes.

Construction follows the FastAPI app-factory pattern (``build_app(...)``)
so that tests can hand a tmp_path-based ``UIConfig`` without monkeypatching
globals. The CLI entrypoint in ``cli.py`` builds the production app from
real paths and hands it to uvicorn.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from dd_demo_toolkit.config import ConfigError, ConfigLoader
from dd_demo_toolkit.utils.dd_api import DatadogAPIClient

from . import env_manager
from .dd_validator import validate_credentials

logger = logging.getLogger(__name__)


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
    # Where to serve static files from. Defaults to the bundled vanilla
    # UI under this package. Phase 1.5 will switch the default to a
    # `web/dist/` produced by `npm run build`.
    static_dir: Path
    # When False, the .gitignore guard in env_manager is bypassed. ONLY
    # set this when env_path is under a tmp_path with no git context — the
    # CLI always sets it True. Tests flip it off so they don't have to
    # build a fake .gitignore tree.
    require_gitignore: bool = True


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
    app = FastAPI(
        title="dd-demo-toolkit UI",
        version="0.1.0",
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
    def get_env() -> Dict[str, str]:
        """Return the current `.env` contents with secrets masked."""
        return env_manager.read_env(cfg.env_path, mask=True)

    @app.post("/api/env")
    def post_env(req: EnvWriteRequest) -> Dict[str, str]:
        # Drop fields the caller didn't include. Pydantic gives us None
        # for omitted Optional fields; we treat None as "don't touch".
        incoming = {k: v for k, v in req.model_dump().items() if v is not None}
        try:
            env_manager.write_env(
                cfg.env_path,
                incoming,
                require_gitignore=cfg.require_gitignore,
            )
        except ValueError as e:
            # gitignore guard / unmanaged-key guard. 400, not 500: it's a
            # client problem the user can fix.
            raise HTTPException(status_code=400, detail=str(e))
        return env_manager.read_env(cfg.env_path, mask=True)

    @app.post("/api/env/test")
    def post_env_test(req: EnvTestRequest) -> Dict[str, Any]:
        """Validate credentials against Datadog.

        Resolves ``KEEP_EXISTING`` sentinels from the on-disk `.env` before
        calling out. That's the only context in which the server reads the
        unmasked secrets — and the unmasked value never leaves this handler.
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
                return v
            return value

        api_key = _resolve(req.DD_API_KEY, "DD_API_KEY")
        app_key = _resolve(req.DD_APP_KEY, "DD_APP_KEY")
        site = req.DD_SITE  # site is never masked, so no resolve needed

        result = validate_credentials(api_key, app_key, site)
        return {
            "ok": result.ok,
            "api_key_ok": result.api_key_ok,
            "app_key_ok": result.app_key_ok,
            "error": result.error,
        }

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
