# Contributing to dd-demo-toolkit

Thanks for your interest in contributing. This toolkit is built and maintained by the Datadog Sales Engineering org to power consistent, realistic demos across verticals. Contributions from any Datadog employee are welcome.

## Before you start

1. **Read [`dd-demo-toolkit/STYLE_GUIDE.md`](dd-demo-toolkit/STYLE_GUIDE.md).** It encodes the Datadog query gotchas, tag standards, naming conventions, and bifurcation rules that have caused production demo bugs in the past. Every rule traces to a real incident.
2. **Read [`dd-demo-toolkit/WORKFLOW_ACTIONS.md`](dd-demo-toolkit/WORKFLOW_ACTIONS.md)** if you're touching any `workflows.yaml`. Unknown `actionId` → 400 from the Datadog API.
3. **Read [`dd-demo-toolkit/CLAUDE.md`](dd-demo-toolkit/CLAUDE.md)** for project scope and prior assumptions.

## Local development

```bash
cd dd-demo-toolkit
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev,ui]'

# Static validation — no Datadog account needed.
dd-demo validate --vertical healthcare

# Unit tests.
pytest

# Run the web UI locally (requires op CLI + signed-in 1Password).
make ui
```

## Before opening a PR

Run the same checks CI runs:

```bash
black --check dd_demo_toolkit dd_demo_toolkit_ui
isort --check-only dd_demo_toolkit dd_demo_toolkit_ui
flake8 dd_demo_toolkit dd_demo_toolkit_ui --max-line-length=100 --extend-ignore=E203,W503
pytest
for v in finance healthcare hospitality insurance manufacturing; do
  dd-demo validate --vertical "$v" || exit 1
done
```

## What to put in a PR

- **One logical change.** A "fix finance SLO + add new BD plugin + bump deps" PR is hard to review.
- **Reproduction notes** for bug fixes: what command shows the bug, what's expected.
- **A `dd-demo validate` clean pass** in the PR description for any vertical you touched.
- **A note in the PR description if you added a new style-guide rule** — and add it to `STYLE_GUIDE.md` in the same PR.

## Adding a new vertical or overlay

See `dd-demo-toolkit/CLAUDE.md §1` (vertical scope) and `§6` (overlays). The 4-axis disjoint rule (`§9.3`) is non-negotiable for new overlay plugins.

## Adding a new toolkit secret

See README §"Adding a new secret to the toolkit". Both `dd_demo_toolkit_ui/env_manager.SECRET_KEYS` and the compose `:?` failure messages must be updated, otherwise the corp-policy chain breaks.

## Reporting issues

Use the issue templates under `.github/ISSUE_TEMPLATE/`. For demo-time outages, see [`SUPPORT.md`](SUPPORT.md).
