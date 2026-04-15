"""
Dashboard resource manager for dd-demo-toolkit.

Handles deployment, deletion, and listing of Datadog dashboards for verticals.
"""

import json
import logging
from pathlib import Path
from typing import Dict, List, Any, Optional

from dd_demo_toolkit.utils.dd_api import DatadogAPIClient


logger = logging.getLogger(__name__)


class DashboardManager:
    """Manages deployment and lifecycle of Datadog dashboards."""

    def __init__(self) -> None:
        """Initialize the dashboard manager."""
        pass

    def deploy(
        self,
        vertical_path: str,
        api_client: DatadogAPIClient,
        tags: Optional[Dict[str, str]] = None,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """
        Deploy dashboards from a vertical.

        Reads all JSON files from {vertical_path}/dashboards/, injects tags, and creates
        via API. Tags include vertical name and dd-demo-toolkit flag.

        Args:
            vertical_path: Path to the vertical directory.
            api_client: Datadog API client instance.
            tags: Additional tags to inject (vertical and dd-demo-toolkit tags added automatically).
            dry_run: If True, skip API calls and return what would be created.

        Returns:
            Dictionary with keys:
            - created_ids: List of created dashboard IDs
            - created_urls: List of URLs for created dashboards
            - errors: List of error messages
            - total_created: Number of successfully created dashboards
            - total_errors: Number of failed deployments
        """
        vertical_path_obj = Path(vertical_path)
        dashboards_dir = vertical_path_obj / "dashboards"

        result = {
            "created_ids": [],
            "created_urls": [],
            "errors": [],
            "total_created": 0,
            "total_errors": 0,
        }

        if not dashboards_dir.exists():
            logger.info(f"No dashboards directory found at {dashboards_dir}")
            return result

        # Discover all JSON files
        json_files = sorted(dashboards_dir.glob("*.json"))
        if not json_files:
            logger.info(f"No dashboard JSON files found in {dashboards_dir}")
            return result

        vertical_name = vertical_path_obj.name
        logger.info(f"Deploying {len(json_files)} dashboard(s) for vertical '{vertical_name}'")

        for json_file in json_files:
            try:
                with open(json_file, "r") as f:
                    payload = json.load(f)

                # Inject tags
                if "tags" not in payload:
                    payload["tags"] = []
                if not isinstance(payload["tags"], list):
                    payload["tags"] = []

                payload["tags"].append(f"vertical:{vertical_name}")
                payload["tags"].append("dd-demo-toolkit:true")

                if tags:
                    for key, value in tags.items():
                        payload["tags"].append(f"{key}:{value}")

                if dry_run:
                    logger.info(f"[DRY RUN] Would create dashboard from {json_file.name}")
                    # Extract ID/name from payload for dry run
                    dashboard_name = payload.get("title", json_file.stem)
                    result["created_ids"].append(f"[dry-run] {dashboard_name}")
                    result["total_created"] += 1
                else:
                    # Create via API
                    response = api_client.create_dashboard(payload)
                    dashboard_id = response.get("id")
                    if dashboard_id:
                        result["created_ids"].append(dashboard_id)
                        # Build dashboard URL
                        site = api_client.site
                        url = f"https://app.{site}/dashboard/{dashboard_id}"
                        result["created_urls"].append(url)
                        result["total_created"] += 1
                        logger.info(f"Created dashboard '{json_file.name}' with ID {dashboard_id}")
                    else:
                        error_msg = f"No dashboard ID in response for {json_file.name}"
                        result["errors"].append(error_msg)
                        result["total_errors"] += 1
                        logger.error(error_msg)

            except json.JSONDecodeError as e:
                error_msg = f"Invalid JSON in {json_file.name}: {str(e)}"
                result["errors"].append(error_msg)
                result["total_errors"] += 1
                logger.error(error_msg)
            except RuntimeError as e:
                error_msg = f"API error deploying {json_file.name}: {str(e)}"
                result["errors"].append(error_msg)
                result["total_errors"] += 1
                logger.error(error_msg)
            except Exception as e:
                error_msg = f"Unexpected error deploying {json_file.name}: {str(e)}"
                result["errors"].append(error_msg)
                result["total_errors"] += 1
                logger.error(error_msg)

        logger.info(
            f"Dashboard deployment complete: {result['total_created']} created, "
            f"{result['total_errors']} errors"
        )

        return result

    def teardown(
        self,
        api_client: DatadogAPIClient,
        vertical_name: str,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """
        Delete all dashboards tagged with a vertical.

        Args:
            api_client: Datadog API client instance.
            vertical_name: Name of the vertical to clean up.
            dry_run: If True, skip API calls and return what would be deleted.

        Returns:
            Dictionary with keys:
            - deleted_ids: List of deleted dashboard IDs
            - errors: List of error messages
            - total_deleted: Number of successfully deleted dashboards
            - total_errors: Number of failed deletions
        """
        result = {
            "deleted_ids": [],
            "errors": [],
            "total_deleted": 0,
            "total_errors": 0,
        }

        try:
            dashboards = api_client.list_dashboards()
            dashboard_list = dashboards.get("dashboards", [])
        except RuntimeError as e:
            error_msg = f"Failed to list dashboards: {str(e)}"
            result["errors"].append(error_msg)
            result["total_errors"] += 1
            logger.error(error_msg)
            return result

        # Filter by vertical tag
        target_tag = f"vertical:{vertical_name}"
        dashboards_to_delete = [
            d for d in dashboard_list
            if target_tag in d.get("tags", [])
        ]

        logger.info(
            f"Found {len(dashboards_to_delete)} dashboard(s) to delete for "
            f"vertical '{vertical_name}'"
        )

        for dashboard in dashboards_to_delete:
            dashboard_id = dashboard.get("id")
            try:
                if dry_run:
                    logger.info(f"[DRY RUN] Would delete dashboard {dashboard_id}")
                    result["deleted_ids"].append(dashboard_id)
                    result["total_deleted"] += 1
                else:
                    api_client.delete_dashboard(dashboard_id)
                    result["deleted_ids"].append(dashboard_id)
                    result["total_deleted"] += 1
                    logger.info(f"Deleted dashboard {dashboard_id}")
            except RuntimeError as e:
                error_msg = f"Failed to delete dashboard {dashboard_id}: {str(e)}"
                result["errors"].append(error_msg)
                result["total_errors"] += 1
                logger.error(error_msg)

        logger.info(
            f"Dashboard teardown complete: {result['total_deleted']} deleted, "
            f"{result['total_errors']} errors"
        )

        return result

    def list_deployed(
        self,
        api_client: DatadogAPIClient,
        vertical_name: str,
    ) -> Dict[str, Any]:
        """
        List all dashboards deployed for a vertical.

        Args:
            api_client: Datadog API client instance.
            vertical_name: Name of the vertical.

        Returns:
            Dictionary with keys:
            - dashboards: List of dashboard objects
            - total: Count of dashboards
            - error: Error message if listing failed, None otherwise
        """
        result = {
            "dashboards": [],
            "total": 0,
            "error": None,
        }

        try:
            dashboards = api_client.list_dashboards()
            dashboard_list = dashboards.get("dashboards", [])
        except RuntimeError as e:
            result["error"] = f"Failed to list dashboards: {str(e)}"
            logger.error(result["error"])
            return result

        target_tag = f"vertical:{vertical_name}"
        deployed = [
            d for d in dashboard_list
            if target_tag in d.get("tags", [])
        ]

        result["dashboards"] = deployed
        result["total"] = len(deployed)
        logger.info(f"Found {result['total']} dashboard(s) for vertical '{vertical_name}'")

        return result
