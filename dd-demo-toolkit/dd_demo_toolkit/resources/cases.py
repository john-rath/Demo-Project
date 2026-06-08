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

    def __init__(self, verticals_dir: str = "verticals") -> None:
        self._project_cache: Dict[str, Optional[str]] = {}
        self.verticals_dir = Path(verticals_dir)

    def _get_expected_titles(self, vertical_name: Optional[str]) -> set:
        """
        Return the set of case titles defined in cases.yaml for one or all
        verticals.  The Cases API doesn't expose tags on list responses, so
        teardown uses title-matching against the YAML configs.
        """
        if vertical_name is not None:
            vertical_dir = self.verticals_dir / vertical_name
            titles = self._titles_from_yaml(vertical_dir)
            overlays_dir = vertical_dir / "overlays"
            if overlays_dir.is_dir():
                for odir in overlays_dir.iterdir():
                    if odir.is_dir():
                        titles |= self._titles_from_yaml(odir)
            return titles

        titles: set = set()
        for vdir in self.verticals_dir.iterdir():
            if vdir.is_dir():
                titles |= self._titles_from_yaml(vdir)
                overlays_dir = vdir / "overlays"
                if overlays_dir.is_dir():
                    for odir in overlays_dir.iterdir():
                        if odir.is_dir():
                            titles |= self._titles_from_yaml(odir)
        return titles

    @staticmethod
    def _titles_from_yaml(path: Path) -> set:
        cases_file = path / "cases.yaml"
        if not cases_file.exists():
            return set()
        try:
            with open(cases_file) as f:
                config = yaml.safe_load(f)
        except Exception:
            return set()
        entries = config if isinstance(config, list) else (config or {}).get("cases", [])
        return {c.get("title") for c in (entries or []) if c.get("title")}

    def _ensure_project(
        self,
        api_client: DatadogAPIClient,
        project_name: str,
        vertical_name: str,
    ) -> Optional[str]:
        """
        Ensure a Case Management project with the given name exists and is owned
        by the correct Datadog Team.

        Team ownership is set via ``data.attributes.team_uuid`` in both the
        POST (create) and PATCH (update) payloads.  Existing projects found by
        name are PATCH-ed to associate the team so that re-running ``make setup``
        corrects previously unowned projects without requiring teardown.

        Args:
            api_client: Datadog API client instance.
            project_name: Desired project name (vertical or sub-vertical name).
            vertical_name: Base vertical name used to look up the team handle
                (``dd-demo-{vertical_name}``).

        Returns:
            Project ID string, or None if project could not be found/created.
        """
        cache_key = project_name
        if cache_key in self._project_cache:
            return self._project_cache[cache_key]

        # Look up the team UUID for this vertical — non-fatal if not yet deployed.
        team_id: Optional[str] = None
        try:
            team = api_client.find_team_by_handle(f"dd-demo-{vertical_name}")
            if team:
                team_id = team.get("id")
        except Exception as e:
            logger.warning(f"Could not look up team for vertical '{vertical_name}': {e}")

        try:
            response = api_client.list_case_projects()
            projects = response.get("data", [])
            for project in projects:
                if project.get("attributes", {}).get("name") == project_name:
                    pid = project.get("id")
                    self._project_cache[cache_key] = pid
                    logger.info(f"Found existing Case Management project '{project_name}' (ID: {pid})")
                    if team_id:
                        try:
                            api_client.update_case_project(pid, {
                                "data": {
                                    "type": "project",
                                    "attributes": {"team_uuid": team_id},
                                }
                            })
                            logger.info(f"Associated project '{project_name}' with team dd-demo-{vertical_name}")
                        except RuntimeError as e:
                            logger.warning(f"Could not set team on project '{project_name}': {e}")
                    return pid
        except RuntimeError as e:
            logger.warning(f"Failed to list Case Management projects: {e}")
            self._project_cache[cache_key] = None
            return None

        # Project not found — create it.  The key must be unique in the org;
        # derive it from the name (alpha chars only, uppercase, max 10 chars).
        key = ''.join(c for c in project_name.upper() if c.isalpha())[:10]
        try:
            attributes: Dict[str, Any] = {"name": project_name, "key": key}
            if team_id:
                attributes["team_uuid"] = team_id
            payload: Dict[str, Any] = {
                "data": {
                    "type": "project",
                    "attributes": attributes,
                }
            }
            response = api_client.create_case_project(payload)
            project_data = response.get("data", {})
            pid = project_data.get("id")
            if pid:
                self._project_cache[cache_key] = pid
                logger.info(f"Created Case Management project '{project_name}' (ID: {pid})")
                return pid
            logger.error(f"Created project '{project_name}' but no ID in response")
        except RuntimeError as e:
            logger.error(f"Failed to create Case Management project '{project_name}': {e}")

        self._project_cache[cache_key] = None
        return None

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
        vertical_name: Optional[str],
    ) -> Dict[str, Any]:
        """
        List cases for a vertical, or all toolkit-managed cases.

        Args:
            api_client: Datadog API client instance.
            vertical_name: Name of the vertical, or ``None`` to match every
                toolkit-tagged case across all verticals (orphan-sweep mode).

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
            # List all cases and filter client-side
            # (Cases API filter syntax doesn't support tag-based queries).
            response = api_client.list_cases()
            all_cases = response.get("data", [])
            # The Cases API doesn't expose tags on list responses, so match
            # by title against cases.yaml.  _get_expected_titles handles both
            # single-vertical and all-verticals sweeps.
            expected_titles = self._get_expected_titles(vertical_name)
            scope_label = f"vertical '{vertical_name}'" if vertical_name else "all toolkit-managed verticals"
            cases = [
                c for c in all_cases
                if c.get("attributes", {}).get("title") in expected_titles
            ]
            result["total"] = len(cases)
            result["cases"] = cases
            logger.info(f"Found {len(cases)} case(s) for {scope_label}")
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
        vertical_name: Optional[str],
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """
        Close and archive demo cases for a vertical.

        Cases already in CLOSED or ARCHIVED state are skipped (the API
        returns 404 on PATCH for terminal-state cases).

        Args:
            api_client: Datadog API client instance.
            vertical_name: Name of the vertical, or ``None`` to close every
                toolkit-tagged case across all verticals (orphan-sweep mode).
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

        scope_label = "all toolkit-managed verticals" if vertical_name is None else f"vertical '{vertical_name}'"
        logger.info(f"Found {len(cases)} case(s) to close for {scope_label}")

        # The Cases API returns status as lowercase ("open", "closed") in list
        # responses but accepts uppercase ("CLOSED") on PATCH.  Normalise to
        # uppercase for comparison so both forms are handled.
        _TERMINAL = {"CLOSED", "ARCHIVED"}

        for case in cases:
            try:
                case_id = case.get("id")
                case_title = case.get("attributes", {}).get("title", "")
                current_status = case.get("attributes", {}).get("status", "").upper()

                if not case_id:
                    result["errors"].append("Case missing ID")
                    result["total_errors"] += 1
                    logger.error("Case missing ID")
                    continue

                # Skip cases already in a terminal state — the API rejects
                # PATCH on closed/archived cases with 404.
                if current_status in _TERMINAL:
                    logger.debug(f"Skipping case '{case_title}' (already {current_status})")
                    continue

                if dry_run:
                    logger.info(f"[DRY RUN] Would close+archive case '{case_title}' (ID: {case_id})")
                    result["closed_ids"].append(case_id)
                    result["closed_titles"].append(case_title)
                    result["total_closed"] += 1
                else:
                    # Use the /status action endpoint (not PATCH) — PATCH does
                    # not accept status changes in the v2 API.
                    api_client.close_case(case_id)
                    api_client.archive_case(case_id)
                    result["closed_ids"].append(case_id)
                    result["closed_titles"].append(case_title)
                    result["total_closed"] += 1
                    logger.info(f"Closed and archived case '{case_title}' (ID: {case_id})")

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
        vertical_name: Optional[str] = None,
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

        if vertical_name is None:
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

        # Determine project name: sub-vertical name when deploying an overlay,
        # otherwise the base vertical name.
        path_parts = vertical_path_obj.parts
        if "overlays" in path_parts:
            overlay_idx = list(path_parts).index("overlays")
            project_name = path_parts[overlay_idx + 1]
        else:
            project_name = vertical_name or vertical_path_obj.name

        # Cases require a project — ensure one exists before creating any cases
        project_id = self._ensure_project(api_client, project_name, vertical_name)
        if not project_id and not dry_run:
            error_msg = (
                "Cannot create cases: no Case Management project available. "
                "Create a project in Datadog Case Management first, or check API permissions."
            )
            result["errors"].append(error_msg)
            result["total_errors"] += 1
            logger.error(error_msg)
            return result

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
                        linked_resources={"project_id": project_id},
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
        # Tags are not exposed on list responses, so teardown uses title-
        # matching against cases.yaml instead of tag-based filtering.
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
