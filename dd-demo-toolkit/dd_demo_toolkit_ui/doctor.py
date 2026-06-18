"""
Preflight checks for the dd-demo-toolkit front door.

`make ui` runs this before launching so a new SE is told *exactly* what's
missing — `op` not signed in, Docker down, the UI port busy, `.env`
absent/non-compliant, no vertical selected, bad credentials, assets that won't
deploy — each with a copy-paste fix. The UI's Configure tab renders the same
results via ``GET /api/doctor`` (server.py imports ``run_doctor``).

Design notes:
  - **Degrades gracefully.** A missing `op`/Docker is *diagnosed*, never a
    crash — so the doctor can tell you to install the very thing it needs.
  - **Importable engine + thin __main__.** `run_doctor()` returns dataclasses
    the UI serializes; ``python -m dd_demo_toolkit_ui.doctor`` is what the
    Makefile calls.
  - **`--quick` skips the one network check** (credentials) so `make ui` stays
    snappy; `make doctor` and the UI run the full set.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import List, Optional

DEFAULT_PORT = 8765


@dataclass
class Check:
    name: str
    ok: bool
    level: str = "error"  # "error" blocks the stack · "warn" · "info"
    detail: str = ""
    fix: str = ""
    skipped: bool = False  # a dependency was missing, so the check couldn't run

    def as_dict(self) -> dict:
        return asdict(self)


def _run(cmd: List[str], timeout: float = 8.0):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, ((r.stdout or "") + (r.stderr or "")).strip()
    except FileNotFoundError:
        return 127, "not found"
    except subprocess.TimeoutExpired:
        return 124, "timed out"


def _env_value(env_path: str, key: str) -> Optional[str]:
    """Read a single env value: process env first, then the .env file."""
    val = os.environ.get(key)
    if val:
        return val
    p = Path(env_path)
    if not p.exists():
        return None
    try:
        from .env_manager import read_env
        return read_env(p, mask=False).get(key)
    except Exception:
        return None


def check_op_installed() -> Check:
    if shutil.which("op"):
        return Check("1Password CLI installed", True)
    return Check("1Password CLI installed", False,
                 detail="`op` not found on PATH",
                 fix="brew install --cask 1password-cli")


def check_op_authed() -> Check:
    if shutil.which("op") is None:
        return Check("1Password CLI signed in", False, skipped=True,
                     detail="skipped — `op` not installed")
    code, _ = _run(["op", "vault", "list"])
    if code == 0:
        return Check("1Password CLI signed in", True)
    return Check("1Password CLI signed in", False,
                 detail="`op vault list` failed — not authenticated",
                 fix='unlock the 1Password desktop app, or run: eval "$(op signin)"')


def check_docker() -> Check:
    if shutil.which("docker") is None:
        return Check("Docker running", False,
                     detail="`docker` not found on PATH",
                     fix="install Docker Desktop and start it")
    code, _ = _run(["docker", "info"])
    if code == 0:
        return Check("Docker running", True)
    return Check("Docker running", False,
                 detail="`docker info` failed", fix="start Docker Desktop")


def check_compose() -> Check:
    if shutil.which("docker") is None:
        return Check("docker compose available", False, skipped=True,
                     detail="skipped — docker not installed")
    code, _ = _run(["docker", "compose", "version"])
    if code == 0:
        return Check("docker compose available", True)
    return Check("docker compose available", False,
                 detail="`docker compose version` failed",
                 fix="update Docker Desktop (needs compose v2)")


def check_port_free(port: int = DEFAULT_PORT) -> Check:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", port))
        free = True
    except OSError:
        free = False
    finally:
        s.close()
    if free:
        return Check(f"UI port {port} free", True, level="warn")
    return Check(f"UI port {port} free", False, level="warn",
                 detail=f"127.0.0.1:{port} is in use (the UI may already be running)",
                 fix=f"stop the other process, or launch the UI with --port")


def check_env_present(env_path: str) -> Check:
    if Path(env_path).exists():
        return Check(".env present", True, level="warn")
    return Check(".env present", False, level="warn",
                 detail=f"{env_path} not found",
                 fix="cp .env.template .env  (then set your op:// refs + vertical)")


def check_env_compliant(env_path: str) -> Check:
    p = Path(env_path)
    if not p.exists():
        return Check(".env secrets compliant", False, level="warn", skipped=True,
                     detail="skipped — no .env yet")
    try:
        from .env_manager import non_compliant_secret_keys
        bad = non_compliant_secret_keys(p)
    except Exception as e:
        return Check(".env secrets compliant", False, level="warn", skipped=True,
                     detail=f"could not parse .env: {e}")
    if not bad:
        return Check(".env secrets compliant", True, level="warn")
    return Check(".env secrets compliant", False, level="warn",
                 detail=f"plain secrets on disk for: {', '.join(bad)}",
                 fix="store them in 1Password and use op:// refs — run `make migrate-secrets`")


def check_vertical(env_path: str, verticals_dir: str) -> Check:
    vertical = _env_value(env_path, "DD_DEMO_VERTICAL")
    if not vertical:
        return Check("Vertical selected", False, level="warn",
                     detail="DD_DEMO_VERTICAL not set",
                     fix="pick one in the UI, or set DD_DEMO_VERTICAL in .env (see `dd-demo list`)")
    sub = _env_value(env_path, "DD_DEMO_SUB_VERTICAL")
    try:
        from dd_demo_toolkit.config import ConfigError, ConfigLoader
        ConfigLoader(verticals_dir).load_vertical(vertical, sub_vertical=sub or None)
    except ConfigError as e:
        return Check("Vertical selected", False, level="warn",
                     detail=f"vertical '{vertical}' failed to load: {e}",
                     fix="check the vertical/overlay name (`dd-demo list`)")
    except Exception as e:  # config import or unexpected
        return Check("Vertical selected", False, level="warn", skipped=True,
                     detail=f"could not load config: {e}")
    label = vertical + (f" + {sub}" if sub else "")
    return Check("Vertical selected", True, level="warn", detail=f"{label} loads OK")


def check_credentials(env_path: str) -> Check:
    """Best-effort LIVE credential check (the only network check)."""
    if not Path(env_path).exists():
        return Check("Datadog credentials valid", False, level="warn", skipped=True,
                     detail="skipped — no .env")
    api_ref = _env_value(env_path, "DD_API_KEY") or ""
    app_ref = _env_value(env_path, "DD_APP_KEY") or ""
    site = _env_value(env_path, "DD_SITE") or "datadoghq.com"
    if not api_ref or not app_ref:
        return Check("Datadog credentials valid", False, level="warn", skipped=True,
                     detail="skipped — DD_API_KEY/DD_APP_KEY not set",
                     fix="set them (op:// refs) in the UI Configure tab")
    try:
        from . import dd_validator
        api = dd_validator.resolve_secret_reference(api_ref)
        app = dd_validator.resolve_secret_reference(app_ref)
    except Exception as e:
        return Check("Datadog credentials valid", False, level="warn",
                     detail=f"could not resolve op:// reference: {e}",
                     fix='sign in to 1Password: eval "$(op signin)"')
    res = dd_validator.validate_credentials(api, app, site)
    if res.ok:
        return Check("Datadog credentials valid", True, level="warn",
                     detail=f"verified against {site}")
    return Check("Datadog credentials valid", False, level="warn",
                 detail=res.error or "validation failed",
                 fix="check DD_API_KEY / DD_APP_KEY / DD_SITE")


def check_lint(env_path: str, verticals_dir: str) -> Check:
    """Summarize local asset validation for the selected vertical."""
    vertical = _env_value(env_path, "DD_DEMO_VERTICAL")
    if not vertical:
        return Check("Assets validate", False, level="info", skipped=True,
                     detail="skipped — no vertical selected")
    sub = _env_value(env_path, "DD_DEMO_SUB_VERTICAL")
    try:
        from dd_demo_toolkit.validation import summarize, validate_vertical
        s = summarize(validate_vertical(vertical, sub_vertical=sub or None,
                                        verticals_dir=verticals_dir))
    except Exception as e:
        return Check("Assets validate", False, level="info", skipped=True,
                     detail=f"validation unavailable: {e}")
    cmd = f"dd-demo validate --vertical {vertical}" + (f" --sub-vertical {sub}" if sub else "")
    if s["errors"]:
        return Check("Assets validate", False, level="error",
                     detail=f"{s['errors']} error(s), {s['warnings']} warning(s)",
                     fix=f"run `{cmd}`")
    return Check("Assets validate", True, level="info",
                 detail=f"0 errors, {s['warnings']} warning(s)")


def run_doctor(
    env_path: str = ".env",
    verticals_dir: str = "verticals",
    port: Optional[int] = DEFAULT_PORT,
    quick: bool = False,
) -> List[Check]:
    """Run all preflight checks in order.

    ``quick`` skips the network credential check (used by `make ui` to stay
    snappy). ``port=None`` skips the port-free check (used by the in-UI
    ``/api/doctor`` endpoint, where the port is obviously in use — by the UI
    itself).
    """
    checks = [
        check_op_installed(),
        check_op_authed(),
        check_docker(),
        check_compose(),
    ]
    if port is not None:
        checks.append(check_port_free(port))
    checks += [
        check_env_present(env_path),
        check_env_compliant(env_path),
        check_vertical(env_path, verticals_dir),
    ]
    if quick:
        checks.append(Check("Datadog credentials valid", False, level="warn",
                            skipped=True, detail="skipped (--quick)"))
    else:
        checks.append(check_credentials(env_path))
    checks.append(check_lint(env_path, verticals_dir))
    return checks


def has_blocking(checks: List[Check]) -> bool:
    return any((not c.ok and not c.skipped and c.level == "error") for c in checks)


# --- CLI / Makefile entry --------------------------------------------------

_C = {"ok": "\033[32m", "err": "\033[31m", "warn": "\033[33m",
      "dim": "\033[2m", "reset": "\033[0m"}


def _print_human(checks: List[Check], use_color: bool = True) -> None:
    def col(k: str) -> str:
        return _C[k] if use_color else ""

    reset = _C["reset"] if use_color else ""
    print("\n  dd-demo-toolkit preflight")
    print("  " + "-" * 50)
    for ch in checks:
        if ch.skipped:
            sym, c = "–", "dim"
        elif ch.ok:
            sym, c = "✓", "ok"
        else:
            sym, c = ("✗", "err") if ch.level == "error" else ("!", "warn")
        line = f"  {col(c)}{sym}{reset} {ch.name}"
        if ch.detail:
            line += f"  {col('dim')}{ch.detail}{reset}"
        print(line)
        if not ch.ok and not ch.skipped and ch.fix:
            print(f"      {col('dim')}→ {ch.fix}{reset}")
    blocking = [c for c in checks if not c.ok and not c.skipped and c.level == "error"]
    if blocking:
        print(f"\n  {col('err')}{len(blocking)} blocking issue(s).{reset} "
              "Fix the ✗ items above, then re-run.\n")
    else:
        print(f"\n  {col('ok')}Preflight OK.{reset}\n")


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(
        prog="dd-demo doctor",
        description="Preflight checks for the dd-demo-toolkit front door.",
    )
    p.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument("--env-path", default=".env")
    p.add_argument("--verticals-dir", default="verticals")
    p.add_argument("--quick", action="store_true",
                   help="Skip the network credential check (faster).")
    p.add_argument("--soft", action="store_true",
                   help="Always exit 0 (advisory mode — used by `make ui`).")
    args = p.parse_args(argv)

    checks = run_doctor(env_path=args.env_path, verticals_dir=args.verticals_dir,
                        port=args.port, quick=args.quick)
    if args.json:
        print(json.dumps([c.as_dict() for c in checks], indent=2))
    else:
        _print_human(checks, use_color=sys.stdout.isatty())

    if args.soft:
        return 0
    return 1 if has_blocking(checks) else 0


if __name__ == "__main__":
    sys.exit(main())
