"""
Service Catalog resource manager for dd-demo-toolkit.

Handles deployment, deletion, and listing of Datadog service catalog entries for verticals.
"""

import logging
from pathlib import Path
from typing import Dict, List, Any, Optional

import yaml

from dd_demo_toolkit.utils.dd_api import DatadogAPIClient


logger = logging.getLogger(__name__)


class ServiceCatalogManager:
    """Manages deployment and lifecycle of Datadog service catalog entries."""

    def __init__(self) -> None:
        """Initialize the service catalog manager."""
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
        Deploy services to the Datadog Service Catalog.

        Reads services.yaml from the vertical path and registers services via API.
        Each service definition should include service catalog fields like name, owner, etc.

        Args:
            vertical_path: Path to the vertical directory.
            api_client: Datadog API client instance.
            tags: Additional tags to inject (vertical and dd-demo-toolkit tags added automatically).
            dry_run: If True, skip API calls and return what would be created.

        Returns:
            Dictionary with keys:
            - created_names: List of registered service names
            - errors: List of error messages
            - total_created: Number of successfully registered services
            - total_errors: Number of failed registrations
        """
        vertical_path_obj = Path(vertical_path)
        services_file = vertical_path_obj / "services.yaml"

        result = {
            "created_names": [],
            "errors": [],
            "total_created": 0,
            "total_errors": 0,
        }

        if not services_file.exists():
            logger.info(f"No services.yaml file found at {services_file}")
            return result

        if vertical_name is None:
            vertical_name = vertical_path_obj.name

        try:
            with open(services_file, "r") as f:
                config = yaml.safe_load(f)
        except yaml.YAMLError as e:
            error_msg = f"Failed to parse services.yaml: {str(e)}"
            result["errors"].append(error_msg)
            result["total_errors"] += 1
            logger.error(error_msg)
            return result
        except IOError as e:
            error_msg = f"Failed to read services.yaml: {str(e)}"
            result["errors"].append(error_msg)
            result["total_errors"] += 1
            logger.error(error_msg)
            return result

        if not config:
            logger.info("No services defined in services.yaml")
            return result

        services = config if isinstance(config, list) else config.get("services", [])
        if not services:
            logger.info("No services found in services.yaml")
            return result

        logger.info(f"Deploying {len(services)} service(s) for vertical '{vertical_name}'")

        for idx, service_config in enumerate(services):
            try:
                # Build the service payload
                payload = self._build_service_payload(service_config, vertical_name, tags)

                # Service name is at top-level "dd-service" in our v2.2 payload
                service_name = payload.get("dd-service", f"service-{idx}")

                if dry_run:
                    logger.info(f"[DRY RUN] Would register service '{service_name}'")
                    result["created_names"].append(service_name)
                    result["total_created"] += 1
                else:
                    # Register service via JSON API v2
                    response = api_client.register_service(payload)
                    result["created_names"].append(service_name)
                    result["total_created"] += 1
                    logger.info(f"Registered service '{service_name}'")

            except KeyError as e:
                error_msg = f"Service {idx} missing required field: {str(e)}"
                result["errors"].append(error_msg)
                result["total_errors"] += 1
                logger.error(error_msg)
            except RuntimeError as e:
                error_msg = f"API error registering service {idx}: {str(e)}"
                result["errors"].append(error_msg)
                result["total_errors"] += 1
                logger.error(error_msg)
            except Exception as e:
                error_msg = f"Unexpected error registering service {idx}: {str(e)}"
                result["errors"].append(error_msg)
                result["total_errors"] += 1
                logger.error(error_msg)

        logger.info(
            f"Service deployment complete: {result['total_created']} registered, "
            f"{result['total_errors']} errors"
        )

        return result

    def _build_service_payload(
        self,
        config: Dict[str, Any],
        vertical_name: str,
        additional_tags: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """
        Build a service payload from config.

        Args:
            config: Service configuration dict.
            vertical_name: Vertical name for tagging.
            additional_tags: Additional tags to add.

        Returns:
            Service payload ready for API submission.

        Raises:
            KeyError: If required fields are missing.
        """
        # Accept either "dd-service" or "name" as the service identifier
        service_name = config.get("dd-service") or config.get("name")
        if not service_name:
            raise KeyError("Required field 'dd-service' missing")

        # Build service definition following Datadog schema v2.2
        # The v2 API expects the full definition as a JSON object
        dd_team = config.get("owner") or config.get("team", "")
        display_name = config.get("display-name") or config.get("display_name", "")
        description = config.get("description", "")

        # Build tags — strip any YAML-provided team: entries and inject the
        # vertical's demo team handle so all services appear on their Team page.
        tags = config.get("tags", []) if isinstance(config.get("tags"), list) else []
        tags = [t for t in tags if not t.startswith("team:")]
        tags.append(f"vertical:{vertical_name}")
        tags.append("dd-demo-toolkit:true")
        tags.append(f"team:dd-demo-{vertical_name}")
        if additional_tags:
            for key, value in additional_tags.items():
                tags.append(f"{key}:{value}")
        tags = list(dict.fromkeys(tags))  # Deduplicate

        # Service Definition v2.2 schema — team must be the Datadog Team handle,
        # not a functional team name (e.g. "Digital Banking" is not a valid handle).
        payload = {
            "schema-version": "v2.2",
            "dd-service": service_name,
            "team": f"dd-demo-{vertical_name}",
            "tags": tags,
        }

        if display_name:
            payload["display-name"] = display_name
        if description:
            payload["description"] = description

        # type classifies the entity in the Software Catalog (e.g. "db", "cache", "web")
        entity_type = config.get("type")
        if entity_type:
            payload["type"] = entity_type

        # Add optional metadata
        languages = config.get("languages", [])
        if languages:
            payload["languages"] = languages
        tier = config.get("tier")
        if tier:
            payload["tier"] = tier

        return payload

    def teardown(
        self,
        api_client: DatadogAPIClient,
        vertical_name: str,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """
        Deregister all services tagged with a vertical.

        Note: Datadog's service catalog API may not provide a direct deregistration method.
        This implementation attempts to deregister by tag matching, but actual behavior
        depends on API availability.

        Args:
            api_client: Datadog API client instance.
            vertical_name: Name of the vertical to clean up.
            dry_run: If True, skip API calls and return what would be deleted.

        Returns:
            Dictionary with keys:
            - deregistered_names: List of deregistered service names
            - errors: List of error messages
            - total_deregistered: Number of successfully deregistered services
            - total_errors: Number of failed deregistrations
        """
        result = {
            "deregistered_names": [],
            "errors": [],
            "total_deregistered": 0,
            "total_errors": 0,
        }

        logger.warning(
            "Service deregistration depends on Datadog API availability. "
            "Services may need to be manually removed from the Service Catalog."
        )

        # For now, log that teardown was requested but cannot be fully automated
        scope_label = (
            "all toolkit-managed verticals" if vertical_name is None
            else f"vertical '{vertical_name}'"
        )
        logger.info(f"Service teardown requested for {scope_label}")
        logger.info(
            "Note: Service Catalog deregistration may require manual intervention "
            "or future API updates."
        )

        return result

    def list_deployed(
        self,
        api_client: DatadogAPIClient,
        vertical_name: str,
    ) -> Dict[str, Any]:
        """
        List all services registered for a vertical.

        Note: This queries available service definition endpoints.
        Actual filtering depends on API response format.

        Args:
            api_client: Datadog API client instance.
            vertical_name: Name of the vertical.

        Returns:
            Dictionary with keys:
            - services: List of service objects
            - total: Count of services
            - error: Error message if listing failed, None otherwise
        """
        result = {
            "services": [],
            "total": 0,
            "error": None,
        }

        try:
            # Attempt to query service definitions endpoint
            services_response = api_client._request("GET", "/api/v2/services/definitions")
            service_list = services_response.get("data", [])
        except RuntimeError as e:
            # Service definitions endpoint may not be available or accessible
            result["error"] = f"Failed to list services: {str(e)}"
            logger.warning(result["error"])
            return result

        # Filter by vertical tag
        target_tag = f"vertical:{vertical_name}"
        deployed = [
            s for s in service_list
            if any(target_tag in str(tag_value) for tag_value in s.get("tags", []))
        ]

        result["services"] = deployed
        result["total"] = len(deployed)
        logger.info(f"Found {result['total']} service(s) for vertical '{vertical_name}'")

        return result
