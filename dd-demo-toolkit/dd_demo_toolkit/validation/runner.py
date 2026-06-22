"""
Discovery + orchestration for the local asset validator.

``validate_vertical`` walks a vertical (and an optional overlay), runs each
per-resource-type validator, and returns a flat list of Findings. No
credentials, no network — safe in CLI, the UI server, and CI.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

import yaml

from . import dashboards as _dashboards
from . import monitors as _monitors
from . import notebooks as _notebooks
from . import slos as _slos
from . import workflows as _workflows
from .core import Finding, Severity

ALL_RESOURCE_TYPES = ["monitors", "dashboards", "notebooks", "workflows", "slos"]

# resource_type -> (filename or None for the dashboards dir, validator module)
_YAML_VALIDATORS = {
    "monitors": ("monitors.yaml", _monitors),
    "slos": ("slos.yaml", _slos),
    "workflows": ("workflows.yaml", _workflows),
    "notebooks": ("notebooks.yaml", _notebooks),
}


def _env_prefix(vertical_dir: Path) -> Optional[str]:
    cfg = vertical_dir / "config.yaml"
    if not cfg.exists():
        return None
    try:
        data = yaml.safe_load(cfg.read_text()) or {}
        return (data.get("vertical") or {}).get("env_prefix")
    except (yaml.YAMLError, OSError):
        return None


def _rel(p: Path, root: Path) -> str:
    try:
        return str(Path(p).resolve().relative_to(root.resolve().parent))
    except (ValueError, OSError):
        return str(p)


def _validate_dir(base_dir: Path, env_prefix, resource_types, root: Path) -> List[Finding]:
    findings: List[Finding] = []
    for rtype, (fname, module) in _YAML_VALIDATORS.items():
        if rtype not in resource_types:
            continue
        f = base_dir / fname
        if f.exists():
            findings += module.validate(f, env_prefix, _rel(f, root))
    if "dashboards" in resource_types:
        d = base_dir / "dashboards"
        if d.is_dir():
            for jf in sorted(d.glob("*.json")):
                findings += _dashboards.validate(jf, env_prefix, _rel(jf, root))
    return findings


def validate_vertical(
    vertical: str,
    sub_vertical: Optional[str] = None,
    verticals_dir: str = "verticals",
    resource_types: Optional[List[str]] = None,
) -> List[Finding]:
    """Validate a vertical's assets (plus an optional overlay's).

    Overlays inherit the base vertical's ``env_prefix`` (STYLE_GUIDE §1.9/§3).
    """
    resource_types = resource_types or ALL_RESOURCE_TYPES
    root = Path(verticals_dir)
    base_dir = root / vertical
    if not base_dir.is_dir():
        return [Finding(Severity.ERROR, "DD000", "vertical",
                        f"Vertical '{vertical}' not found at {base_dir}.")]

    env_prefix = _env_prefix(base_dir)
    findings = _validate_dir(base_dir, env_prefix, resource_types, root)

    if sub_vertical:
        overlay_dir = base_dir / "overlays" / sub_vertical
        if overlay_dir.is_dir():
            findings += _validate_dir(overlay_dir, env_prefix, resource_types, root)
        else:
            findings.append(Finding(Severity.WARNING, "DD001", "vertical",
                f"Overlay '{sub_vertical}' has no resource directory at {overlay_dir} "
                "(config-only overlays are fine; nothing to lint).", resource_name=sub_vertical))
    return findings


def summarize(findings: List[Finding]) -> Dict[str, int]:
    out = {"errors": 0, "warnings": 0, "infos": 0}
    for f in findings:
        if f.severity == Severity.ERROR:
            out["errors"] += 1
        elif f.severity == Severity.WARNING:
            out["warnings"] += 1
        else:
            out["infos"] += 1
    return out


# --- text formatting (used by the CLI) ------------------------------------

_COLORS = {Severity.ERROR: "\033[31m", Severity.WARNING: "\033[33m", Severity.INFO: "\033[36m"}
_RESET = "\033[0m"
_TAG = {Severity.ERROR: "ERROR", Severity.WARNING: "WARN ", Severity.INFO: "INFO "}


def format_text(findings: List[Finding], use_color: bool = True) -> str:
    if not findings:
        return "✓ no issues found"
    lines: List[str] = []
    # Stable order: by file, then most-severe first, then rule id.
    for f in sorted(findings, key=lambda x: (x.file, -int(x.severity), x.rule_id)):
        tag = _TAG[f.severity]
        if use_color:
            tag = f"{_COLORS[f.severity]}{tag}{_RESET}"
        where = f.file + (f" · {f.location}" if f.location else "")
        ref = f" ({f.style_guide_ref})" if f.style_guide_ref else ""
        name = f" {f.resource_name}:" if f.resource_name else ""
        lines.append(f"  {tag} {f.rule_id} {where}{name} {f.message}{ref}")
    s = summarize(findings)
    lines.append("")
    lines.append(f"  {s['errors']} error(s), {s['warnings']} warning(s), {s['infos']} info")
    return "\n".join(lines)
