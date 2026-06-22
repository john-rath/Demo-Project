"""Validator for ``workflows.yaml`` (STYLE_GUIDE §1.7/§7.x; WORKFLOW_ACTIONS.md)."""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Set

import yaml

from .core import Finding, Severity
from .tags import check_tags

_REQUIRED = ["name", "description", "trigger", "steps"]
_MAX_DESCRIPTION = 300


def _verified_action_types() -> Set[str]:
    """The verified action-ID catalog — single source of truth lives in the
    resource manager. Imported lazily so the validator core stays light and
    never hard-fails if that import chain changes."""
    try:
        from dd_demo_toolkit.resources.workflows import _TYPE_TO_ACTION_ID
        return set(_TYPE_TO_ACTION_ID)
    except Exception:
        return set()


def _load(path: Path) -> list:
    with open(path) as f:
        config = yaml.safe_load(f)
    if not config:
        return []
    return config if isinstance(config, list) else config.get("workflows", []) or []


def validate(path, env_prefix: Optional[str] = None, rel: Optional[str] = None) -> List[Finding]:
    rel = rel or str(path)
    try:
        workflows = _load(Path(path))
    except yaml.YAMLError as e:
        return [Finding(Severity.ERROR, "DDW000", "workflow", f"YAML parse error: {e}", file=rel)]

    verified = _verified_action_types()
    findings: List[Finding] = []
    for idx, wf in enumerate(workflows):
        if not isinstance(wf, dict):
            continue
        name = wf.get("name", f"workflow[{idx}]")

        for field in _REQUIRED:
            if field not in wf:
                findings.append(Finding(Severity.ERROR, "DDW004", "workflow",
                                        f"Missing required field '{field}'.", "§7.1", name, rel))

        desc = wf.get("description", "")
        if isinstance(desc, str) and len(desc) > _MAX_DESCRIPTION:
            findings.append(Finding(Severity.ERROR, "DDW001", "workflow",
                f"Description is {len(desc)} chars (>{_MAX_DESCRIPTION} limit) → 400 on deploy. "
                "Move the narrative to the monitor message or a notebook.", "§1.7", name, rel))

        # Pre-built specs are validated server-side; only lint declarative steps.
        if "spec" not in wf:
            for step in wf.get("steps", []) or []:
                if not isinstance(step, dict) or step.get("action_id"):
                    continue
                stype = step.get("type")
                if stype not in verified:
                    sname = step.get("name", "?")
                    findings.append(Finding(Severity.WARNING, "DDW002", "workflow",
                        f"Step type '{stype}' is not in the verified action-ID catalog — it will "
                        "deploy as a no-op. Add it to _TYPE_TO_ACTION_ID or set 'action_id:' "
                        "(see WORKFLOW_ACTIONS.md).", "§7.4", name, rel, f"step '{sname}'"))

        findings += check_tags(wf.get("tags"), resource_type="workflow", resource_name=name, file=rel)
    return findings
