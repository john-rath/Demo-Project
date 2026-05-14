"""
Workflow Automation resource manager for dd-demo-toolkit.

Handles deployment, deletion, and listing of Datadog Workflow Automation workflows for verticals.
"""

import logging
from pathlib import Path
from typing import Dict, List, Any, Optional

import yaml

from dd_demo_toolkit.utils.dd_api import DatadogAPIClient


logger = logging.getLogger(__name__)


# Mapping from the declarative YAML `type:` field on each step to the
# Datadog Workflow Automation action ID the engine uses at runtime.
#
# These defaults are best-effort against the public Datadog action
# catalog. Specific tenant installations may use slightly different
# IDs (vendor renames, private actions, etc.) — verify in the UI by
# opening any existing workflow and clicking "Edit JSON Spec" to see
# the live `actionId` strings used in that tenant.
#
# Override per-step by adding `action_id: com.datadoghq.<...>` in the
# YAML; the explicit value always wins over the type-based lookup.
_TYPE_TO_ACTION_ID = {
    # core / control flow
    "noop": "com.datadoghq.core.noop",
    "sleep": "com.datadoghq.core.wait",
    "wait": "com.datadoghq.core.wait",
    # NOTE: `condition` (branching) doesn't fit the sequential
    # auto-wiring model — set `action_id:` AND `out_edges:` explicitly
    # on conditional steps. We keep an entry here so the warning at
    # least surfaces the closest Datadog action handle.
    "condition": "com.datadoghq.core.conditional",
    # Datadog signal sources
    "datadog_query": "com.datadoghq.dd.monitors.searchMonitors",
    "datadog_incident": "com.datadoghq.dd.incidents.createIncident",
    "datadog_case": "com.datadoghq.dd.cases.createCase",
    # generic outbound
    "http_request": "com.datadoghq.http.request",
    # vendor / SaaS
    "slack_message": "com.datadoghq.slack.sendMessage",
    "slack_send_message": "com.datadoghq.slack.sendMessage",
    "pagerduty_alert": "com.datadoghq.pagerduty.triggerIncident",
    "pagerduty_trigger": "com.datadoghq.pagerduty.triggerIncident",
    "jira_create_issue": "com.datadoghq.jira.createIssue",
    "servicenow_create_incident": "com.datadoghq.servicenow.createIncident",
    "github_create_issue": "com.datadoghq.github.createIssue",
}


def _resolve_action_id(step: Dict[str, Any]) -> str:
    """Return the Datadog action ID for a step.

    Lookup order: explicit ``action_id`` in YAML, then a type-based
    mapping, then the no-op fallback (so a typo never crashes deploy).
    """
    explicit = step.get("action_id")
    if explicit:
        return explicit
    step_type = step.get("type")
    if step_type and step_type in _TYPE_TO_ACTION_ID:
        return _TYPE_TO_ACTION_ID[step_type]
    if step_type:
        logger.warning(
            "Workflow step '%s' has unknown type '%s'; falling back to no-op. "
            "Add it to _TYPE_TO_ACTION_ID or set `action_id:` on the step.",
            step.get("name", "<unnamed>"), step_type,
        )
    return "com.datadoghq.core.noop"


