"""
Resource managers for deploying and managing Datadog resources across verticals.

Exports:
- DashboardManager: Manages dashboard deployments
- MonitorManager: Manages monitor deployments
- NotebookManager: Manages notebook deployments
- SLOManager: Manages SLO deployments
- ServiceCatalogManager: Manages service catalog registrations
- WorkflowManager: Manages workflow automation deployments
- IncidentManager: Manages incident creation and lifecycle
- CaseManager: Manages case creation and lifecycle
- ResourceManager: Orchestrates all resource types
"""

from dd_demo_toolkit.resources.dashboards import DashboardManager
from dd_demo_toolkit.resources.monitors import MonitorManager
from dd_demo_toolkit.resources.notebooks import NotebookManager
from dd_demo_toolkit.resources.slos import SLOManager
from dd_demo_toolkit.resources.services import ServiceCatalogManager
from dd_demo_toolkit.resources.workflows import WorkflowManager
from dd_demo_toolkit.resources.incidents import IncidentManager
from dd_demo_toolkit.resources.cases import CaseManager
from dd_demo_toolkit.resources.manager import ResourceManager

__all__ = [
    "DashboardManager",
    "MonitorManager",
    "NotebookManager",
    "SLOManager",
    "ServiceCatalogManager",
    "WorkflowManager",
    "IncidentManager",
    "CaseManager",
    "ResourceManager",
]
