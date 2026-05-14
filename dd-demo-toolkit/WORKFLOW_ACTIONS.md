# Workflow Authoring & Action ID Reference

The Datadog Workflow Automation API is **not** declarative-YAML-friendly out
of the box. This doc captures the things that have repeatedly broken
deploys and the rules for authoring new workflows in the toolkit. Always
read this before writing or editing a `workflows.yaml`.

The authoritative payload shape lives in the Datadog public API reference:
<https://docs.datadoghq.com/api/latest/workflow-automation/>. This doc
captures the toolkit-specific YAML conventions and the lookup process for
the bits Datadog under-documents (the action ID catalog).

---

## TL;DR — five rules that prevent 400 / silent no-op

1. **Every step's `actionId` must be a real Datadog action ID.** If it
   isn't, Datadog rejects the entire workflow create with HTTP 400
   `"spec is invalid"`. The toolkit's `_TYPE_TO_ACTION_ID` map in
   [`dd_demo_toolkit/resources/workflows.py`](dd_demo_toolkit/resources/workflows.py)
   only contains action IDs that have been verified against the
   public docs or a live tenant. Unknown YAML `type:` values fall
   through to `com.datadoghq.core.noop` so deploys still succeed.
2. **Step connections use `outboundEdges`, not `outEdges`.** The right
   shape is a list of OBJECTS:
   `[{"branchName": "main", "nextStepName": "next_step"}]` — not a list
   of step-name strings. The manager auto-wires sequentially; override
   per-step with `out_edges:` in YAML for non-linear flows.
3. **The trigger `type:` selects the wrapper key, not a field inside.**
   YAML `trigger: {type: monitor, ...}` becomes
   `{"startStepNames": [...], "monitorTrigger": {<the rest>}}`. The
   manager strips `type:` automatically. Don't leave `type:` inside
   `monitorTrigger` — Datadog ignores extras but the noise is
   misleading.
4. **`published: true` is added by the manager.** Don't set it in YAML;
   you'd just be overriding the default.
5. **Workflows that target an integration (Slack, Jira, ServiceNow,
   Datadog API actions) need a `connection_label:` on the step.** This
   tells Datadog which connection in the spec's `connectionEnvs` block
   to use. The toolkit doesn't auto-materialize connections (tenant-
   specific), so the workflow will still deploy without one, but the
   step won't *execute* against a real integration until the user
   binds a connection in the UI.

---

## Verified action ID catalog

These are the action IDs the toolkit currently trusts. Source column
identifies where the ID was verified.

