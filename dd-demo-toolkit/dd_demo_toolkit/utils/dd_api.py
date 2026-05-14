"""
Datadog API client wrapper for creating and managing dashboards, monitors, notebooks, and SLOs.
"""

import logging
import os
import time
from typing import Optional, Dict, Any, List
import requests
from dotenv import load_dotenv


logger = logging.getLogger(__name__)

# Retry policy for transient Datadog API failures. Only 5xx and
# connection / read timeouts are retried; 4xx errors are deterministic
# and won't get better. The backoff is short enough to keep a deploy
# pass under a minute even when several requests retry.
_RETRY_STATUS_CODES = {500, 502, 503, 504}
_RETRY_MAX_ATTEMPTS = 3
_RETRY_BACKOFF_BASE_SEC = 1.5


class DatadogAPIClient:
    """
    Wrapper around Datadog REST API with support for multiple sites.

    Reads credentials from environment variables or .env file:
    - DD_API_KEY: Datadog API key
    - DD_APP_KEY: Datadog application key
    - DD_SITE: Datadog site (e.g., datadoghq.com, us3.datadoghq.com, etc.)
    """

    # Mapping of site domains to API base URLs
    SITE_MAPPING = {
        "datadoghq.com": "https://api.datadoghq.com",
        "us3.datadoghq.com": "https://api.us3.datadoghq.com",
        "us5.datadoghq.com": "https://api.us5.datadoghq.com",
        "datadoghq.eu": "https://api.datadoghq.eu",
        "ap1.datadoghq.com": "https://api.ap1.datadoghq.com",
        "ddog-gov.com": "https://api.ddog-gov.com",
    }

    def __init__(self, api_key: Optional[str] = None, app_key: Optional[str] = None,
                 site: Optional[str] = None):
        """
        Initialize the Datadog API client.

        Args:
            api_key: Datadog API key. If None, reads from DD_API_KEY env var.
            app_key: Datadog application key. If None, reads from DD_APP_KEY env var.
            site: Datadog site domain. If None, reads from DD_SITE env var. Defaults to datadoghq.com.

        Raises:
            ValueError: If required credentials are missing.
        """
        self.api_key = api_key or os.getenv("DD_API_KEY")
        self.app_key = app_key or os.getenv("DD_APP_KEY")
        self.site = site or os.getenv("DD_SITE", "datadoghq.com")

        if not self.api_key:
            raise ValueError("DD_API_KEY not provided and not found in environment")
        if not self.app_key:
            raise ValueError("DD_APP_KEY not provided and not found in environment")

        # Resolve site to base URL
        if self.site not in self.SITE_MAPPING:
            raise ValueError(f"Unknown DD_SITE: {self.site}. Supported sites: {list(self.SITE_MAPPING.keys())}")

        self.base_url = self.SITE_MAPPING[self.site]

        # Common headers
        self.headers = {
            "DD-API-KEY": self.api_key,
            "DD-APPLICATION-KEY": self.app_key,
            "Content-Type": "application/json",
        }

    @classmethod
    def load_env(cls, path: str = ".env") -> "DatadogAPIClient":
        """
        Load environment variables from a .env file and create a client.

        Args:
            path: Path to .env file. Defaults to ".env".

        Returns:
            DatadogAPIClient instance.

        Raises:
            ValueError: If required credentials are missing after loading .env.
        """
        load_dotenv(path)
        return cls()

    def _request(self, method: str, endpoint: str, json_data: Optional[Dict[str, Any]] = None,
                 params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Make a request to the Datadog API.

        Args:
            method: HTTP method (GET, POST, DELETE, etc.).
            endpoint: API endpoint (e.g., "/api/v1/dashboard").
            json_data: JSON payload for POST/PUT requests.
            params: Query parameters.

        Returns:
            Parsed JSON response.

        Raises:
            RuntimeError: If the request fails.
        """
        url = f"{self.base_url}{endpoint}"
        method_upper = method.upper()

        for attempt in range(1, _RETRY_MAX_ATTEMPTS + 1):
            try:
                if method_upper == "GET":
                    response = requests.get(url, headers=self.headers, params=params, timeout=10)
                elif method_upper == "POST":
                    response = requests.post(url, headers=self.headers, json=json_data, params=params, timeout=10)
                elif method_upper == "PUT":
                    response = requests.put(url, headers=self.headers, json=json_data, params=params, timeout=10)
                elif method_upper == "PATCH":
                    response = requests.patch(url, headers=self.headers, json=json_data, params=params, timeout=10)
                elif method_upper == "DELETE":
                    response = requests.delete(url, headers=self.headers, params=params, timeout=10)
                else:
                    raise ValueError(f"Unsupported HTTP method: {method}")

                response.raise_for_status()
                return response.json() if response.text else {}

            except requests.exceptions.HTTPError as e:
                status = e.response.status_code if e.response is not None else None
                # Retry transient server errors; let client errors fail fast.
                if status in _RETRY_STATUS_CODES and attempt < _RETRY_MAX_ATTEMPTS:
                    wait = _RETRY_BACKOFF_BASE_SEC * (2 ** (attempt - 1))
                    logger.warning(
                        "Datadog API %s %s returned %d, retrying in %.1fs "
                        "(attempt %d/%d)",
                        method_upper, endpoint, status, wait,
                        attempt, _RETRY_MAX_ATTEMPTS,
                    )
                    time.sleep(wait)
                    continue
                # Final attempt or non-retriable status: surface the failure.
                body = ""
                if e.response is not None:
                    try:
                        body = e.response.text[:1000]  # Limit to 1000 chars
                    except Exception:
                        pass
                raise RuntimeError(
                    f"Datadog API request failed ({method} {endpoint}): "
                    f"{status if status is not None else 'N/A'} - {body or str(e)}"
                )
            except (requests.exceptions.ConnectionError,
                    requests.exceptions.Timeout) as e:
                # Network-level transient failures — same retry policy.
                if attempt < _RETRY_MAX_ATTEMPTS:
                    wait = _RETRY_BACKOFF_BASE_SEC * (2 ** (attempt - 1))
                    logger.warning(
                        "Datadog API %s %s network error (%s), retrying in %.1fs "
                        "(attempt %d/%d)",
                        method_upper, endpoint, type(e).__name__, wait,
                        attempt, _RETRY_MAX_ATTEMPTS,
                    )
                    time.sleep(wait)
                    continue
                raise RuntimeError(f"Datadog API request failed ({method} {endpoint}): {str(e)}")
            except requests.exceptions.RequestException as e:
                raise RuntimeError(f"Datadog API request failed ({method} {endpoint}): {str(e)}")

    def create_dashboard(self, json_payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create a new dashboard.

        Args:
            json_payload: Dashboard configuration JSON.

        Returns:
            API response with created dashboard details.
        """
        return self._request("POST", "/api/v1/dashboard", json_data=json_payload)

    def delete_dashboard(self, dashboard_id: str) -> Dict[str, Any]:
        """
        Delete a dashboard by ID.

        Args:
            dashboard_id: ID of the dashboard to delete.

        Returns:
            API response.
        """
        return self._request("DELETE", f"/api/v1/dashboard/{dashboard_id}")

    def list_dashboards(self) -> Dict[str, Any]:
        """
        List all dashboards, paging through every result.

        The Datadog /api/v1/dashboard endpoint paginates with ``start`` (offset)
        and ``count`` (page size, max 100). Without pagination the teardown
        step only sees the first page, which causes older toolkit-managed
        dashboards to survive across demo runs.

        Returns:
            API response shaped like a single page ({"dashboards": [...]}) but
            with the full result set concatenated across all pages.
        """
        page_size = 100
        start = 0
        all_dashboards: List[Dict[str, Any]] = []
        last_response: Dict[str, Any] = {}

        while True:
            params = {"start": start, "count": page_size}
            response = self._request("GET", "/api/v1/dashboard", params=params)
            last_response = response
            page = response.get("dashboards", []) or []
            all_dashboards.extend(page)
            if len(page) < page_size:
                break
            start += page_size

        # Preserve the envelope of the last page for callers that read other
        # top-level fields, but replace the list with the fully-paged result.
        result = dict(last_response)
        result["dashboards"] = all_dashboards
        return result

    def create_monitor(self, json_payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create a new monitor.

        Args:
            json_payload: Monitor configuration JSON.

        Returns:
            API response with created monitor details.
        """
        return self._request("POST", "/api/v1/monitor", json_data=json_payload)

    def delete_monitor(self, monitor_id: int) -> Dict[str, Any]:
        """
        Delete a monitor by ID.

        Args:
            monitor_id: ID of the monitor to delete.

        Returns:
            API response.
        """
        return self._request("DELETE", f"/api/v1/monitor/{monitor_id}")

    def list_monitors(self, tag: Optional[str] = None) -> Dict[str, Any]:
        """
        List monitors, paging through every result.

        The Datadog /api/v1/monitor endpoint paginates with ``page`` (0-indexed)
        and ``page_size`` (default 100, max 1000). Without pagination, teardown
        only sees the first page, so monitors beyond page 1 survive across
        demo runs and accumulate indefinitely.

        Args:
            tag: Optional tag filter. Passed as ``monitor_tags`` (comma-
                separated tag expression) per Datadog's list-monitor API.
                NOTE: the previous implementation incorrectly used the
                ``name`` parameter for this, which filters by monitor name
                rather than by tag.

        Returns:
            API response shaped as a single page ({"monitors": [...]}) but with
            the full result set concatenated across all pages.
        """
        page_size = 1000  # Datadog max
        page = 0
        all_monitors: List[Dict[str, Any]] = []

        while True:
            params: Dict[str, Any] = {
                "page": page,
                "page_size": page_size,
            }
            if tag:
                params["monitor_tags"] = tag
            response = self._request("GET", "/api/v1/monitor", params=params)

            # /api/v1/monitor historically returns a bare JSON array. Be
            # defensive and accept either shape.
            if isinstance(response, list):
                page_items = response
            else:
                page_items = response.get("monitors", []) or []

            all_monitors.extend(page_items)
            if len(page_items) < page_size:
                break
            page += 1

        # Return the full list in the "monitors" envelope. MonitorManager
        # handles both the list and dict shapes, so this is compatible with
        # both of the historical response formats.
        return {"monitors": all_monitors}

    def create_notebook(self, json_payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create a new notebook.

        Args:
            json_payload: Notebook configuration JSON.

        Returns:
            API response with created notebook details.
        """
        return self._request("POST", "/api/v1/notebooks", json_data=json_payload)

    def delete_notebook(self, notebook_id: int) -> Dict[str, Any]:
        """
        Delete a notebook by ID.

        Args:
            notebook_id: ID of the notebook to delete.

        Returns:
            API response.
        """
        return self._request("DELETE", f"/api/v1/notebooks/{notebook_id}")

    def list_notebooks(self) -> Dict[str, Any]:
        """
        List all notebooks, paging through every result.

        The Datadog /api/v1/notebooks endpoint paginates with ``start`` (offset)
        and ``count`` (max 100). Without pagination the teardown step sees only
        the first 100 notebooks, which is why older toolkit-managed notebooks
        linger across demo runs.

        Returns:
            API response shaped like a single page ({"data": [...]}) but with
            the full result set concatenated across all pages.
        """
        page_size = 100  # Datadog max for notebooks
        start = 0
        all_notebooks: List[Dict[str, Any]] = []
        last_response: Dict[str, Any] = {}

        while True:
            params = {"start": start, "count": page_size}
            response = self._request("GET", "/api/v1/notebooks", params=params)
            last_response = response
            page = response.get("data", []) or []
            all_notebooks.extend(page)
            if len(page) < page_size:
                break
            start += page_size

        # Preserve meta/links from the last page but swap in the fully-paged
        # data array.
        result = dict(last_response)
        result["data"] = all_notebooks
        return result

    def create_slo(self, json_payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create a new SLO (Service Level Objective).

        Args:
            json_payload: SLO configuration JSON.

        Returns:
            API response with created SLO details.
        """
        return self._request("POST", "/api/v1/slo", json_data=json_payload)

    def delete_slo(self, slo_id: str) -> Dict[str, Any]:
        """
        Delete an SLO by ID.

        Args:
            slo_id: ID of the SLO to delete.

        Returns:
            API response.
        """
        return self._request("DELETE", f"/api/v1/slo/{slo_id}")

    def register_service(self, json_payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Register a service in the Datadog catalog (Service Definition API v2).

        Args:
            json_payload: Service definition as a dict (JSON payload).

        Returns:
            API response.

        Note:
            Uses the v2 service definitions endpoint with JSON content type.
        """
        return self._request("POST", "/api/v2/services/definitions", json_data=json_payload)

    # ===== Workflow Automation API =====

    def create_workflow(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create a new workflow via Datadog Workflow Automation API.

        Args:
            payload: Workflow payload containing data.attributes.name, description, trigger, steps, tags, etc.

        Returns:
            API response with created workflow details.
        """
        return self._request("POST", "/api/v2/workflows", json_data=payload)

    def delete_workflow(self, workflow_id: str) -> Dict[str, Any]:
        """
        Delete a workflow by ID.

        Args:
            workflow_id: ID of the workflow to delete.

        Returns:
            API response.
        """
        return self._request("DELETE", f"/api/v2/workflows/{workflow_id}")

    def list_workflows(self, tag_filter: Optional[str] = None) -> Dict[str, Any]:
        """
        List all workflows, optionally filtered by tag.

        Args:
            tag_filter: Optional tag filter (e.g., "vertical:healthcare").

        Returns:
            API response with workflows list.
        """
        params = {}
        if tag_filter:
            params["filter[tags]"] = tag_filter
        return self._request("GET", "/api/v2/workflows", params=params)

    # ===== Incident Management API =====

    def create_incident(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create a new incident via Datadog Incident Management API.

        Args:
            payload: Incident payload with data.attributes (title, severity, customer_impact_scope, fields, etc.)
                     and data.relationships (commander_user, etc.).

        Returns:
            API response with created incident details.
        """
        return self._request("POST", "/api/v2/incidents", json_data=payload)

    def update_incident(self, incident_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Update an incident.

        Args:
            incident_id: ID of the incident to update.
            payload: Update payload with data.attributes to modify.

        Returns:
            API response with updated incident details.
        """
        return self._request("PATCH", f"/api/v2/incidents/{incident_id}", json_data=payload)

    def list_incidents(self, filter_query: Optional[str] = None) -> Dict[str, Any]:
        """
        List incidents, optionally filtered by query.

        Args:
            filter_query: Optional filter query (e.g., "tag:vertical:healthcare").

        Returns:
            API response with incidents list.
        """
        params = {}
        if filter_query:
            params["filter"] = filter_query
        return self._request("GET", "/api/v2/incidents", params=params)

    def add_incident_timeline(self, incident_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Add a timeline entry to an incident.

        Args:
            incident_id: ID of the incident.
            payload: Timeline entry payload with content and timestamp.

        Returns:
            API response.
        """
        return self._request("POST", f"/api/v2/incidents/{incident_id}/timeline", json_data=payload)

    # ===== Case Management API =====

    def create_case(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create a new case via Datadog Case Management API.

        Args:
            payload: Case payload with data.attributes (title, description, priority, type, status, etc.)
                     and data.relationships.

        Returns:
            API response with created case details.
        """
        return self._request("POST", "/api/v2/cases", json_data=payload)

    def update_case(self, case_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Update a case.

        Args:
            case_id: ID of the case to update.
            payload: Update payload with data.attributes to modify.

        Returns:
            API response with updated case details.
        """
        return self._request("PATCH", f"/api/v2/cases/{case_id}", json_data=payload)

    def list_cases(self, filter_query: Optional[str] = None) -> Dict[str, Any]:
        """
        List cases, optionally filtered by query.

        Args:
            filter_query: Optional filter query.

        Returns:
            API response with cases list.
        """
        params = {}
        if filter_query:
            params["filter"] = filter_query
        return self._request("GET", "/api/v2/cases", params=params)

    # ===== Case Management Projects API =====

    def list_case_projects(self) -> Dict[str, Any]:
        """
        List all Case Management projects.

        Returns:
            API response with projects list.
        """
        return self._request("GET", "/api/v2/cases/projects")

    def create_case_project(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create a new Case Management project.

        Args:
            payload: Project payload with data.attributes (name, key).

        Returns:
            API response with created project details.
        """
        return self._request("POST", "/api/v2/cases/projects", json_data=payload)
