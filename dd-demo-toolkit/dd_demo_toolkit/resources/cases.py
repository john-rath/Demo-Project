"""
Case Management resource manager for dd-demo-toolkit.

Handles creation, updating, and lifecycle management of Datadog cases for verticals.
"""

import logging
from pathlib import Path
from typing import Dict, List, Any, Optional

import yaml

from dd_demo_toolkit.utils.dd_api import DatadogAPIClient


logger = logging.getLogger(__name__)


class CaseManager:
    """Manages creation and lifecycle of Datadog cases."""

    def __init__(self) -> None:
        """Initialize the case manager."""
        pass

    def create_case(
        self,
        api_client: DatadogAPIClient,
        title: str,
        description: str,
        vertical_name: str,
        priority: str = "P3",
        linked_resources: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Create a case.

        Args:
            api_client: Datadog API client instance.
            title: Case title.
            description: Case description.
            vertical_name: Name of the vertical for tagging.
            priority: Case priority (P1, P2, P3, P4). Defaults to P3.
            linked_resources: Optional dict with linked resource IDs (e.g., {"incident": "incident_id"}).

        Returns:
            Dictionary with:
            - case_id: ID of the created case
            - status: "success" or "error"
            - message: Status message
        """
        result = {
            "case_id": None,
            "status": "error",
            "message": "",
        }

        linked_resources = linked_resources or {}

        # Build case payload
        payload = self._build_case_payload(
            title=title,
            description=description,
            priority=priority,
            vertical_name=vertical_name,
            linked_resources=linked_resources,
        )

        try:
            response = api_client.create_case(payload)
            case_data = response.get("data", {})
            case_id = case_data.get("id")

            if case_id:
                result["case_id"] = case_id
                result["status"] = "success"
                result["message"] = f"Case '{title}' created with ID {case_id}"
                logger.info(result["message"])
            else:
                result["message"] = "No case ID in API response"
                logger.error(result["message"])

        except RuntimeError as e:
            result["message"] = f"API error creating case: {str(e)}"
            logger.error(result["message"])
        except Exception as e:
            result["message"] = f"Unexpected error creating case: {str(e)}"
            logger.error(result["message"])

        return result

    def update_case(
        self,
        api_client: DatadogAPIClient,
        case_id: str,
        status: Optional[str] = None,
        notes: Optional[str] = None,
        priority: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Update a case.

        Args:
            api_client: Datadog API client instance.
            case_id: ID of the case to update.
            status: Optional new status (OPEN, CLOSED, etc.).
            notes: Optional notes to append.
            priority: Optional new priority.

        Returns:
            Dictionary with:
            - status: "success" or "error"
            - message: Status message
        """
        result = {
            "status": "error",
            "message": "",
        }

        attributes = {}

        if status:
            attributes["status"] = status
        if priority:
            attributes["priority"] = priority
        if notes:
            attributes["notes"] = notes

        if not attributes:
            result["message"] = "No update attributes provided"
            logger.warning(result["message"])
            return result

        payload = {
            "data": {
                "type": "case",
                "attributes": attributes,
            }
        }

        try:
            api_client.update_case(case_id, payload)
            result["status"] = "success"
            result["message"] = f"Case {case_id} updated successfully"
            logger.info(result["message"])
        except RuntimeError as e:
            result["message"] = f"API error updating case: {str(e)}"
            logger.error(result["message"])
        except Exception as e:
            result["message"] = f"Unexpected error updating case: {str(e)}"
            logger.error(result["message"])

        return result

    def list_cases(
        self,
        api_client: DatadogAPIClient,
        vertical_name: str,
    ) -> Dict[str, Any]:
        """
        List cases for a vertical.

        Args:
            api_client: Datadog API client instance.
            vertical_name: Name of the vertical.

        Returns:
            Dictionary with:
            - total: Number of cases
            - cases: List of case details
            - errors: List of error messages
        """
        result = {
            "total": 0,
            "cases": [],
            "errors": [],
        }

        try:
            # Filter for cases with vertical tag
            filter_query = f"tag:vertical:{vertical_name}"
            response = api_client.list_cases(filter_query=filter_query)
            cases = response.get("data", [])
            result["total"] = len(cases)
            result["cases"] = cases
            logger.info(f"Found {len(cases)} case(s) for vertical '{vertical_name}'")
        except RuntimeError as e:
            error_msg = f"Failed to list cases: {str(e)}"
            result["errors"].append(error_msg)
            logger.error(error_msg)
        except Exception as e:
            error_msg = f"Unexpected error listing cases: {str(e)}"
            result["errors"].append(error_msg)
            logger.error(error_msg)

        return result

    def teardown(
        self,
        api_client: DatadogAPIClient,
        vertical_name: str,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """
        Close/archive all demo cases for a vertical.

        Args:
            api_client: Datadog API client instance.
            vertical_name: Name of the vertical.
            dry_run: If True, skip API calls.

        Returns:
            Dictionary with:
            - closed_ids: List of closed case IDs
            - closed_titles: List of closed case titles
            - errors: List of error messages
            - total_closed: Number of successfully closed cases
            - total_errors: Number of failed closures
        """
        result = {
            "closed_ids": [],
            "closed_titles": [],
            "errors": [],
            "total_closed": 0,
            "total_errors": 0,
        }

        # Get cases for this vertical
        list_result = self.list_cases(api_client, vertical_name)
        cases = list_result.get("cases", [])

        logger.info(f"Found {len(cases)} case(s) to close for vertical '{vertical_name}'")

        for case in cases:
            try:
                case_id = case.get("id")
                case_title = case.get("attributes", {}).get("title", "")

                if not case_id:
                    error_msg = f"Case missing ID"
                    result["errors"].append(error_msg)
                    result["total_errors"] += 1
                    logger.error(error_msg)
                    continue

                if dry_run:
                    logger.info(f"[DRY RUN] Would close case '{case_title}' (ID: {case_id})")
                    result["closed_ids"].append(case_id)
                    result["closed_titles"].append(case_title)
                    result["total_closed"] += 1
                else:
                    self.update_case(api_client, case_id, status="CLOSED")
                    result["closed_ids"].append(case_id)
                    result["closed_titles"].append(case_title)
                    result["total_closed"] += 1
                    logger.info(f"Closed case '{case_title}' (ID: {case_id})")

            except Exception as e:
                error_msg = f"Error closing case {case.get('id')}: {str(e)}"
                result["errors"].append(error_msg)
                result["total_errors"] += 1
                logger.error(error_msg)

        logger.info(
            f"Case teardown complete: {result['total_closed']} closed, "
            f"{result['total_errors']} errors"
        )

        return result

    def deploy(
        self,
        vertical_path: str,
        api_client: DatadogAPIClient,
        tags: Optional[Dict[str, str]] = None,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """
        Deploy cases from a vertical's cases.yaml configuration.

        Args:
            vertical_path: Path to the vertical directory.
            api_client: Datadog API client instance.
            tags: Additional tags to inject (vertical and dd-demo-toolkit tags added automatically).
            dry_run: If True, skip API calls and return what would be created.

        Returns:
            Dictionary with keys:
            - created_ids: List of created case IDs
            - created_titles: List of created case titles
            - errors: List of error messages
            - total_created: Number of successfully created cases
            - total_errors: Number of failed deployments
        """
        vertical_path_obj = Path(vertical_path)
        cases_file = vertical_path_obj / "cases.yaml"

        result = {
            "created_ids": [],
            "created_titles": [],
            "errors": [],
            "total_created": 0,
            "total_errors": 0,
        }

        if not cases_file.exists():
            logger.info(f"No cases.yaml file found at {cases_file}")
            return result

        vertical_name = vertical_path_obj.name

        try:
            with open(cases_file, "r") as f:
                config = yaml.safe_load(f)
        except yaml.YAMLError as e:
            error_msg = f"Failed to parse cases.yaml: {str(e)}"
            result["errors"].append(error_msg)
            result["total_errors"] += 1
            logger.error(error_msg)
            return result
        except IOError as e:
            error_msg = f"Failed to read cases.yaml: {str(e)}"
            result["errors"].append(error_msg)
            result["total_errors"] += 1
            logger.error(error_msg)
            return result

        if not config:
            logger.info("No cases defined in cases.yaml")
            return result

        cases = config if isinstance(config, list) else config.get("cases", [])
        if not cases:
            logger.info("No cases found in cases.yaml")
            return result

        logger.info(f"Deploying {len(cases)} case(s) for vertical '{vertical_name}'")

        for idx, case_config in enumerate(cases):
            try:
                title = case_config.get("title", f"case-{idx}")
                description = case_config.get("description", "")
                priority = case_config.get("priority", "P3")

                if dry_run:
                    logger.info(f"[DRY RUN] Would create case '{title}'")
                    result["created_titles"].append(title)
                    result["total_created"] += 1
                else:
                    case_result = self.create_case(
                        api_client=api_client,
                        title=title,
                        description=description,
                        vertical_name=vertical_name,
                        priority=priority,
                    )

                    if case_result.get("status") == "success":
                        case_id = case_result.get("case_id")
                        result["created_ids"].append(case_id)
                        result["created_titles"].append(title)
                        result["total_created"] += 1
                    else:
                        error_msg = f"Failed to create case '{title}': {case_result.get('message')}"
                        result["errors"].append(error_msg)
                        result["total_errors"] += 1
                        logger.error(error_msg)

            except KeyError as e:
                error_msg = f"Case {idx} missing required field: {str(e)}"
                result["errors"].append(error_msg)
                result["total_errors"] += 1
                logger.error(error_msg)
            except Exception as e:
                error_msg = f"Unexpected error deploying case {idx}: {str(e)}"
                result["errors"].append(error_msg)
                result["total_errors"] += 1
                logger.error(error_msg)

        logger.info(
            f"Case deployment complete: {result['total_created']} created, "
            f"{result['total_errors']} errors"
        )

        return result

    def _build_case_payload(
        self,
        title: str,
        description: str,
        priority: str,
        vertical_name: str,
        linked_resources: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Build a case payload.

        Args:
            title: Case title.
            description: Case description.
            priority: Case priority.
            vertical_name: Vertical name for tagging.
            linked_resources: Optional linked resources.

        Returns:
            Case payload ready for API submission.
        """
        # The Datadog Case Management API v2 requires:
        # - data.type = "case"
        # - data.attributes: title, priority (P1-P5), type (STANDARD)
        # - data.relationships.project: must reference an existing project
        attributes = {
            "title": title,
            "priority": priority,
            "type": "STANDARD",
        }

        # Description goes in attributes if the API supports it
        if description:
            attributes["description"] = description

        payload = {
            "data": {
                "type": "case",
                "attributes": attributes,
            }
        }

        # Project relationship is required — check linked_resources for project_id
        relationships = {}
        project_id = (linked_resources or {}).get("project_id")
        if project_id:
            relationships["project"] = {
                "data": {
                    "type": "project",
                    "id": project_id,
                }
            }

        if linked_resources and "incident_id" in linked_resources:
            relationships["incident"] = {
                "data": {
                    "type": "incidents",
                    "id": linked_resources["incident_id"],
                }
            }

        if relationships:
            payload["data"]["relationships"] = relationships

        return payload
