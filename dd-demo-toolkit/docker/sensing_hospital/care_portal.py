"""care-portal — patient/clinician WEB FRONTEND (RUM target).

Serves a real browser page instrumented with the Datadog RUM Browser SDK so we
get Real User Monitoring (sessions, views, resources, actions, frontend errors)
in addition to backend APM. RUM credentials (applicationId, clientToken, site)
are injected at RUNTIME via /config.js from env — never baked into static HTML,
so no secret lands in the image or git.

The page calls same-origin /api/* on a timer; `allowedTracingUrls` makes RUM
inject trace headers into those calls, so a RUM session links to the backend
APM trace that flows care-portal → care-event-router → care-experience-platform
→ rtls-location-service. A Datadog Synthetic browser test (private location)
will drive this page to generate the traffic — see docker/sensing_hospital
README.

DD_SERVICE/DD_TAGS (deployment:cloud) come from docker-compose; the page is
auto-instrumented on the backend by dd-trace-py.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

import requests
from fastapi import FastAPI, Response
from fastapi.responses import FileResponse, JSONResponse

from metrics import statsd

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("care-portal")

ROUTER_URL = os.getenv("ROUTER_URL", "http://care-event-router:8080")
STATIC_DIR = Path(__file__).parent / "portal_static"
SESSION = requests.Session()

# RUM browser SDK is served per-region from Datadog's CDN. Map DD_SITE to the
# CDN region segment so the loader URL matches the org's site.
_SITE = os.getenv("DD_SITE", "datadoghq.com")
_SITE_TO_REGION = {
    "datadoghq.com": "us1",
    "us3.datadoghq.com": "us3",
    "us5.datadoghq.com": "us5",
    "datadoghq.eu": "eu1",
    "ap1.datadoghq.com": "ap1",
}
_CDN_REGION = _SITE_TO_REGION.get(_SITE, "us1")

app = FastAPI()


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/config.js")
def config_js() -> Response:
    """Runtime RUM config. Empty applicationId/clientToken simply means RUM
    stays dormant — the page still works — so the app runs even before the SE
    creates a RUM application and sets DD_RUM_APPLICATION_ID/DD_CLIENT_TOKEN."""
    app_id = os.getenv("DD_RUM_APPLICATION_ID", "")
    client_token = os.getenv("DD_CLIENT_TOKEN", "")
    service = os.getenv("DD_SERVICE", "care-portal")
    env = os.getenv("DD_ENV", "demo")
    version = os.getenv("DD_VERSION", "1.0.0")
    js = f"""window.DD_RUM_CONFIG = {{
  applicationId: {app_id!r},
  clientToken: {client_token!r},
  site: {_SITE!r},
  cdnRegion: {_CDN_REGION!r},
  service: {service!r},
  env: {env!r},
  version: {version!r}
}};"""
    return Response(content=js, media_type="application/javascript")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/ping")
def api_ping() -> JSONResponse:
    """Same-origin call the browser makes on a timer. Forwards a portal
    interaction event into the cascade so a RUM session links to the backend
    APM trace. Emits a custom metric for the frontend request rate."""
    statsd.increment("care.portal.requests_total")
    event = {"device_id": "care-portal", "device_type": "web_portal", "event": "portal_interaction"}
    try:
        r = SESSION.post(f"{ROUTER_URL}/events", json=event, timeout=12)
        return JSONResponse({"ok": True, "downstream": r.json()})
    except requests.RequestException as e:
        statsd.increment("care.portal.errors_total")
        return JSONResponse({"ok": False, "error": str(e)}, status_code=502)