class WorkflowManager:
    """Manages deployment and lifecycle of Datadog Workflow Automation workflows."""

    def __init__(self) -> None:
        """Initialize the workflow manager."""
        pass

    def deploy(
        self,
        vertical_path: str,
        api_client: DatadogAPIClient,
        tags: Optional[Dict[str, str]] = None,
        dry_run: bool = False,
        vertical_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Deploy workflows from a vertical.

        Reads workflows.yaml from the vertical path and creates workflows via API.
        Each workflow definition should have: name, description, trigger, steps, tags.

        Args:
            vertical_path: Path to the vertical directory.
            api_client: Datadog API client instance.
            tags: Additional tags to inject (vertical and dd-demo-toolkit tags added automatically).
            dry_run: If True, skip API calls and return what would be created.

        Returns:
            Dictionary with keys:
            - created_ids: List of created workflow IDs
            - created_names: List of created workflow names
            - errors: List of error messages
            - total_created: Number of successfully created workflows
            - total_errors: Number of failed deployments
        """
        vertical_path_obj = Path(vertical_path)
        workflows_file = vertical_path_obj / "workflows.yaml"

        result = {
            "created_ids": [],
            "created_names": [],
            "errors": [],
            "total_created": 0,
            "total_errors": 0,
        }

        if not workflows_file.exists():
            logger.info(f"No workflows.yaml file found at {workflows_file}")
            return result

        if vertical_name is None:
            vertical_name = vertical_path_obj.name

        try:
            with open(workflows_file, "r") as f:
                config = yaml.safe_load(f)
        except yaml.YAMLError as e:
            error_msg = f"Failed to parse workflows.yaml: {str(e)}"
            result["errors"].append(error_msg)
            result["total_errors"] += 1
            logger.error(error_msg)
            return result
        except IOError as e:
            error_msg = f"Failed to read workflows.yaml: {str(e)}"
            result["errors"].append(error_msg)
            result["total_errors"] += 1
            logger.error(error_msg)
            return result

        if not config:
            logger.info("No workflows defined in workflows.yaml")
            return result

        workflows = config if isinstance(config, list) else config.get("workflows", [])
        if not workflows:
            logger.info("No workflows found in workflows.yaml")
            return result

        logger.info(f"Deploying {len(workflows)} workflow(s) for vertical '{vertical_name}'")

        for idx, workflow_config in enumerate(workflows):
            try:
                # Build the workflow payload
                payload = self._build_workflow_payload(workflow_config, vertical_name, tags)

                if dry_run:
                    workflow_name = payload.get("data", {}).get("attributes", {}).get("name", f"workflow-{idx}")
                    logger.info(f"[DRY RUN] Would create workflow '{workflow_name}'")
                    result["created_names"].append(workflow_name)
                    result["total_created"] += 1
                else:
                    # Create via API
                    response = api_client.create_workflow(payload)
                    workflow_data = response.get("data", {})
                    workflow_id = workflow_data.get("id")
                    workflow_name = workflow_data.get("attributes", {}).get("name", "")

                    if workflow_id:
                        result["created_ids"].append(workflow_id)
                        result["created_names"].append(workflow_name)
                        result["total_created"] += 1
                        logger.info(f"Created workflow '{workflow_name}' with ID {workflow_id}")
                    else:
                        error_msg = f"No workflow ID in response for workflow {idx}"
                        result["errors"].append(error_msg)
                        result["total_errors"] += 1
                        logger.error(error_msg)

            except KeyError as e:
                error_msg = f"Workflow {idx} missing required field: {str(e)}"
                result["errors"].append(error_msg)
                result["total_errors"] += 1
                logger.error(error_msg)
            except RuntimeError as e:
                error_msg = f"API error deploying workflow {idx}: {str(e)}"
                result["errors"].append(error_msg)
                result["total_errors"] += 1
                logger.error(error_msg)
            except Exception as e:
                error_msg = f"Unexpected error deploying workflow {idx}: {str(e)}"
                result["errors"].append(error_msg)
                result["total_errors"] += 1
                logger.error(error_msg)

        logger.info(
            f"Workflow deployment complete: {result['total_created']} created, "
            f"{result['total_errors']} errors"
        )

        return result

    def _build_workflow_payload(
        self,
        config: Dict[str, Any],
        vertical_name: str,
        additional_tags: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """
        Build a workflow payload from config.

        Args:
            config: Workflow configuration dict.
            vertical_name: Vertical name for tagging.
            additional_tags: Additional tags to add.

        Returns:
            Workflow payload ready for API submission.

        Raises:
            KeyError: If required fields are missing.
        """
        # Validate required fields
        required = ["name", "description", "trigger", "steps"]
        for field in required:
            if field not in config:
                raise KeyError(f"Required field '{field}' missing")

        # The Workflow Automation API v2 requires a complex 'spec' field with
        # actionIds, display bounds, connectionEnv, etc. that cannot be generated
        # from declarative YAML. If a pre-built spec is provided, use it directly.
        # Otherwise, build a minimal spec from our YAML config.
        if "spec" in config:
            # Use pre-built spec exported from Datadog UI (Edit JSON Spec)
            attributes = {
                "name": config["name"],
                "description": config["description"],
                "spec": config["spec"],
            }
        else:
            # Build a minimal valid spec from our trigger/steps config.
            #
            # Two things must be right for the workflow to show up on the
            # Datadog canvas as a connected, executable pipeline rather
            # than disconnected no-op boxes:
            #   1. Each step needs a real `actionId` (mapped from the
            #      YAML `type:` field via _resolve_action_id).
            #   2. Each step needs an `outEdges` list pointing to the
            #      name of the next step. We wire steps sequentially in
            #      the order they appear in the YAML; the last step's
            #      outEdges list is empty.
            raw_steps = config.get("steps", []) or []
            steps_spec = []
            for idx, step in enumerate(raw_steps):
                # The Workflow API expects parameters as an array of
                # {name: str, value: any} objects, not a flat dict.
                raw_params = step.get("parameters", {})
                if isinstance(raw_params, dict):
                    params_array = [
                        {"name": k, "value": v}
                        for k, v in raw_params.items()
                    ]
                elif isinstance(raw_params, list):
                    # Already in [{name, value}] format
                    params_array = raw_params
                else:
                    params_array = []

                # Sequential wiring: this step's `outEdges` points to
                # the NEXT step's name. The last step has no outEdges.
                # Allow an explicit `out_edges:` override in YAML for
                # workflows that need fan-out / non-linear control flow.
                explicit_edges = step.get("out_edges")
                if explicit_edges is not None:
                    out_edges = list(explicit_edges)
                elif idx + 1 < len(raw_steps):
                    next_step_name = raw_steps[idx + 1].get("name", f"step_{idx + 1}")
                    out_edges = [next_step_name]
                else:
                    out_edges = []

                step_spec: Dict[str, Any] = {
                    "name": step.get("name", f"step_{idx}"),
                    "actionId": _resolve_action_id(step),
                    "parameters": params_array,
                    "outEdges": out_edges,
                }
                if "description" in step:
                    step_spec["description"] = step["description"]
                steps_spec.append(step_spec)

            trigger_config = config.get("trigger", {})
            trigger_type = trigger_config.get("type", "manual")

            spec = {
                "triggers": [{
                    "startStepNames": [steps_spec[0]["name"]] if steps_spec else [],
                    f"{trigger_type}Trigger": trigger_config,
                }],
                "steps": steps_spec,
            }

            attributes = {
                "name": config["name"],
                "description": config["description"],
                "spec": spec,
            }

        # Inject tags
        tags = config.get("tags", []) if isinstance(config.get("tags"), list) else []
        tags.append(f"vertical:{vertical_name}")
        tags.append("dd-demo-toolkit:true")

        if additional_tags:
            for key, value in additional_tags.items():
                tags.append(f"{key}:{value}")

        attributes["tags"] = tags

        payload = {
            "data": {
                "type": "workflows",
                "attributes": attributes,
            }
        }

        return payload

    def teardown(
        self,
        api_client: DatadogAPIClient,
        vertical_name: Optional[str],
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """
        Delete workflows tagged by the toolkit.

        Args:
            api_client: Datadog API client instance.
            vertical_name: Name of the vertical to clean up. If ``None``, every
                workflow tagged ``dd-demo-toolkit:true`` is deleted regardless
                of vertical (orphan-sweep mode).
            dry_run: If True, skip API calls and return what would be deleted.

        Returns:
            Dictionary with keys:
            - deleted_ids: List of deleted workflow IDs
            - deleted_names: List of deleted workflow names
            - errors: List of error messages
            - total_deleted: Number of successfully deleted workflows
            - total_errors: Number of failed deletions
        """
        result = {
            "deleted_ids": [],
            "deleted_names": [],
            "errors": [],
            "total_deleted": 0,
            "total_errors": 0,
        }

        try:
            if vertical_name is None:
                tag_filter = "dd-demo-toolkit:true"
                scope_label = "all toolkit-managed verticals"
            else:
                tag_filter = f"vertical:{vertical_name}"
                scope_label = f"vertical '{vertical_name}'"
            response = api_client.list_workflows(tag_filter=tag_filter)
            workflows = response.get("data", [])
        except RuntimeError as e:
            error_msg = f"Failed to list workflows: {str(e)}"
            result["errors"].append(error_msg)
            result["total_errors"] += 1
            logger.error(error_msg)
            return result

        logger.info(
            f"Found {len(workflows)} workflow(s) to delete for {scope_label}"
        )

        for workflow in workflows:
            try:
                workflow_id = workflow.get("id")
                workflow_name = workflow.get("attributes", {}).get("name", "")

                if not workflow_id:
                    error_msg = f"Workflow missing ID: {workflow_name}"
                    result["errors"].append(error_msg)
                    result["total_errors"] += 1
                    logger.error(error_msg)
                    continue

                if dry_run:
                    logger.info(f"[DRY RUN] Would delete workflow '{workflow_name}' (ID: {workflow_id})")
                    result["deleted_ids"].append(workflow_id)
                    result["deleted_names"].append(workflow_name)
                    result["total_deleted"] += 1
                else:
                    api_client.delete_workflow(workflow_id)
                    result["deleted_ids"].append(workflow_id)
                    result["deleted_names"].append(workflow_name)
                    result["total_deleted"] += 1
                    logger.info(f"Deleted workflow '{workflow_name}' (ID: {workflow_id})")

            except RuntimeError as e:
                error_msg = f"API error deleting workflow {workflow.get('id')}: {str(e)}"
                result["errors"].append(error_msg)
                result["total_errors"] += 1
                logger.error(error_msg)
            except Exception as e:
                error_msg = f"Unexpected error deleting workflow {workflow.get('id')}: {str(e)}"
                result["errors"].append(error_msg)
                result["total_errors"] += 1
                logger.error(error_msg)

        logger.info(
            f"Workflow teardown complete: {result['total_deleted']} deleted, "
            f"{result['total_errors']} errors"
        )

        return result

    def list_deployed(
        self,
        api_client: DatadogAPIClient,
        vertical_name: str,
    ) -> Dict[str, Any]:
        """
        List workflows deployed for a vertical.

        Args:
            api_client: Datadog API client instance.
            vertical_name: Name of the vertical.

        Returns:
            Dictionary with keys:
            - total: Number of workflows
            - workflows: List of workflow details
            - errors: List of error messages
        """
        result = {
            "total": 0,
            "workflows": [],
            "errors": [],
        }

        try:
            tag_filter = f"vertical:{vertical_name}"
            response = api_client.list_workflows(tag_filter=tag_filter)
            workflows = response.get("data", [])
            result["total"] = len(workflows)
            result["workflows"] = workflows
            logger.info(f"Found {len(workflows)} deployed workflow(s) for vertical '{vertical_name}'")
        except RuntimeError as e:
            error_msg = f"Failed to list workflows: {str(e)}"
            result["errors"].append(error_msg)
            logger.error(error_msg)

        return result
