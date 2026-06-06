"""
Datadog Teams resource manager for dd-demo-toolkit.

Creates/deletes a per-vertical Datadog Team (handle: dd-demo-<vertical>) and
adds the current APP-key user as a member so team-scoped custom views,
Dashboard List filters, and Service Catalog "Owned by" links are populated.

Teams are looked up by handle at teardown time — no persistent ID storage needed.
"""

import logging
from typing import Dict, Any, Optional

from dd_demo_toolkit.utils.dd_api import DatadogAPIClient


logger = logging.getLogger(__name__)

_TEAM_HANDLE_PREFIX = "dd-demo-"


def _handle(vertical_name: str) -> str:
    return f"{_TEAM_HANDLE_PREFIX}{vertical_name}"


class TeamManager:
    """Creates and tears down the demo Datadog Team for a vertical."""

    def deploy(
        self,
        vertical_path: str,
        api_client: DatadogAPIClient,
        tags: Optional[Dict[str, str]] = None,
        dry_run: bool = False,
        vertical_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        if vertical_name is None:
            from pathlib import Path
            vertical_name = Path(vertical_path).name

        handle = _handle(vertical_name)
        name = f"DD Demo — {vertical_name.replace('-', ' ').title()}"
        description = (
            f"Datadog demo team for the {vertical_name} vertical. "
            "Managed by dd-demo-toolkit (dd-demo-toolkit:true)."
        )

        result: Dict[str, Any] = {
            "total_created": 0,
            "total_errors": 0,
            "errors": [],
        }

        if dry_run:
            logger.info("[dry-run] Would create team '%s'", handle)
            result["total_created"] = 1
            return result

        try:
            team_id = api_client.create_team(handle, name, description)
            logger.info("Team '%s' ready (id=%s)", handle, team_id)
            result["team_id"] = team_id
            result["total_created"] += 1
        except Exception as e:
            msg = f"Failed to create team '{handle}': {e}"
            logger.error(msg)
            result["errors"].append(msg)
            result["total_errors"] += 1
            return result

        try:
            user_resp = api_client.get_current_user()
            user_id = user_resp["data"]["id"]
            api_client.add_team_member(team_id, user_id)
            logger.info("Added current user (%s) to team '%s'", user_id, handle)
        except Exception as e:
            # Non-fatal — team still works; user just won't be a member automatically.
            logger.warning("Could not add current user to team '%s': %s", handle, e)

        return result

    def teardown(
        self,
        api_client: DatadogAPIClient,
        vertical_name: Optional[str],
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "total_deleted": 0,
            "total_errors": 0,
            "errors": [],
        }

        if vertical_name is None:
            # All-verticals sweep: we only know our own prefix, not every handle.
            # Skip — individual vertical teardowns will clean their own teams.
            logger.info("Skipping team teardown for all-verticals sweep (handled per-vertical)")
            return result

        handle = _handle(vertical_name)

        if dry_run:
            logger.info("[dry-run] Would delete team '%s'", handle)
            result["total_deleted"] = 1
            return result

        try:
            team = api_client.find_team_by_handle(handle)
            if team is None:
                logger.info("Team '%s' not found — nothing to delete", handle)
                return result
            api_client.delete_team(team["id"])
            logger.info("Deleted team '%s' (id=%s)", handle, team["id"])
            result["total_deleted"] = 1
        except Exception as e:
            msg = f"Failed to delete team '{handle}': {e}"
            logger.error(msg)
            result["errors"].append(msg)
            result["total_errors"] += 1

        return result

    def list_deployed(
        self,
        api_client: DatadogAPIClient,
        vertical_name: Optional[str],
    ) -> Dict[str, Any]:
        if vertical_name is None:
            return {"total": 0, "teams": []}
        handle = _handle(vertical_name)
        team = api_client.find_team_by_handle(handle)
        if team:
            return {"total": 1, "teams": [team]}
        return {"total": 0, "teams": []}
