"""
Master resource manager for dd-demo-toolkit.

Orchestrates deployment and management of all resource types across verticals.
"""

import logging
from pathlib import Path
from typing import Dict, List, Any, Optional, Set

from dd_demo_toolkit.utils.dd_api import DatadogAPIClient
from dd_demo_toolkit.resources.dashboards import DashboardManager
from dd_demo_toolkit.resources.monitors import MonitorManager
from dd_demo_toolkit.resources.notebooks import NotebookManager
from dd_demo_toolkit.resources.slos import SLOManager
from dd_demo_toolkit.resources.services import ServiceCatalogManager
from dd_demo_toolkit.resources.workflows import WorkflowManager
from dd_demo_toolkit.resources.incidents import IncidentManager
from dd_demo_toolkit.resources.cases import CaseManager
from dd_demo_toolkit.resources.sds import SDSManager
from dd_demo_toolkit.resources.teams import TeamManager


logger = logging.getLogger(__name__)


class ResourceManager:
    """Orchestrates deployment and lifecycle management of all Datadog resources."""

    # Supported resource types — teams first (deploy before others, teardown before others is fine)
    RESOURCE_TYPES = {
        "teams": TeamManager,
        "dashboards": DashboardManager,
        "monitors": MonitorManager,
        "notebooks": NotebookManager,
        "slos": SLOManager,
        "services": ServiceCatalogManager,
        "workflows": WorkflowManager,
        "incidents": IncidentManager,
        "cases": CaseManager,
        "sds": SDSManager,
    }

    def __init__(self, verticals_dir: str = "verticals") -> None:
        """
        Initialize the resource manager.

        Args:
            verticals_dir: Path to the verticals directory.
        """
        self.verticals_dir = Path(verticals_dir)
        self.team_manager = TeamManager()
        self.dashboard_manager = DashboardManager()
        self.monitor_manager = MonitorManager()
        self.notebook_manager = NotebookManager(verticals_dir=verticals_dir)
        self.slo_manager = SLOManager()
        self.service_manager = ServiceCatalogManager()
        self.workflow_manager = WorkflowManager()
        self.incident_manager = IncidentManager()
        self.case_manager = CaseManager(verticals_dir=verticals_dir)
        self.sds_manager = SDSManager()

    def deploy_all(
        self,
        vertical_name: str,
        api_client: DatadogAPIClient,
        tags: Optional[Dict[str, str]] = None,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """
        Deploy all resource types for a vertical.

        Args:
            vertical_name: Name of the vertical to deploy.
            api_client: Datadog API client instance.
            tags: Additional tags to apply to all resources.
            dry_run: If True, skip API calls.

        Returns:
            Dictionary with deployment results for each resource type:
            {
                "dashboards": {...},
                "monitors": {...},
                "notebooks": {...},
                "slos": {...},
                "services": {...},
                "summary": {
                    "total_created": int,
                    "total_errors": int,
                }
            }
        """
        logger.info(f"Starting full deployment for vertical '{vertical_name}'")
        vertical_path = self.verticals_dir / vertical_name

        if not vertical_path.exists():
            logger.error(f"Vertical path does not exist: {vertical_path}")
            return {
                "error": f"Vertical '{vertical_name}' not found",
                "summary": {"total_created": 0, "total_errors": 1},
            }

        results = {}
        total_created = 0
        total_errors = 0

        # Deploy each resource type
        for resource_type in self.RESOURCE_TYPES.keys():
            results[resource_type] = self.deploy_selected(
                vertical_name,
                api_client,
                [resource_type],
                tags=tags,
                dry_run=dry_run,
            )
            # Aggregate counts from individual resource type results
            if isinstance(results[resource_type], dict):
                total_created += results[resource_type].get("summary", {}).get("total_created", 0)
                total_errors += results[resource_type].get("summary", {}).get("total_errors", 0)

        results["summary"] = {
            "total_created": total_created,
            "total_errors": total_errors,
        }

        logger.info(
            f"Full deployment complete: {total_created} resources created, "
            f"{total_errors} errors"
        )

        return results

    def deploy_overlay_selected(
        self,
        vertical_name: str,
        sub_vertical: str,
        api_client: DatadogAPIClient,
        resource_types: List[str],
        tags: Optional[Dict[str, str]] = None,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """
        Deploy resources from a sub-vertical overlay directory.

        Resolves to ``verticals/<vertical_name>/overlays/<sub_vertical>/`` and
        deploys any resource files found there as additive resources for the
        base vertical. Tag injection still uses ``vertical:<vertical_name>``
        (not the overlay's directory name) so overlay resources cohere with
        the base vertical's existing tag standards. Use this *after*
        ``deploy_selected`` for the base vertical when ``--sub-vertical`` is
        passed on the CLI.
        """
        overlay_path = self.verticals_dir / vertical_name / "overlays" / sub_vertical
        logger.info(
            f"Deploying overlay '{sub_vertical}' for vertical "
            f"'{vertical_name}' from {overlay_path}"
        )

        if not overlay_path.exists():
            logger.warning(f"Overlay path does not exist: {overlay_path}")
            return {
                "summary": {"total_created": 0, "total_errors": 0},
            }

        results: Dict[str, Any] = {}
        total_created = 0
        total_errors = 0

        for resource_type in resource_types:
            if resource_type not in self.RESOURCE_TYPES:
                continue
            try:
                if resource_type == "teams":
                    # Team is created by the base vertical deploy; skip in overlay.
                    continue
                elif resource_type == "dashboards":
                    result = self.dashboard_manager.deploy(
                        str(overlay_path), api_client, tags, dry_run,
                        vertical_name=vertical_name,
                    )
                elif resource_type == "monitors":
                    result = self.monitor_manager.deploy(
                        str(overlay_path), api_client, tags, dry_run,
                        vertical_name=vertical_name,
                    )
                elif resource_type == "notebooks":
                    result = self.notebook_manager.deploy(
                        str(overlay_path), api_client, tags, dry_run,
                        vertical_name=vertical_name,
                    )
                elif resource_type == "slos":
                    result = self.slo_manager.deploy(
                        str(overlay_path), api_client, tags, dry_run,
                        vertical_name=vertical_name,
                    )
                elif resource_type == "services":
                    result = self.service_manager.deploy(
                        str(overlay_path), api_client, tags, dry_run,
                        vertical_name=vertical_name,
                    )
                elif resource_type == "workflows":
                    result = self.workflow_manager.deploy(
                        str(overlay_path), api_client, tags, dry_run,
                        vertical_name=vertical_name,
                    )
                elif resource_type == "incidents":
                    result = {"total_created": 0, "total_errors": 0}
                elif resource_type == "cases":
                    result = self.case_manager.deploy(
                        str(overlay_path), api_client, tags, dry_run,
                        vertical_name=vertical_name,
                    )
                elif resource_type == "sds":
                    result = self.sds_manager.deploy(
                        str(overlay_path), api_client, tags, dry_run,
                        vertical_name=vertical_name,
                    )

                results[resource_type] = result
                total_created += result.get("total_created", 0)
                total_errors += result.get("total_errors", 0)

            except Exception as e:
                error_msg = f"Error deploying overlay {resource_type}: {str(e)}"
                logger.error(error_msg)
                results[resource_type] = {"error": error_msg}
                total_errors += 1

        results["summary"] = {
            "total_created": total_created,
            "total_errors": total_errors,
        }

        logger.info(
            f"Overlay deployment complete: {total_created} resources created, "
            f"{total_errors} errors"
        )

        return results

    def deploy_selected(
        self,
        vertical_name: str,
        api_client: DatadogAPIClient,
        resource_types: List[str],
        tags: Optional[Dict[str, str]] = None,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """
        Deploy selected resource types for a vertical.

        Args:
            vertical_name: Name of the vertical to deploy.
            api_client: Datadog API client instance.
            resource_types: List of resource types to deploy (dashboards, monitors, etc.).
            tags: Additional tags to apply to all resources.
            dry_run: If True, skip API calls.

        Returns:
            Dictionary with deployment results:
            {
                "dashboards": {...},
                "monitors": {...},
                ...
                "summary": {
                    "total_created": int,
                    "total_errors": int,
                }
            }
        """
        logger.info(
            f"Starting selective deployment for vertical '{vertical_name}': "
            f"{', '.join(resource_types)}"
        )
        vertical_path = self.verticals_dir / vertical_name

        if not vertical_path.exists():
            logger.error(f"Vertical path does not exist: {vertical_path}")
            return {
                "error": f"Vertical '{vertical_name}' not found",
                "summary": {"total_created": 0, "total_errors": 1},
            }

        results = {}
        total_created = 0
        total_errors = 0

        for resource_type in resource_types:
            if resource_type not in self.RESOURCE_TYPES:
                logger.warning(f"Unknown resource type: {resource_type}")
                results[resource_type] = {
                    "error": f"Unknown resource type: {resource_type}",
                }
                total_errors += 1
                continue

            try:
                if resource_type == "teams":
                    result = self.team_manager.deploy(
                        str(vertical_path), api_client, tags, dry_run,
                        vertical_name=vertical_name,
                    )
                elif resource_type == "dashboards":
                    result = self.dashboard_manager.deploy(
                        str(vertical_path), api_client, tags, dry_run
                    )
                elif resource_type == "monitors":
                    result = self.monitor_manager.deploy(
                        str(vertical_path), api_client, tags, dry_run
                    )
                elif resource_type == "notebooks":
                    result = self.notebook_manager.deploy(
                        str(vertical_path), api_client, tags, dry_run
                    )
                elif resource_type == "slos":
                    result = self.slo_manager.deploy(
                        str(vertical_path), api_client, tags, dry_run
                    )
                elif resource_type == "services":
                    result = self.service_manager.deploy(
                        str(vertical_path), api_client, tags, dry_run
                    )
                elif resource_type == "workflows":
                    result = self.workflow_manager.deploy(
                        str(vertical_path), api_client, tags, dry_run
                    )
                elif resource_type == "incidents":
                    # Incidents are typically declared dynamically, but can be deployed from incidents.yaml
                    result = {"total_created": 0, "total_errors": 0}
                    logger.info("Incidents are typically declared dynamically rather than deployed from YAML")
                elif resource_type == "cases":
                    result = self.case_manager.deploy(
                        str(vertical_path), api_client, tags, dry_run
                    )
                elif resource_type == "sds":
                    result = self.sds_manager.deploy(
                        str(vertical_path), api_client, tags, dry_run
                    )

                results[resource_type] = result
                total_created += result.get("total_created", 0)
                total_errors += result.get("total_errors", 0)

            except Exception as e:
                error_msg = f"Error deploying {resource_type}: {str(e)}"
                logger.error(error_msg)
                results[resource_type] = {"error": error_msg}
                total_errors += 1

        results["summary"] = {
            "total_created": total_created,
            "total_errors": total_errors,
        }

        logger.info(
            f"Selective deployment complete: {total_created} resources created, "
            f"{total_errors} errors"
        )

        return results

    def teardown_all(
        self,
        vertical_name: Optional[str],
        api_client: DatadogAPIClient,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """
        Tear down all resources for a vertical.

        Args:
            vertical_name: Name of the vertical to clean up, or ``None`` for
                an all-verticals sweep that deletes every toolkit-managed
                resource.
            api_client: Datadog API client instance.
            dry_run: If True, skip API calls.

        Returns:
            Dictionary with teardown results for each resource type:
            {
                "dashboards": {...},
                "monitors": {...},
                ...
                "summary": {
                    "total_deleted": int,
                    "total_errors": int,
                }
            }
        """
        scope = vertical_name if vertical_name is not None else "<all toolkit verticals>"
        logger.info(f"Starting full teardown for vertical '{scope}'")

        return self.teardown_selected(
            vertical_name,
            api_client,
            list(self.RESOURCE_TYPES.keys()),
            dry_run=dry_run,
        )

    def teardown_selected(
        self,
        vertical_name: Optional[str],
        api_client: DatadogAPIClient,
        resource_types: List[str],
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """
        Tear down selected resource types for a vertical.

        Args:
            vertical_name: Name of the vertical to clean up, or ``None`` to
                sweep every toolkit-managed resource across all verticals
                (orphan-sweep mode).
            api_client: Datadog API client instance.
            resource_types: List of resource types to tear down.
            dry_run: If True, skip API calls.

        Returns:
            Dictionary with teardown results:
            {
                "dashboards": {...},
                "monitors": {...},
                ...
                "summary": {
                    "total_deleted": int,
                    "total_errors": int,
                }
            }
        """
        scope = vertical_name if vertical_name is not None else "<all toolkit verticals>"
        logger.info(
            f"Starting selective teardown for vertical '{scope}': "
            f"{', '.join(resource_types)}"
        )

        results = {}
        total_deleted = 0
        total_errors = 0

        for resource_type in resource_types:
            if resource_type not in self.RESOURCE_TYPES:
                logger.warning(f"Unknown resource type: {resource_type}")
                results[resource_type] = {
                    "error": f"Unknown resource type: {resource_type}",
                }
                total_errors += 1
                continue

            try:
                if resource_type == "teams":
                    result = self.team_manager.teardown(api_client, vertical_name, dry_run)
                elif resource_type == "dashboards":
                    result = self.dashboard_manager.teardown(api_client, vertical_name, dry_run)
                elif resource_type == "monitors":
                    result = self.monitor_manager.teardown(api_client, vertical_name, dry_run)
                elif resource_type == "notebooks":
                    result = self.notebook_manager.teardown(api_client, vertical_name, dry_run)
                elif resource_type == "slos":
                    result = self.slo_manager.teardown(api_client, vertical_name, dry_run)
                elif resource_type == "services":
                    result = self.service_manager.teardown(api_client, vertical_name, dry_run)
                elif resource_type == "workflows":
                    result = self.workflow_manager.teardown(api_client, vertical_name, dry_run)
                elif resource_type == "incidents":
                    result = self.incident_manager.teardown(api_client, vertical_name, dry_run)
                elif resource_type == "cases":
                    result = self.case_manager.teardown(api_client, vertical_name, dry_run)
                elif resource_type == "sds":
                    result = self.sds_manager.teardown(api_client, vertical_name, dry_run)

                results[resource_type] = result
                total_deleted += result.get("total_deleted", 0)
                total_errors += result.get("total_errors", 0)

            except Exception as e:
                error_msg = f"Error tearing down {resource_type}: {str(e)}"
                logger.error(error_msg)
                results[resource_type] = {"error": error_msg}
                total_errors += 1

        results["summary"] = {
            "total_deleted": total_deleted,
            "total_errors": total_errors,
        }

        logger.info(
            f"Selective teardown complete for '{scope}': "
            f"{total_deleted} resources deleted, {total_errors} errors"
        )

        return results

    def get_status(
        self,
        vertical_name: str,
        api_client: DatadogAPIClient,
    ) -> Dict[str, Any]:
        """
        Get deployment status for all resources in a vertical.

        Args:
            vertical_name: Name of the vertical.
            api_client: Datadog API client instance.

        Returns:
            Dictionary with status for each resource type:
            {
                "dashboards": {"total": int, "dashboards": [...]},
                "monitors": {"total": int, "monitors": [...]},
                ...
                "summary": {
                    "total_resources": int,
                }
            }
        """
        logger.info(f"Getting status for vertical '{vertical_name}'")

        results = {}
        total_resources = 0

        try:
            dashboard_status = self.dashboard_manager.list_deployed(api_client, vertical_name)
            results["dashboards"] = dashboard_status
            total_resources += dashboard_status.get("total", 0)
        except Exception as e:
            logger.error(f"Error listing dashboards: {str(e)}")
            results["dashboards"] = {"error": str(e), "total": 0}

        try:
            monitor_status = self.monitor_manager.list_deployed(api_client, vertical_name)
            results["monitors"] = monitor_status
            total_resources += monitor_status.get("total", 0)
        except Exception as e:
            logger.error(f"Error listing monitors: {str(e)}")
            results["monitors"] = {"error": str(e), "total": 0}

        try:
            notebook_status = self.notebook_manager.list_deployed(api_client, vertical_name)
            results["notebooks"] = notebook_status
            total_resources += notebook_status.get("total", 0)
        except Exception as e:
            logger.error(f"Error listing notebooks: {str(e)}")
            results["notebooks"] = {"error": str(e), "total": 0}

        try:
            slo_status = self.slo_manager.list_deployed(api_client, vertical_name)
            results["slos"] = slo_status
            total_resources += slo_status.get("total", 0)
        except Exception as e:
            logger.error(f"Error listing SLOs: {str(e)}")
            results["slos"] = {"error": str(e), "total": 0}

        try:
            service_status = self.service_manager.list_deployed(api_client, vertical_name)
            results["services"] = service_status
            total_resources += service_status.get("total", 0)
        except Exception as e:
            logger.error(f"Error listing services: {str(e)}")
            results["services"] = {"error": str(e), "total": 0}

        try:
            workflow_status = self.workflow_manager.list_deployed(api_client, vertical_name)
            results["workflows"] = workflow_status
            total_resources += workflow_status.get("total", 0)
        except Exception as e:
            logger.error(f"Error listing workflows: {str(e)}")
            results["workflows"] = {"error": str(e), "total": 0}

        try:
            incident_status = self.incident_manager.list_active(api_client, vertical_name)
            results["incidents"] = incident_status
            total_resources += incident_status.get("total", 0)
        except Exception as e:
            logger.error(f"Error listing incidents: {str(e)}")
            results["incidents"] = {"error": str(e), "total": 0}

        try:
            case_status = self.case_manager.list_cases(api_client, vertical_name)
            results["cases"] = case_status
            total_resources += case_status.get("total", 0)
        except Exception as e:
            logger.error(f"Error listing cases: {str(e)}")
            results["cases"] = {"error": str(e), "total": 0}

        try:
            sds_status = self.sds_manager.list_deployed(api_client, vertical_name)
            results["sds"] = sds_status
            total_resources += sds_status.get("total", 0)
        except Exception as e:
            logger.error(f"Error listing SDS resources: {str(e)}")
            results["sds"] = {"error": str(e), "total": 0}

        results["summary"] = {
            "total_resources": total_resources,
        }

        logger.info(f"Status: {total_resources} total resources deployed")

        return results

    @staticmethod
    def get_supported_types() -> List[str]:
        """
        Get list of supported resource types.

        Returns:
            List of resource type strings.
        """
        return list(ResourceManager.RESOURCE_TYPES.keys())