| YAML `type:`         | Datadog `actionId`                                | Verified via |
|----------------------|---------------------------------------------------|--------------|
| `noop`               | `com.datadoghq.core.noop`                         | Live tenant deploys |
| `datadog_query`      | `com.datadoghq.dd.monitor.listMonitors`           | [Datadog API ref example](https://docs.datadoghq.com/api/latest/workflow-automation/) |
| `condition`          | `com.datadoghq.core.if`                           | [Datadog flow-control docs](https://docs.datadoghq.com/actions/workflows/actions/flow_control/) |
| `slack_message`      | `com.datadoghq.slack.send_simple_message`         | [Runa Terraform export blog](https://medium.com/runa-engineering/terraforming-datadog-workflows-0673abefe87b) |
| `data_transform`     | `com.datadoghq.datatransformation.func`           | Runa Terraform export blog |
| `javascript`         | `com.datadoghq.datatransformation.func`           | Runa Terraform export blog |

**Still unverified** (commented out in the map, will fall through to
no-op until populated):

- `http_request` — Datadog's HTTP action. Discovery procedure below.
- `sleep` / `wait` — Sleep / delay action.
- `datadog_incident` / `datadog_case` — Datadog incident / case creators.
- `pagerduty_alert` / `pagerduty_trigger` — PagerDuty trigger.
- `jira_create_issue`, `servicenow_create_incident`, `github_create_issue`.

---

## How to discover a new action ID

Datadog deliberately doesn't expose the actionId catalog in its public
docs (it's a private surface oriented around the drag-and-drop UI).
Three reliable discovery methods, fastest first:

### Method 1 — Datadog UI "Edit JSON Spec" (~30 seconds)

1. Workflow Automation home → **New Workflow** (blank).
2. Drag the action you want (e.g. "HTTP — Make request") onto the
   canvas. Connect it to the trigger.
3. Click **Edit JSON Spec** in the top-right.
4. The `actionId` is at the top of the step block. Copy it.
5. Add the entry to `_TYPE_TO_ACTION_ID` in
   [`dd_demo_toolkit/resources/workflows.py`](dd_demo_toolkit/resources/workflows.py).
6. Optional: discard the scratch workflow without saving — the action
   ID is stable across instances of the same action.

### Method 2 — Introspect script

[`scripts/introspect_workflow_actions.py`](scripts/introspect_workflow_actions.py)
pulls every workflow your credentials can see and prints an actionId
histogram. Useful once your tenant has some real workflows wired in.

```
export $(grep -v '^#' .env | grep '=' | xargs) 2>/dev/null
python3 scripts/introspect_workflow_actions.py --include-blueprint
```

Note: blueprint workflows are not returned by the list API (UI-only),
so this method only works after you have real (or scratch) workflows
in the tenant.

### Method 3 — Public docs / Terraform exports

The Runa "Terraforming Datadog Workflows" article and a small handful
of community blog posts include real Terraform exports. Search
GitHub / blog posts for `"com.datadoghq."` plus the action you want.

---

## Naming patterns

Once you know one action ID, the patterns help guess sibling IDs:

- **Native Datadog actions:** `com.datadoghq.dd.<resource>.<action>`
  - Resource is **singular** (`monitor`, not `monitors`).
  - Action is **camelCase verb** (`listMonitors`, `getMonitor`,
    `createIncident`).
- **Datadog-shipped integrations:** `com.datadoghq.<vendor>.<action>`
  - e.g. `com.datadoghq.slack.send_simple_message`,
    `com.datadoghq.datatransformation.func`.
  - Action is **snake_case** here (not camelCase). Don't ask why.
- **Core / control flow:** `com.datadoghq.core.<action>`
  - e.g. `com.datadoghq.core.noop`, `com.datadoghq.core.if`.

These are pattern *guesses* — Datadog still has to register the ID,
so always verify before adding to the trusted map.

---

## Canonical YAML shape (template)

```yaml
workflows:

  - name: "[Vertical/Customer] My Workflow"
    description: "One-sentence description (<=300 chars total — Datadog limit)."

    # Trigger. `type` selects the wrapper key on the API side.
    # Supported trigger types: monitor, manual, slack, github_webhook,
    # schedule (each maps to <type>Trigger in the payload).
    trigger:
      type: monitor
      monitor_tags:                # optional — bind monitors by tag
        - "incident_domain:foo"

    steps:
      # Sequential auto-wiring: each step's outboundEdges points at
      # the NEXT step in the list. Override with `out_edges:` for
      # non-linear flows.

      - name: gather_context
        type: datadog_query        # mapped to com.datadoghq.dd.monitor.listMonitors
        description: "Pull current state."
        parameters:
          query: "max:my.metric{tag:value}"

      - name: decide
        type: condition            # mapped to com.datadoghq.core.if
        description: "Branch on the gathered state."
        parameters:
          condition: "{{steps.gather_context.output.value}} > 100"
        # Non-linear flow: fan out to two branches by name.
        out_edges:
          - branch_name: "true"
            next_step_name: remediate
          - branch_name: "false"
            next_step_name: notify_only

      - name: remediate
        type: http_request         # NO action ID yet — falls through to noop
        action_id: com.datadoghq.http.<TODO>   # set explicitly once known
        connection_label: "INTEGRATION_HTTP"   # required for HTTP action
        parameters:
          method: POST
          url: "https://example.com/admin"
          headers:
            Content-Type: "application/json"
          body:
            action: "rate-limit"

      - name: notify_only
        type: slack_message        # mapped to com.datadoghq.slack.send_simple_message
        connection_label: "INTEGRATION_SLACK"  # required for Slack action
        parameters:
          channel: "#ops"
          message: "Threshold not breached."

    tags:
      - "vertical:<vert>"          # auto-injected by manager — keep for clarity
      - "team:<role>"
      - "incident_domain:<value>"
      - "workflow-type:auto-remediation"
```

### Per-step YAML fields the manager understands

| YAML field             | Maps to API field             | Notes                                                 |
|------------------------|-------------------------------|-------------------------------------------------------|
| `name:` (required)     | `name`                        | Used as the `nextStepName` for sequential wiring.     |
| `type:` (recommended)  | (looked up in `_TYPE_TO_ACTION_ID`) | Translated to `actionId`. Unknown types → no-op + warning. |
| `action_id:` (optional)| `actionId`                    | Explicit override; always wins over type-based lookup. |
| `parameters:` (dict)   | `parameters: [{name, value}]` | Manager converts flat dict to API's array shape.       |
| `description:`         | `description`                 | Passed through.                                       |
| `connection_label:`    | `connectionLabel`             | Required when the action targets an integration.      |
| `out_edges:` (override)| `outboundEdges`               | List of strings (treated as main-branch next-steps) OR list of `{branch_name, next_step_name}` objects. Default: sequential wiring. |

---

## Common pitfalls

| Symptom | Cause | Fix |
|---|---|---|
| `400 spec is invalid` + `no action registered for ID X` | Unknown `actionId` in the payload | Verify the ID via Method 1 above; add to `_TYPE_TO_ACTION_ID` |
| Workflow deploys, steps shown as `DATADOG CORE > NO OP` | YAML `type:` not in the map → fell through to no-op | Discover the right actionId and add to the map (or set `action_id:` per step) |
| Steps deployed but **disconnected** on canvas | `outboundEdges` not built or built with wrong shape | The manager handles this now — if you bypassed it via raw `spec:` in YAML, use the object shape `{branchName, nextStepName}` |
| Step deploys but won't execute | Step needs an integration connection | Set `connection_label:` on the step and bind a connection in the UI (or extend the manager to emit `connectionEnvs`) |
| Workflow created but doesn't show as active | Missing `published: true` | Manager sets this automatically since 2026-05 |
| Description rejected as too long | Datadog workflow description is capped at 300 chars | Trim. The manager does not validate, so this lands as a 400 |

---

## Where the code lives

- **Manager:** [`dd_demo_toolkit/resources/workflows.py`](dd_demo_toolkit/resources/workflows.py)
  - `_TYPE_TO_ACTION_ID` — the catalog. Add new entries here as they're verified.
  - `_resolve_action_id()` — resolution order: explicit `action_id:` → type lookup → no-op.
  - `_build_workflow_payload()` — translates YAML config to the v2 API payload.
- **Discovery helper:** [`scripts/introspect_workflow_actions.py`](scripts/introspect_workflow_actions.py)
- **External references:**
  - Datadog API reference: <https://docs.datadoghq.com/api/latest/workflow-automation/>
  - Workflow Logic / flow control: <https://docs.datadoghq.com/actions/workflows/actions/flow_control/>
  - Action catalog (high-level, no IDs): <https://docs.datadoghq.com/actions/actions_catalog/>

---

## Adding a new action type — full checklist

1. Identify the YAML `type:` value you want (e.g. `pagerduty_trigger`).
2. Discover the actionId via Method 1 (UI Edit JSON Spec) or 2 (introspect).
3. Add `"pagerduty_trigger": "com.datadoghq.<...>"` to `_TYPE_TO_ACTION_ID`.
4. If the action targets an integration, also document the expected
   `connection_label:` value (e.g. `INTEGRATION_PAGERDUTY`) in the
   `_TYPE_TO_ACTION_ID` comments.
5. Update this doc's verified-IDs table with the source.
6. Deploy a workflow that uses the new type. Verify in the UI that
   steps render with the right action, not `NO OP`.
7. If the workflow is meant to actually run, bind any required
   connection in the UI for each `connection_label:` and trigger it.
