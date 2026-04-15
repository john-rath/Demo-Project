"""
Plugin system for SimulatorEngine to inject incidents and modify behavior.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class IncidentPlugin(ABC):
    """
    Abstract base class for incident plugins.

    Plugins are called on each tick to simulate incidents by modifying device states,
    injecting errors, or triggering anomalies.
    """

    @abstractmethod
    def on_tick(self, tick_count: int, fleet: List[Dict[str, Any]], engine: Any) -> None:
        """
        Called on each simulator tick.

        Args:
            tick_count: Current tick number (starts at 0).
            fleet: List of device profiles in the fleet.
            engine: Reference to the SimulatorEngine instance for accessing meters/tracers.
        """
        pass

    @abstractmethod
    def get_incident_name(self) -> str:
        """
        Get a human-readable name for this incident plugin.

        Returns:
            String name of the incident (e.g., "High Latency Spike", "Device Failure").
        """
        pass

    def reset(self) -> None:
        """
        Optional: Reset plugin state. Called when simulator is reset.
        """
        pass


__all__ = ["IncidentPlugin"]
