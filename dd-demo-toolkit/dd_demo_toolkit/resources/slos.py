"""
SLO resource manager for dd-demo-toolkit.

Handles deployment, deletion, and listing of Datadog SLOs for verticals.
"""

import logging
from pathlib import Path
from typing import Dict, List, Any, Optional

import yaml

from dd_demo_toolkit.utils.dd_api import DatadogAPIClient


logger = logging.getLogger(__name__)


class SLOManager:
    """Manages deployment and lifecycle of Datadog SLOs."""

    def __init__(self) -> None:
        """Initialize the SLO manager."""
        pass

    def deploy(
        self,
        vertical_path: str,
        api_client: DatadogAPIClient,
        tags: Optional[Dict[str, str]] = None,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """
        Deploy SLOs from a vertical.

        Reads slos.yaml from the vertical path and creates SLOs via API.
        Each SLO definition should include: name, type, thresholds, description, etc.

        Args:
            vertical_path: Path to the vertical directory.
            api_client: Datadog API client instance.
            tags: Additional tags to inject (vertical and dd-demo-toolkit tags added automatically).
            dry_run: If True, skip API calls and return what would be created.

        Returns:
            Dictionary with keys:
            - created_ids: List of created SLO IDs
            - created_names: List of created SLO names
            - errors: List of error messages
            - total_created: Number of successfully created SLOs
            - total_errors: Number of failed deployments
        """
        vertical_path_obj = Path(vertical_path)
        slos_file = vertical_path_obj / "slos.yaml"

        result = {
            "created_ids": [],
            "created_names": [],
            "errors": [],
            "total_created": 0,
            "total_errors": 0,
        }

        if not slos_file.exists():
            logger.info(f"No slos.yaml file found at {slos_file}")
            return result

        vertical_name = vertical_path_obj.name

        try:
            with open(slos_file, "r") as f:
                config = yaml.safe_load(f)
        except yaml.YAMLError as e:
            error_msg = f"Failed to parse slos.yaml: {str(e)}"
            result["errors"].append(error_msg)
            result["total_errors"] += 1
            logger.error(error_msg)
            return result
        except IOError as e:
            error_msg = f"Failed to read slos.yaml: {str(e)}"
            result["errors"].append(error_msg)
            result["total_errors"] += 1
            logger.error(error_msg)
            return result

        if not config:
            logger.info("No SLOs defined in slos.yaml")
            return result

        slos = config if isinstance(config, list) else config.get("slos", [])
        if not slos:
            logger.info("No SLOs found in slos.yaml")
            return result

        logger.info(f"Deploying {len(slos)} SLO(s) for vertical '{vertical_name}'")

        for idx, slo_config in enumerate(slos):
            try:
                # Build the SLO payload
                payload = self._build_slo_payload(slo_config, vertical_name, tags)

                if dry_run:
                    slo_name = payload.get("name", f"slo-{idx}")
                    logger.info(f"[DRY RUN] Would create SLO '{slo_name}'")
                    result["created_names"].append(slo_name)
                    result["total_created"] += 1
                else:
                    # Create via API
                    response = api_client.create_slo(payload)
                    # SLO API returns {"data": [{"id": ..., "name": ...}]}
                    slo_data = response.get("data", [response])
                    if isinstance(slo_data, list) and slo_data:
                        slo_data = slo_data[0]
                    elif isinstance(slo_data, dict):
                        pass
                    else:
                        slo_data = response
                    slo_id = slo_data.get("id")
                    slo_name = slo_data.get("name", "")

                    if slo_id:
                        result["created_ids"].append(slo_id)
                        result["created_names"].append(slo_name)
                        result["total_created"] += 1
                        logger.info(f"Created SLO '{slo_name}' with ID {slo_id}")
                    else:
                        error_msg = f"No SLO ID in response for SLO {idx}"
                        result["errors"].append(error_msg)
                        result["total_errors"] += 1
                        logger.error(error_msg)

            except KeyError as e:
                error_msg = f"SLO {idx} missing required field: {str(e)}"
                result["errors"].append(error_msg)
                result["total_errors"] += 1
                logger.error(error_msg)
            except RuntimeError as e:
                error_msg = f"API error deploying SLO {idx}: {str(e)}"
                result["errors"].append(error_msg)
                result["total_errors"] += 1
                logger.error(error_msg)
            except Exception as e:
                error_msg = f"Unexpected error deploying SLO {idx}: {str(e)}"
                result["errors"].append(error_msg)
                result["total_errors"] += 1
                logger.error(error_msg)

        logger.info(
            f"SLO deployment complete: {result['total_created']} created, "
            f"{result['total_errors']} errors"
        )

        return result

    def _build_slo_payload(
        self,
        config: Dict[str, Any],
        vertical_name: str,
        additional_tags: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """
        Build an SLO payload from config.

        Args:
            config: SLO configuration dict.
            vertical_name: Vertical name for tagging.
            additional_tags: Additional tags to add.

        Returns:
            SLO payload ready for API submission.

        Raises:
            KeyError: If required fields are missing.
        """
        # Validate required fields
        required = ["name", "type"]
        for field in required:
            if field not in config:
                raise KeyError(f"Required field '{field}' missing")

        payload = {
            "name": config["name"],
            "type": config["type"],
        }

        # Add optional fields if present
        if "description" in config:
            payload["description"] = config["description"]
        if "groups" in config:
            payload["groups"] = config["groups"]
        if "query" in config:
            payload["query"] = config["query"]

        # Build thresholds array — the SLO API requires this format:
        # "thresholds": [{"target": 99.9, "timeframe": "30d"}]
        if "thresholds" in config:
            # Already in correct format
            payload["thresholds"] = config["thresholds"]
        elif "target" in config:
            # Convert flat target/timeframe to thresholds array
            threshold = {"target": config["target"]}
            if "timeframe" in config:
                threshold["timeframe"] = config["timeframe"]
            else:
                threshold["timeframe"] = "30d"
            if "target_display" in config:
                threshold["target_display"] = config["target_display"]
            if "warning" in config:
                threshold["warning"] = config["warning"]
            payload["thresholds"] = [threshold]

        # Add tags
        tags = config.get("tags", []) if isinstance(config.get("tags"), list) else []
        tags.append(f"vertical:{vertical_name}")
        tags.append("dd-demo-toolkit:true")

        if additional_tags:
            for key, value in additional_tags.items():
                tags.append(f"{key}:{value}")

        payload["tags"] = list(dict.fromkeys(tags))

        return payload

    def teardown(
        self,
        api_client: DatadogAPIClient,
        vertical_name: Optional[str],
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """
        Delete SLOs tagged by the toolkit.

        Args:
            api_client: Datadog API client instance.
            vertical_name: Name of the vertical to clean up. If ``None``, every
                SLO tagged ``dd-demo-toolkit:true`` is deleted regardless of
                vertical (orphan-sweep mode).
            dry_run: If True, skip API calls and return what would be deleted.

        Returns:
            Dictionary with keys:
            - deleted_ids: List of deleted SLO IDs
            - deleted_names: List of deleted SLO names
            - errors: List of error messages
            - total_deleted: Number of successfully deleted SLOs
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
            slos_response = api_client._request("GET", "/api/v1/slo")
            slo_list = slos_response.get("data", [])
        except RuntimeError as e:
            error_msg = f"Failed to list SLOs: {str(e)}"
            result["errors"].append(error_msg)
            result["total_errors"] += 1
            logger.error(error_msg)
            return result

        # Filter by vertical tag, or by toolkit marker when no vertical given.
        if vertical_name is None:
            slos_to_delete = [
                s for s in slo_list
                if "dd-demo-toolkit:true" in s.get("tags", [])
            ]
            scope_label = "all toolkit-managed verticals"
        else:
            target_tag = f"vertical:{vertical_name}"
            slos_to_delete = [
                s for s in slo_list
                if target_tag in s.get("tags", [])
            ]
            scope_label = f"vertical '{vertical_name}'"

        logger.info(
            f"Found {len(slos_to_delete)} SLO(s) to delete for {scope_label}"
        )

        for slo in slos_to_delete:
            slo_id = slo.get("id")
            slo_name = slo.get("name", "")

            try:
                if dry_run:
                    logger.info(f"[DRY RUN] Would delete SLO {slo_id}")
                    result["deleted_ids"].append(slo_id)
                    result["deleted_names"].append(slo_name)
                    result["total_deleted"] += 1
                else:
                    api_client.delete_slo(slo_id)
                    result["deleted_ids"].append(slo_id)
                    result["deleted_names"].append(slo_name)
                    result["total_deleted"] += 1
                    logger.info(f"Deleted SLO {slo_id} ({slo_name})")
            except RuntimeError as e:
                error_msg = f"Failed to delete SLO {slo_id}: {str(e)}"
                result["errors"].append(error_msg)
                result["total_errors"] += 1
                logger.error(error_msg)

        logger.info(
            f"SLO teardown complete: {result['total_deleted']} deleted, "
            f"{result['total_errors']} errors"
        )

        return result

    def list_deployed(
        self,
        api_client: DatadogAPIClient,
        vertical_name: str,
    ) -> Dict[str, Any]:
        """
        List all SLOs deployed for a vertical.

        Args:
            api_client: Datadog API client instance.
            vertical_name: Name of the vertical.

        Returns:
            Dictionary with keys:
            - slos: List of SLO objects
            - total: Count of SLOs
            - error: Error message if listing failed, None otherwise
        """
        result = {
            "slos": [],
            "total": 0,
            "error": None,
        }

        try:
            slos_response = api_client._request("GET", "/api/v1/slo")
            slo_list = slos_response.get("data", [])
        except RuntimeError as e:
            result["error"] = f"Failed to list SLOs: {str(e)}"
            logger.error(result["error"])
            return result

        target_tag = f"vertical:{vertical_name}"
        deployed = [
            s for s in slo_list
            if target_tag in s.get("tags", [])
        ]

        result["slos"] = deployed
        result["total"] = len(deployed)
        logger.info(f"Found {result['total']} SLO(s) for vertical '{vertical_name}'")

        return result
