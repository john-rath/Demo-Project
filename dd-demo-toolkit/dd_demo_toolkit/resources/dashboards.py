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

# Datadog has no API to favorite/star a dashboard (per-user UI preference).
# Instead we group each vertical's toolkit dashboards into a shared manual
# "dashboard list", auto-created on deploy and removed on teardown, so they sit
# one click away in the left nav for every user. All toolkit dashboards use
# `layout_type: ordered` (STYLE_GUIDE §4.4) → the `custom_timeboard` type.
_DASHBOARD_LIST_PREFIX = "dd-demo-toolkit"


def _dashboard_list_name(vertical_name: str) -> str:
    """Stable name for a vertical's toolkit dashboard list (deploy + teardown
    must agree on this so the list is reused, not duplicated)."""
    return f"{_DASHBOARD_LIST_PREFIX} — {vertical_name}"


def _dashboard_type(payload: Dict[str, Any]) -> str:
    """Datadog dashboard-list item type for a dashboard payload."""
    return "custom_screenboard" if payload.get("layout_type") == "free" else "custom_timeboard"


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
        vertical_name: Optional[str] = None,
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
            "dashboard_list_id": None,
        }
        # (id, type) of each created dashboard, for grouping into the list.
        created_items: List[Dict[str, str]] = []

        if not dashboards_dir.exists():
            logger.info(f"No dashboards directory found at {dashboards_dir}")
            return result

        # Discover all JSON files
        json_files = sorted(dashboards_dir.glob("*.json"))
        if not json_files:
            logger.info(f"No dashboard JSON files found in {dashboards_dir}")
            return result

        if vertical_name is None:
            vertical_name = vertical_path_obj.name
        logger.info(f"Deploying {len(json_files)} dashboard(s) for vertical '{vertical_name}'")

        for json_file in json_files:
            try:
                with open(json_file, "r") as f:
                    payload = json.load(f)

                # Inject tags using only allowed tag keys.
                # Many Datadog orgs restrict tag keys (e.g. only "team" and "ai").
                # We use "team" as a safe namespace for our identification tags.
                if "tags" not in payload:
                    payload["tags"] = []
                if not isinstance(payload["tags"], list):
                    payload["tags"] = []

                # Add identification tags using allowed key format
                payload["tags"].append(f"team:dd-demo-{vertical_name}")

                if tags:
                    for key, value in tags.items():
                        payload["tags"].append(f"{key}:{value}")

                # Deduplicate tags
                payload["tags"] = list(dict.fromkeys(payload["tags"]))

                # Embed a toolkit marker in the description so teardown can
                # identify our dashboards.  The Datadog list-dashboards API
                # does NOT return tags, but it does return descriptions.
                marker = f"[dd-demo-toolkit:{vertical_name}]"
                desc = payload.get("description", "") or ""
                if marker not in desc:
                    payload["description"] = (desc + f"\n\n{marker}").strip()

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
                        created_items.append({"id": dashboard_id, "type": _dashboard_type(payload)})
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

        # Group the created dashboards into a shared, per-vertical dashboard
        # list (the API-supported stand-in for "favorites"). Non-fatal: the
        # dashboards themselves deployed fine even if listing fails, so a
        # grouping error is logged as a warning, not a deploy error.
        if not dry_run and created_items:
            list_name = _dashboard_list_name(vertical_name)
            try:
                list_id = self._ensure_dashboard_list(api_client, list_name)
                api_client.add_dashboards_to_list(list_id, created_items)
                result["dashboard_list_id"] = list_id
                logger.info(
                    "Added %d dashboard(s) to dashboard list '%s' (id=%s)",
                    len(created_items), list_name, list_id,
                )
            except (RuntimeError, KeyError) as e:
                logger.warning(
                    "Dashboards deployed, but grouping into list '%s' was skipped: %s",
                    list_name, e,
                )

        logger.info(
            f"Dashboard deployment complete: {result['total_created']} created, "
            f"{result['total_errors']} errors"
        )

        return result

    @staticmethod
    def _ensure_dashboard_list(api_client: DatadogAPIClient, name: str) -> Any:
        """Return the ID of the manual dashboard list named ``name``, reusing an
        existing one (idempotent across re-deploys) or creating it if absent."""
        existing = api_client.list_dashboard_lists().get("dashboard_lists", []) or []
        for lst in existing:
            if lst.get("name") == name and lst.get("id") is not None:
                return lst["id"]
        created = api_client.create_dashboard_list(name)
        list_id = created.get("id")
        if list_id is None:
            raise RuntimeError(f"create_dashboard_list returned no id for '{name}'")
        return list_id

    def teardown(
        self,
        api_client: DatadogAPIClient,
        vertical_name: Optional[str],
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """
        Delete dashboards marked as toolkit-managed.

        Args:
            api_client: Datadog API client instance.
            vertical_name: Name of the vertical to clean up. If ``None``, every
                dashboard whose description contains the ``[dd-demo-toolkit:``
                marker is deleted regardless of vertical (orphan-sweep mode).
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
            "deleted_list_ids": [],
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

        # NOTE: The list-dashboards API does NOT return tags, so we use the
        # description marker injected during deploy instead. The vertical-
        # specific form is "[dd-demo-toolkit:<vertical>]"; for all-verticals
        # sweeps we match the common prefix "[dd-demo-toolkit:".
        if vertical_name is None:
            marker = "[dd-demo-toolkit:"
            scope_label = "all toolkit-managed verticals"
        else:
            marker = f"[dd-demo-toolkit:{vertical_name}]"
            scope_label = f"vertical '{vertical_name}'"
        dashboards_to_delete = [
            d for d in dashboard_list
            if marker in (d.get("description") or "")
        ]

        logger.info(
            f"Found {len(dashboards_to_delete)} dashboard(s) to delete for {scope_label}"
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

        # Remove the per-vertical dashboard list(s) created at deploy time.
        # Exact-name match for a single vertical; prefix match for the
        # all-verticals sweep. Non-fatal — a leftover empty list is cosmetic,
        # and deleting a list never deletes its dashboards.
        try:
            lists = api_client.list_dashboard_lists().get("dashboard_lists", []) or []
            if vertical_name is None:
                targets = [l for l in lists
                           if str(l.get("name", "")).startswith(_DASHBOARD_LIST_PREFIX)]
            else:
                want = _dashboard_list_name(vertical_name)
                targets = [l for l in lists if l.get("name") == want]
            for lst in targets:
                lid = lst.get("id")
                if lid is None:
                    continue
                if dry_run:
                    logger.info(f"[DRY RUN] Would delete dashboard list {lid} ('{lst.get('name')}')")
                else:
                    api_client.delete_dashboard_list(lid)
                    logger.info(f"Deleted dashboard list {lid} ('{lst.get('name')}')")
                result["deleted_list_ids"].append(lid)
        except RuntimeError as e:
            logger.warning("Dashboard-list cleanup skipped: %s", e)

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

        marker = f"[dd-demo-toolkit:{vertical_name}]"
        deployed = [
            d for d in dashboard_list
            if marker in (d.get("description") or "")
        ]

        result["dashboards"] = deployed
        result["total"] = len(deployed)
        logger.info(f"Found {result['total']} dashboard(s) for vertical '{vertical_name}'")

        return result
