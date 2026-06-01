"""
Notebook resource manager for dd-demo-toolkit.

Handles deployment, deletion, and listing of Datadog notebooks for verticals.
"""

import logging
from pathlib import Path
from typing import Dict, List, Any, Optional

import yaml

from dd_demo_toolkit.utils.dd_api import DatadogAPIClient


logger = logging.getLogger(__name__)


class NotebookManager:
    """Manages deployment and lifecycle of Datadog notebooks."""

    def __init__(self) -> None:
        """Initialize the notebook manager."""
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
        Deploy notebooks from a vertical.

        Reads notebooks.yaml from the vertical path and creates notebooks via API.
        Each notebook definition should include cells (markdown, timeseries, query_value, etc.).

        Args:
            vertical_path: Path to the vertical directory.
            api_client: Datadog API client instance.
            tags: Additional tags to inject (vertical and dd-demo-toolkit tags added automatically).
            dry_run: If True, skip API calls and return what would be created.

        Returns:
            Dictionary with keys:
            - created_ids: List of created notebook IDs
            - created_names: List of created notebook names
            - errors: List of error messages
            - total_created: Number of successfully created notebooks
            - total_errors: Number of failed deployments
        """
        vertical_path_obj = Path(vertical_path)
        notebooks_file = vertical_path_obj / "notebooks.yaml"

        result = {
            "created_ids": [],
            "created_names": [],
            "errors": [],
            "total_created": 0,
            "total_errors": 0,
        }

        if not notebooks_file.exists():
            logger.info(f"No notebooks.yaml file found at {notebooks_file}")
            return result

        if vertical_name is None:
            vertical_name = vertical_path_obj.name

        try:
            with open(notebooks_file, "r") as f:
                config = yaml.safe_load(f)
        except yaml.YAMLError as e:
            error_msg = f"Failed to parse notebooks.yaml: {str(e)}"
            result["errors"].append(error_msg)
            result["total_errors"] += 1
            logger.error(error_msg)
            return result
        except IOError as e:
            error_msg = f"Failed to read notebooks.yaml: {str(e)}"
            result["errors"].append(error_msg)
            result["total_errors"] += 1
            logger.error(error_msg)
            return result

        if not config:
            logger.info("No notebooks defined in notebooks.yaml")
            return result

        notebooks = config if isinstance(config, list) else config.get("notebooks", [])
        if not notebooks:
            logger.info("No notebooks found in notebooks.yaml")
            return result

        logger.info(f"Deploying {len(notebooks)} notebook(s) for vertical '{vertical_name}'")

        for idx, notebook_config in enumerate(notebooks):
            try:
                # Build the notebook payload
                payload = self._build_notebook_payload(notebook_config, vertical_name, tags)

                if dry_run:
                    notebook_name = payload.get("name", f"notebook-{idx}")
                    logger.info(f"[DRY RUN] Would create notebook '{notebook_name}'")
                    result["created_names"].append(notebook_name)
                    result["total_created"] += 1
                else:
                    # Create via API
                    response = api_client.create_notebook(payload)
                    # Notebooks API returns {data: {id: ..., attributes: {name: ...}}}
                    notebook_data = response.get("data", response)
                    notebook_id = notebook_data.get("id")
                    notebook_name = notebook_data.get("attributes", {}).get("name", "")

                    if notebook_id:
                        result["created_ids"].append(notebook_id)
                        result["created_names"].append(notebook_name)
                        result["total_created"] += 1
                        logger.info(f"Created notebook '{notebook_name}' with ID {notebook_id}")
                    else:
                        error_msg = f"No notebook ID in response for notebook {idx}"
                        result["errors"].append(error_msg)
                        result["total_errors"] += 1
                        logger.error(error_msg)

            except KeyError as e:
                error_msg = f"Notebook {idx} missing required field: {str(e)}"
                result["errors"].append(error_msg)
                result["total_errors"] += 1
                logger.error(error_msg)
            except RuntimeError as e:
                error_msg = f"API error deploying notebook {idx}: {str(e)}"
                result["errors"].append(error_msg)
                result["total_errors"] += 1
                logger.error(error_msg)
            except Exception as e:
                error_msg = f"Unexpected error deploying notebook {idx}: {str(e)}"
                result["errors"].append(error_msg)
                result["total_errors"] += 1
                logger.error(error_msg)

        logger.info(
            f"Notebook deployment complete: {result['total_created']} created, "
            f"{result['total_errors']} errors"
        )

        return result

    def _build_notebook_payload(
        self,
        config: Dict[str, Any],
        vertical_name: str,
        additional_tags: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """
        Build a notebook payload from config.

        The Datadog Notebooks API v1 expects:
        {
          "data": {
            "attributes": {
              "name": "...",
              "cells": [...],
              "time": {"live_span": "1h"},
              "status": "published"
            },
            "type": "notebooks"
          }
        }

        Each cell must be:
        {
          "attributes": {
            "definition": {
              "type": "markdown",
              "text": "..."
            }
          },
          "type": "notebook_cells"
        }

        Args:
            config: Notebook configuration dict.
            vertical_name: Vertical name for tagging.
            additional_tags: Additional tags to add.

        Returns:
            Notebook payload ready for API submission.

        Raises:
            KeyError: If required fields are missing.
        """
        if "name" not in config:
            raise KeyError("Required field 'name' missing")

        # Build cells - convert from simple config format to API format
        cells = []
        if "cells" in config and isinstance(config["cells"], list):
            for cell in config["cells"]:
                cells.append(cell)
        else:
            # Auto-generate a title cell from name and description
            title_text = f"# {config['name']}"
            if "description" in config:
                title_text += f"\n\n{config['description']}"
            cells.append({
                "attributes": {
                    "definition": {
                        "type": "markdown",
                        "text": title_text,
                    }
                },
                "type": "notebook_cells",
            })

        # Determine time range
        time_range = config.get("time_range", "1h")
        time_obj = {"live_span": time_range}

        # Build tags
        tags = config.get("tags", []) if isinstance(config.get("tags"), list) else []
        if f"vertical:{vertical_name}" not in tags:
            tags.append(f"vertical:{vertical_name}")
        if "dd-demo-toolkit:true" not in tags:
            tags.append("dd-demo-toolkit:true")
        if additional_tags:
            for key, value in additional_tags.items():
                tags.append(f"{key}:{value}")

        # Wrap in Notebooks API v1 envelope
        payload = {
            "data": {
                "attributes": {
                    "name": config["name"],
                    "cells": cells,
                    "time": time_obj,
                    "status": "published",
                    "metadata": {
                        "type": config.get("type", "investigation"),
                    },
                    "tags": tags,
                },
                "type": "notebooks",
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
        Delete notebooks tagged by the toolkit.

        Args:
            api_client: Datadog API client instance.
            vertical_name: Name of the vertical to clean up. If ``None``, every
                notebook tagged ``dd-demo-toolkit:true`` is deleted regardless
                of vertical (orphan-sweep mode).
            dry_run: If True, skip API calls and return what would be deleted.

        Returns:
            Dictionary with keys:
            - deleted_ids: List of deleted notebook IDs
            - deleted_names: List of deleted notebook names
            - errors: List of error messages
            - total_deleted: Number of successfully deleted notebooks
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
            notebooks = api_client.list_notebooks()
            notebook_list = notebooks.get("data", [])
        except RuntimeError as e:
            error_msg = f"Failed to list notebooks: {str(e)}"
            result["errors"].append(error_msg)
            result["total_errors"] += 1
            logger.error(error_msg)
            return result

        # Filter by vertical tag, or by toolkit marker when no vertical given.
        # Tags can be None on some notebooks — coalesce to [] defensively.
        if vertical_name is None:
            notebooks_to_delete = [
                n for n in notebook_list
                if "dd-demo-toolkit:true" in (n.get("attributes", {}).get("tags") or [])
            ]
            scope_label = "all toolkit-managed verticals"
        else:
            target_tag = f"vertical:{vertical_name}"
            notebooks_to_delete = [
                n for n in notebook_list
                if target_tag in (n.get("attributes", {}).get("tags") or [])
            ]
            scope_label = f"vertical '{vertical_name}'"

        logger.info(
            f"Found {len(notebooks_to_delete)} notebook(s) to delete for {scope_label}"
        )

        for notebook in notebooks_to_delete:
            notebook_id = notebook.get("id")
            notebook_name = notebook.get("attributes", {}).get("name", "")

            try:
                if dry_run:
                    logger.info(f"[DRY RUN] Would delete notebook {notebook_id}")
                    result["deleted_ids"].append(notebook_id)
                    result["deleted_names"].append(notebook_name)
                    result["total_deleted"] += 1
                else:
                    api_client.delete_notebook(notebook_id)
                    result["deleted_ids"].append(notebook_id)
                    result["deleted_names"].append(notebook_name)
                    result["total_deleted"] += 1
                    logger.info(f"Deleted notebook {notebook_id} ({notebook_name})")
            except RuntimeError as e:
                error_msg = f"Failed to delete notebook {notebook_id}: {str(e)}"
                result["errors"].append(error_msg)
                result["total_errors"] += 1
                logger.error(error_msg)

        logger.info(
            f"Notebook teardown complete: {result['total_deleted']} deleted, "
            f"{result['total_errors']} errors"
        )

        return result

    def list_deployed(
        self,
        api_client: DatadogAPIClient,
        vertical_name: str,
    ) -> Dict[str, Any]:
        """
        List all notebooks deployed for a vertical.

        Args:
            api_client: Datadog API client instance.
            vertical_name: Name of the vertical.

        Returns:
            Dictionary with keys:
            - notebooks: List of notebook objects
            - total: Count of notebooks
            - error: Error message if listing failed, None otherwise
        """
        result = {
            "notebooks": [],
            "total": 0,
            "error": None,
        }

        try:
            notebooks = api_client.list_notebooks()
            notebook_list = notebooks.get("data", [])
        except RuntimeError as e:
            result["error"] = f"Failed to list notebooks: {str(e)}"
            logger.error(result["error"])
            return result

        target_tag = f"vertical:{vertical_name}"
        deployed = [
            n for n in notebook_list
            if target_tag in (n.get("attributes", {}).get("tags") or [])
        ]

        result["notebooks"] = deployed
        result["total"] = len(deployed)
        logger.info(f"Found {result['total']} notebook(s) for vertical '{vertical_name}'")

        return result
