"""
Monitor resource manager for dd-demo-toolkit.

Handles deployment, deletion, and listing of Datadog monitors for verticals.
"""

import logging
import re
from pathlib import Path
from typing import Dict, List, Any, Optional

import yaml

from dd_demo_toolkit.utils.dd_api import DatadogAPIClient


logger = logging.getLogger(__name__)


class MonitorManager:
    """Manages deployment and lifecycle of Datadog monitors."""

    def __init__(self) -> None:
        """Initialize the monitor manager."""
        pass

    def deploy(
        self,
        vertical_path: str,
        api_client: DatadogAPIClient,
        tags: Optional[Dict[str, str]] = None,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """
        Deploy monitors from a vertical.

        Reads monitors.yaml from the vertical path and creates monitors via API.
        Each monitor definition should have: name, type, query, message, tags, thresholds, options.

        Args:
            vertical_path: Path to the vertical directory.
            api_client: Datadog API client instance.
            tags: Additional tags to inject (vertical and dd-demo-toolkit tags added automatically).
            dry_run: If True, skip API calls and return what would be created.

        Returns:
            Dictionary with keys:
            - created_ids: List of created monitor IDs
            - created_names: List of created monitor names
            - errors: List of error messages
            - total_created: Number of successfully created monitors
            - total_errors: Number of failed deployments
        """
        vertical_path_obj = Path(vertical_path)
        monitors_file = vertical_path_obj / "monitors.yaml"

        result = {
            "created_ids": [],
            "created_names": [],
            "errors": [],
            "total_created": 0,
            "total_errors": 0,
        }

        if not monitors_file.exists():
            logger.info(f"No monitors.yaml file found at {monitors_file}")
            return result

        vertical_name = vertical_path_obj.name

        try:
            with open(monitors_file, "r") as f:
                config = yaml.safe_load(f)
        except yaml.YAMLError as e:
            error_msg = f"Failed to parse monitors.yaml: {str(e)}"
            result["errors"].append(error_msg)
            result["total_errors"] += 1
            logger.error(error_msg)
            return result
        except IOError as e:
            error_msg = f"Failed to read monitors.yaml: {str(e)}"
            result["errors"].append(error_msg)
            result["total_errors"] += 1
            logger.error(error_msg)
            return result

        if not config:
            logger.info("No monitors defined in monitors.yaml")
            return result

        monitors = config if isinstance(config, list) else config.get("monitors", [])
        if not monitors:
            logger.info("No monitors found in monitors.yaml")
            return result

        logger.info(f"Deploying {len(monitors)} monitor(s) for vertical '{vertical_name}'")

        for idx, monitor_config in enumerate(monitors):
            try:
                # Build the monitor payload
                payload = self._build_monitor_payload(monitor_config, vertical_name, tags)

                if dry_run:
                    monitor_name = payload.get("name", f"monitor-{idx}")
                    logger.info(f"[DRY RUN] Would create monitor '{monitor_name}'")
                    result["created_names"].append(monitor_name)
                    result["total_created"] += 1
                else:
                    # Create via API
                    response = api_client.create_monitor(payload)
                    monitor_id = response.get("id")
                    monitor_name = response.get("name", "")

                    if monitor_id:
                        result["created_ids"].append(monitor_id)
                        result["created_names"].append(monitor_name)
                        result["total_created"] += 1
                        logger.info(f"Created monitor '{monitor_name}' with ID {monitor_id}")
                    else:
                        error_msg = f"No monitor ID in response for monitor {idx}"
                        result["errors"].append(error_msg)
                        result["total_errors"] += 1
                        logger.error(error_msg)

            except KeyError as e:
                error_msg = f"Monitor {idx} missing required field: {str(e)}"
                result["errors"].append(error_msg)
                result["total_errors"] += 1
                logger.error(error_msg)
            except RuntimeError as e:
                error_str = str(e)
                monitor_name = monitor_config.get("name", f"monitor-{idx}")
                # Always log the raw API error for debugging
                logger.error(f"Monitor '{monitor_name}' API error (raw): {error_str}")
                if "query" in error_str and "invalid" in error_str.lower():
                    # Metrics likely don't exist in Datadog yet — not a config bug,
                    # just means the simulator needs to run first.
                    warn_msg = (
                        f"Skipped monitor '{monitor_name}': metric not yet in Datadog. "
                        f"Start the simulator and re-run setup. "
                        f"(API response: {error_str})"
                    )
                    result["errors"].append(warn_msg)
                    result["total_errors"] += 1
                    logger.warning(warn_msg)
                else:
                    error_msg = f"API error deploying monitor {idx} '{monitor_name}': {error_str}"
                    result["errors"].append(error_msg)
                    result["total_errors"] += 1
                    logger.error(error_msg)
            except Exception as e:
                error_msg = f"Unexpected error deploying monitor {idx}: {str(e)}"
                result["errors"].append(error_msg)
                result["total_errors"] += 1
                logger.error(error_msg)

        logger.info(
            f"Monitor deployment complete: {result['total_created']} created, "
            f"{result['total_errors']} errors"
        )

        return result

    def _build_monitor_payload(
        self,
        config: Dict[str, Any],
        vertical_name: str,
        additional_tags: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """
        Build a monitor payload from config.

        Args:
            config: Monitor configuration dict.
            vertical_name: Vertical name for tagging.
            additional_tags: Additional tags to add.

        Returns:
            Monitor payload ready for API submission.

        Raises:
            KeyError: If required fields are missing.
        """
        # Validate required fields
        required = ["name", "type", "query", "message"]
        for field in required:
            if field not in config:
                raise KeyError(f"Required field '{field}' missing")

        payload = {
            "name": config["name"],
            "type": config["type"],
            "query": config["query"],
            "message": config["message"],
        }

        # Build options with thresholds
        options = config.get("options", {})
        if "thresholds" in config:
            options["thresholds"] = config["thresholds"]

        # If no explicit thresholds, try to extract from the query string
        if "thresholds" not in options:
            query = config["query"]
            # Match patterns like "> 75", "< -70", "> 2000"
            match = re.search(r'([><]=?)\s*([-\d.]+)\s*$', query)
            if match:
                op, val = match.group(1), float(match.group(2))
                options["thresholds"] = {"critical": val}

        if options:
            payload["options"] = options

        # Add priority if present (1-5 integer)
        if "priority" in config:
            payload["priority"] = config["priority"]

        # Inject tags
        tags = config.get("tags", []) if isinstance(config.get("tags"), list) else []
        tags.append(f"vertical:{vertical_name}")
        tags.append("dd-demo-toolkit:true")

        if additional_tags:
            for key, value in additional_tags.items():
                tags.append(f"{key}:{value}")

        # Deduplicate
        payload["tags"] = list(dict.fromkeys(tags))

        return payload

    def teardown(
        self,
        api_client: DatadogAPIClient,
        vertical_name: Optional[str],
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """
        Delete monitors tagged by the toolkit.

        Args:
            api_client: Datadog API client instance.
            vertical_name: Name of the vertical to clean up. If ``None``, every
                monitor tagged ``dd-demo-toolkit:true`` is deleted regardless
                of vertical (orphan-sweep mode).
            dry_run: If True, skip API calls and return what would be deleted.

        Returns:
            Dictionary with keys:
            - deleted_ids: List of deleted monitor IDs
            - deleted_names: List of deleted monitor names
            - errors: List of error messages
            - total_deleted: Number of successfully deleted monitors
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
            monitors = api_client.list_monitors()
            monitor_list = monitors if isinstance(monitors, list) else monitors.get("monitors", [])
        except RuntimeError as e:
            error_msg = f"Failed to list monitors: {str(e)}"
            result["errors"].append(error_msg)
            result["total_errors"] += 1
            logger.error(error_msg)
            return result

        # Filter by vertical tag, or by toolkit marker when no vertical given.
        if vertical_name is None:
            monitors_to_delete = [
                m for m in monitor_list
                if "dd-demo-toolkit:true" in m.get("tags", [])
            ]
            scope_label = "all toolkit-managed verticals"
        else:
            target_tag = f"vertical:{vertical_name}"
            monitors_to_delete = [
                m for m in monitor_list
                if target_tag in m.get("tags", [])
            ]
            scope_label = f"vertical '{vertical_name}'"

        logger.info(
            f"Found {len(monitors_to_delete)} monitor(s) to delete for {scope_label}"
        )

        for monitor in monitors_to_delete:
            monitor_id = monitor.get("id")
            monitor_name = monitor.get("name", "")

            try:
                if dry_run:
                    logger.info(f"[DRY RUN] Would delete monitor {monitor_id}")
                    result["deleted_ids"].append(monitor_id)
                    result["deleted_names"].append(monitor_name)
                    result["total_deleted"] += 1
                else:
                    api_client.delete_monitor(monitor_id)
                    result["deleted_ids"].append(monitor_id)
                    result["deleted_names"].append(monitor_name)
                    result["total_deleted"] += 1
                    logger.info(f"Deleted monitor {monitor_id} ({monitor_name})")
            except RuntimeError as e:
                error_msg = f"Failed to delete monitor {monitor_id}: {str(e)}"
                result["errors"].append(error_msg)
                result["total_errors"] += 1
                logger.error(error_msg)

        logger.info(
            f"Monitor teardown complete: {result['total_deleted']} deleted, "
            f"{result['total_errors']} errors"
        )

        return result

    def list_deployed(
        self,
        api_client: DatadogAPIClient,
        vertical_name: str,
    ) -> Dict[str, Any]:
        """
        List all monitors deployed for a vertical.

        Args:
            api_client: Datadog API client instance.
            vertical_name: Name of the vertical.

        Returns:
            Dictionary with keys:
            - monitors: List of monitor objects
            - total: Count of monitors
            - error: Error message if listing failed, None otherwise
        """
        result = {
            "monitors": [],
            "total": 0,
            "error": None,
        }

        try:
            monitors = api_client.list_monitors()
            monitor_list = monitors if isinstance(monitors, list) else monitors.get("monitors", [])
        except RuntimeError as e:
            result["error"] = f"Failed to list monitors: {str(e)}"
            logger.error(result["error"])
            return result

        target_tag = f"vertical:{vertical_name}"
        deployed = [
            m for m in monitor_list
            if target_tag in m.get("tags", [])
        ]

        result["monitors"] = deployed
        result["total"] = len(deployed)
        logger.info(f"Found {result['total']} monitor(s) for vertical '{vertical_name}'")

        return result
