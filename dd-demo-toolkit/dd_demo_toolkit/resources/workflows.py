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
            # Build a minimal valid spec from our trigger/steps config
            steps_spec = []
            for step in config.get("steps", []):
                steps_spec.append({
                    "name": step.get("name", "step"),
                    "actionId": step.get("action_id", "com.datadoghq.core.noop"),
                    "parameters": step.get("parameters", {}),
                })

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
        vertical_name: str,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """
        Delete all workflows tagged with a vertical.

        Args:
            api_client: Datadog API client instance.
            vertical_name: Name of the vertical to clean up.
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
            tag_filter = f"vertical:{vertical_name}"
            response = api_client.list_workflows(tag_filter=tag_filter)
            workflows = response.get("data", [])
        except RuntimeError as e:
            error_msg = f"Failed to list workflows: {str(e)}"
            result["errors"].append(error_msg)
            result["total_errors"] += 1
            logger.error(error_msg)
            return result

        logger.info(
            f"Found {len(workflows)} workflow(s) to delete for vertical '{vertical_name}'"
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
