"""
Incident Management resource manager for dd-demo-toolkit.

Handles creation, updating, and lifecycle management of Datadog incidents for verticals.
"""

import logging
from pathlib import Path
from typing import Dict, List, Any, Optional
from datetime import datetime, timezone

import yaml

from dd_demo_toolkit.utils.dd_api import DatadogAPIClient


logger = logging.getLogger(__name__)


class IncidentManager:
    """Manages creation and lifecycle of Datadog incidents."""

    def __init__(self) -> None:
        """Initialize the incident manager."""
        pass

    def declare_incident(
        self,
        api_client: DatadogAPIClient,
        title: str,
        severity: str,
        vertical_name: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Create and declare an incident.

        Args:
            api_client: Datadog API client instance.
            title: Incident title.
            severity: Severity level (e.g., "SEV-1", "SEV-2", etc.).
            vertical_name: Name of the vertical for tagging.
            details: Optional dictionary with additional details (customer_impact_scope, commander, etc.).

        Returns:
            Dictionary with:
            - incident_id: ID of the created incident
            - status: "success" or "error"
            - message: Status message
        """
        result = {
            "incident_id": None,
            "status": "error",
            "message": "",
        }

        details = details or {}

        # Build incident payload
        payload = self._build_incident_payload(
            title=title,
            severity=severity,
            vertical_name=vertical_name,
            customer_impact_scope=details.get("customer_impact_scope", ""),
            detection_method=details.get("detection_method", "monitor"),
            commander_id=details.get("commander_id"),
        )

        try:
            response = api_client.create_incident(payload)
            incident_data = response.get("data", {})
            incident_id = incident_data.get("id")

            if incident_id:
                result["incident_id"] = incident_id
                result["status"] = "success"
                result["message"] = f"Incident '{title}' created with ID {incident_id}"
                logger.info(result["message"])
            else:
                result["message"] = "No incident ID in API response"
                logger.error(result["message"])

        except RuntimeError as e:
            result["message"] = f"API error creating incident: {str(e)}"
            logger.error(result["message"])
        except Exception as e:
            result["message"] = f"Unexpected error creating incident: {str(e)}"
            logger.error(result["message"])

        return result

    def update_timeline(
        self,
        api_client: DatadogAPIClient,
        incident_id: str,
        content: str,
        timestamp: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Add a timeline entry to an incident.

        Args:
            api_client: Datadog API client instance.
            incident_id: ID of the incident.
            content: Timeline entry content.
            timestamp: Optional ISO 8601 timestamp. Defaults to now.

        Returns:
            Dictionary with:
            - status: "success" or "error"
            - message: Status message
        """
        result = {
            "status": "error",
            "message": "",
        }

        if not timestamp:
            timestamp = datetime.now(timezone.utc).isoformat()

        payload = {
            "data": {
                "type": "incident_timeline_entries",
                "attributes": {
                    "content": content,
                    "timestamp": timestamp,
                },
            }
        }

        try:
            api_client.add_incident_timeline(incident_id, payload)
            result["status"] = "success"
            result["message"] = f"Timeline entry added to incident {incident_id}"
            logger.info(result["message"])
        except RuntimeError as e:
            result["message"] = f"API error adding timeline entry: {str(e)}"
            logger.error(result["message"])
        except Exception as e:
            result["message"] = f"Unexpected error adding timeline entry: {str(e)}"
            logger.error(result["message"])

        return result

    def resolve_incident(
        self,
        api_client: DatadogAPIClient,
        incident_id: str,
    ) -> Dict[str, Any]:
        """
        Resolve an incident.

        Args:
            api_client: Datadog API client instance.
            incident_id: ID of the incident to resolve.

        Returns:
            Dictionary with:
            - status: "success" or "error"
            - message: Status message
        """
        result = {
            "status": "error",
            "message": "",
        }

        payload = {
            "data": {
                "type": "incidents",
                "attributes": {
                    "status": "resolved",
                },
            }
        }

        try:
            api_client.update_incident(incident_id, payload)
            result["status"] = "success"
            result["message"] = f"Incident {incident_id} resolved"
            logger.info(result["message"])
        except RuntimeError as e:
            result["message"] = f"API error resolving incident: {str(e)}"
            logger.error(result["message"])
        except Exception as e:
            result["message"] = f"Unexpected error resolving incident: {str(e)}"
            logger.error(result["message"])

        return result

    def list_active(
        self,
        api_client: DatadogAPIClient,
        vertical_name: Optional[str],
    ) -> Dict[str, Any]:
        """
        List active incidents for a vertical.

        Args:
            api_client: Datadog API client instance.
            vertical_name: Name of the vertical, or ``None`` to match every
                toolkit-tagged active incident across all verticals.

        Returns:
            Dictionary with:
            - total: Number of active incidents
            - incidents: List of incident details
            - errors: List of error messages
        """
        result = {
            "total": 0,
            "incidents": [],
            "errors": [],
        }

        try:
            # Filter for active incidents — either scoped to one vertical or
            # the full toolkit-managed set.
            if vertical_name is None:
                filter_query = "tag:dd-demo-toolkit:true AND status:active"
                scope_label = "all toolkit-managed verticals"
            else:
                filter_query = f"tag:vertical:{vertical_name} AND status:active"
                scope_label = f"vertical '{vertical_name}'"
            response = api_client.list_incidents(filter_query=filter_query)
            incidents = response.get("data", [])
            result["total"] = len(incidents)
            result["incidents"] = incidents
            logger.info(f"Found {len(incidents)} active incident(s) for {scope_label}")
        except RuntimeError as e:
            error_msg = f"Failed to list incidents: {str(e)}"
            result["errors"].append(error_msg)
            logger.error(error_msg)
        except Exception as e:
            error_msg = f"Unexpected error listing incidents: {str(e)}"
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
        Resolve active demo incidents for a vertical.

        Args:
            api_client: Datadog API client instance.
            vertical_name: Name of the vertical, or ``None`` to resolve every
                toolkit-tagged active incident across all verticals
                (orphan-sweep mode).
            dry_run: If True, skip API calls.

        Returns:
            Dictionary with:
            - resolved_ids: List of resolved incident IDs
            - resolved_titles: List of resolved incident titles
            - errors: List of error messages
            - total_resolved: Number of successfully resolved incidents
            - total_errors: Number of failed resolutions
        """
        result = {
            "resolved_ids": [],
            "resolved_titles": [],
            "errors": [],
            "total_resolved": 0,
            "total_errors": 0,
        }

        # Get active incidents for this vertical
        active_result = self.list_active(api_client, vertical_name)
        incidents = active_result.get("incidents", [])

        scope_label = "all toolkit-managed verticals" if vertical_name is None else f"vertical '{vertical_name}'"
        logger.info(f"Found {len(incidents)} active incident(s) to resolve for {scope_label}")

        for incident in incidents:
            try:
                incident_id = incident.get("id")
                incident_title = incident.get("attributes", {}).get("title", "")

                if not incident_id:
                    error_msg = f"Incident missing ID"
                    result["errors"].append(error_msg)
                    result["total_errors"] += 1
                    logger.error(error_msg)
                    continue

                if dry_run:
                    logger.info(f"[DRY RUN] Would resolve incident '{incident_title}' (ID: {incident_id})")
                    result["resolved_ids"].append(incident_id)
                    result["resolved_titles"].append(incident_title)
                    result["total_resolved"] += 1
                else:
                    self.resolve_incident(api_client, incident_id)
                    result["resolved_ids"].append(incident_id)
                    result["resolved_titles"].append(incident_title)
                    result["total_resolved"] += 1
                    logger.info(f"Resolved incident '{incident_title}' (ID: {incident_id})")

            except Exception as e:
                error_msg = f"Error resolving incident {incident.get('id')}: {str(e)}"
                result["errors"].append(error_msg)
                result["total_errors"] += 1
                logger.error(error_msg)

        logger.info(
            f"Incident teardown complete: {result['total_resolved']} resolved, "
            f"{result['total_errors']} errors"
        )

        return result

    def _build_incident_payload(
        self,
        title: str,
        severity: str,
        vertical_name: str,
        customer_impact_scope: str = "",
        detection_method: str = "monitor",
        commander_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Build an incident payload.

        Args:
            title: Incident title.
            severity: Severity level.
            vertical_name: Vertical name for tagging.
            customer_impact_scope: Customer impact description.
            detection_method: How the incident was detected (default: monitor).
            commander_id: Optional user ID of incident commander.

        Returns:
            Incident payload ready for API submission.
        """
        attributes = {
            "title": title,
            "severity": severity,
            "customer_impact_scope": customer_impact_scope,
            "fields": {
                "detection_method": {
                    "type": "dropdown",
                    "value": detection_method,
                }
            },
            "tags": [f"vertical:{vertical_name}", "dd-demo-toolkit:true", f"team:dd-demo-{vertical_name}"],
        }

        relationships = {}

        if commander_id:
            relationships["commander_user"] = {
                "data": {
                    "type": "users",
                    "id": commander_id,
                }
            }

        payload = {
            "data": {
                "type": "incidents",
                "attributes": attributes,
            }
        }

        if relationships:
            payload["data"]["relationships"] = relationships

        return payload
