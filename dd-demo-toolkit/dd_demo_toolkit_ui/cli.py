"""
`dd-demo-ui` console-script entrypoint.

Resolves paths, builds the FastAPI app via the factory in ``server.py``,
and hands it to uvicorn. Kept small on purpose — all the interesting
logic lives in ``server.py`` so the same app can be constructed from
tests.

Safety defaults:
  - Binds to 127.0.0.1 (loopback only) unless `--insecure-bind` is passed.
    This is the project-plan Risk-R4 mitigation in code form: even a
    careless `--host 0.0.0.0` invocation needs a second flag.
  - Refuses to start if the verticals dir doesn't exist.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import uvicorn

from .server import UIConfig, build_app

_DEFAULT_PORT = 8765  # arbitrary but memorable; avoids the usual 3000/8000/8080 clashes
_PACKAGE_DIR = Path(__file__).resolve().parent
_DEFAULT_STATIC_DIR = _PACKAGE_DIR / "static"


def _resolve_default_verticals_dir() -> Path:
    """Locate `verticals/` relative to the user's current working dir,
    with a fallback to the install-time location. Matches how `dd-demo`
    behaves so the two CLIs feel consistent.
    """
    cwd_candidate = Path.cwd() / "verticals"
    if cwd_candidate.exists():
        return cwd_candidate
    # Fallback: the verticals dir that shipped with this checkout, if any.
    # (`__file__` is inside `dd-demo-toolkit/dd_demo_toolkit_ui/`; verticals
    # lives at `dd-demo-toolkit/verticals/`.)
    install_candidate = _PACKAGE_DIR.parent / "verticals"
    if install_candidate.exists():
        return install_candidate
    return cwd_candidate  # let the validation error point at the cwd path


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="dd-demo-ui",
        description="Launch the dd-demo-toolkit web UI (single-user, local).",
    )
    p.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind address. Default 127.0.0.1 (loopback only). "
             "Pass --insecure-bind to allow non-loopback hosts.",
    )
    p.add_argument(
        "--port",
        type=int,
        default=_DEFAULT_PORT,
        help=f"TCP port. Default {_DEFAULT_PORT}.",
    )
    p.add_argument(
        "--verticals-dir",
        type=Path,
        default=None,
        help="Path to the toolkit's verticals/ directory. "
             "Defaults to ./verticals (cwd) or the bundled copy if missing.",
    )
    p.add_argument(
        "--env-path",
        type=Path,
        default=Path(".env"),
        help="Path to the .env file the UI reads/writes. Default ./.env",
    )
    p.add_argument(
        "--static-dir",
        type=Path,
        default=_DEFAULT_STATIC_DIR,
        help="Path to the static UI bundle. Default: the bundled vanilla UI.",
    )
    p.add_argument(
        "--insecure-bind",
        action="store_true",
        help="Allow binding to a non-loopback host (e.g. 0.0.0.0). "
             "Off by default; secrets travel over this port unencrypted.",
    )
    p.add_argument(
        "--no-gitignore-check",
        action="store_true",
        help="Skip the .gitignore guard when writing .env. "
             "Intended for use outside a git repo only.",
    )
    p.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable DEBUG logging.",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    log = logging.getLogger("dd-demo-ui")

    # Bind safety: refuse non-loopback without explicit opt-in.
    if args.host not in ("127.0.0.1", "localhost", "::1") and not args.insecure_bind:
        log.error(
            "Refusing to bind to %s without --insecure-bind. "
            "The UI handles Datadog API keys and has no auth; "
            "loopback-only is the safe default.",
            args.host,
        )
        return 2

    verticals_dir = (args.verticals_dir or _resolve_default_verticals_dir()).resolve()
    env_path = args.env_path.resolve()
    static_dir = args.static_dir.resolve()

    if not verticals_dir.exists():
        log.error(
            "verticals dir not found: %s. Run from the toolkit checkout "
            "or pass --verticals-dir.",
            verticals_dir,
        )
        return 2

    # project_dir is where `docker compose` runs from. It must contain
    # docker-compose.yaml and (typically) the .env file. We default it to
    # the directory holding the .env path the user passed, since that's
    # also where the toolkit's docker-compose.yaml lives.
    project_dir = env_path.parent.resolve()

    cfg = UIConfig(
        verticals_dir=verticals_dir,
        env_path=env_path,
        project_dir=project_dir,
        static_dir=static_dir,
        require_gitignore=not args.no_gitignore_check,
    )

    log.info("verticals dir: %s", cfg.verticals_dir)
    log.info("env path:      %s (exists=%s)", cfg.env_path, cfg.env_path.exists())
    log.info("project dir:   %s", cfg.project_dir)
    log.info("static dir:    %s", cfg.static_dir)
    log.info("listening on:  http://%s:%d", args.host, args.port)

    app = build_app(cfg)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
