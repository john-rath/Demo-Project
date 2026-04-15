"""
Core simulator engine for dd-demo-toolkit.

Config-driven simulation of devices and services with realistic metrics and traces.
"""

import random
import signal
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Callable
from contextlib import contextmanager
import logging

from opentelemetry.metrics import Meter
from opentelemetry.trace import Tracer, Status, StatusCode
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.metrics import MeterProvider

try:
    from opentelemetry.sdk._logs import LoggerProvider
except ImportError:
    from opentelemetry.sdk.logs import LoggerProvider

from dd_demo_toolkit.utils.otel import setup_otel, shutdown_otel
from dd_demo_toolkit.simulator.plugins import IncidentPlugin

logger = logging.getLogger(__name__)


@dataclass
class MetricConfig:
    """Configuration for a single metric."""
    name: str
    type: str  # "gauge", "counter", "histogram"
    unit: str = ""
    range: List[float] = field(default_factory=lambda: [0, 100])
    drift: float = 0.5  # Gaussian noise std dev
    description: str = ""


@dataclass
class DeviceProfile:
    """Represents a device instance with its configuration and current state."""
    device_id: str
    type: str
    manufacturer: str
    model: str
    firmware: str
    category: str
    location: Dict[str, str]  # {dimension_name: value, ...}
    battery_powered: bool = False
    metrics: List[MetricConfig] = field(default_factory=list)
    state: Dict[str, float] = field(default_factory=dict)  # Current metric values

    def __post_init__(self):
        """Initialize metric state with midpoints of ranges."""
        for metric in self.metrics:
            mid = (metric.range[0] + metric.range[1]) / 2.0
            self.state[metric.name] = mid


@dataclass
class ServiceOperation:
    """Configuration for a service operation."""
    name: str
    latency_base_ms: float = 100
    latency_p99_ms: float = 500
    error_rate: float = 0.0
    description: str = ""


@dataclass
class ServiceDependency:
    """Represents a dependency from one service to another."""
    service: str
    operation: str
    probability: float = 1.0


@dataclass
class ServiceProfile:
    """Represents a service with its configuration and operations."""
    name: str
    language: str
    framework: str
    operations: List[ServiceOperation]
    dependencies: List[ServiceDependency] = field(default_factory=list)
    tags: Dict[str, str] = field(default_factory=dict)


class SimulatorEngine:
    """
    Config-driven simulator engine for generating realistic metrics and traces.

    The engine:
    - Loads vertical configs and builds a fleet of devices
    - Tracks device state and applies drift to metrics each tick
    - Emits metrics via OpenTelemetry
    - Generates service traces with realistic latencies and error rates
    - Supports plugin-based incident injection
    """

    def __init__(
        self,
        config: Dict[str, Any],
        meter: Optional[Meter] = None,
        tracer: Optional[Tracer] = None,
        log_emitter: Optional[Any] = None,
        meter_provider: Optional[MeterProvider] = None,
        tracer_provider: Optional[TracerProvider] = None,
        logger_provider: Optional[LoggerProvider] = None,
    ):
        """
        Initialize the simulator engine.

        Args:
            config: Parsed vertical configuration dictionary.
            meter: OpenTelemetry Meter instance. If None, creates one.
            tracer: OpenTelemetry Tracer instance. If None, creates one.
            log_emitter: OpenTelemetry Logger instance.
            meter_provider: MeterProvider for shutdown. Required if meter is None.
            tracer_provider: TracerProvider for shutdown. Required if tracer is None.
            logger_provider: LoggerProvider for shutdown.
        """
        self.config = config
        self.vertical_name = config["vertical"]["name"]
        self.env_prefix = config["vertical"]["env_prefix"]
        self.display_name = config["vertical"]["display_name"]

        # Initialize OTel providers
        if meter is None:
            self.meter, self.tracer, self.log_emitter, self.meter_provider, self.tracer_provider, self.logger_provider = setup_otel()
            self._owns_providers = True
        else:
            self.meter = meter
            self.tracer = tracer
            self.log_emitter = log_emitter
            self.meter_provider = meter_provider
            self.tracer_provider = tracer_provider
            self.logger_provider = logger_provider
            self._owns_providers = False

        # Build service catalog (before fleet, since _build_fleet logs service count)
        self.services: Dict[str, ServiceProfile] = {}
        self._build_services()

        # Build device fleet
        self.fleet: List[DeviceProfile] = []
        self._build_fleet()

        # OTel instruments (cached)
        self.instruments: Dict[str, Any] = {}
        self._create_instruments()

        # Plugin system
        self.plugins: List[IncidentPlugin] = []
        self.tick_count = 0
        self._shutdown = False
        self._shutdown_event = None

    def _build_fleet(self) -> None:
        """Build device fleet from config."""
        device_id_counter = 0
        locations_config = self.config.get("locations", {})
        dimensions = locations_config.get("dimensions", [])

        # Generate all location combinations
        location_values = {}
        for dim in dimensions:
            location_values[dim["name"]] = dim.get("values", [])

        # Cartesian product of location dimensions
        def generate_locations(dims_list, idx=0, current=None):
            if current is None:
                current = {}
            if idx >= len(dims_list):
                yield current.copy()
                return
            dim_name = dims_list[idx]["name"]
            for value in location_values[dim_name]:
                current[dim_name] = value
                yield from generate_locations(dims_list, idx + 1, current)

        locations = list(generate_locations(dimensions))

        # Create devices for each category
        device_categories = self.config.get("device_categories", {})

        for category_name, category_config in device_categories.items():
            devices_config = category_config.get("devices", [])
            department_pool = category_config.get("department_pool", [])

            for device_config in devices_config:
                device_type = device_config.get("type")
                manufacturer = device_config.get("manufacturer", "Unknown")
                model = device_config.get("model", "Unknown")
                firmware = device_config.get("firmware", "1.0")
                count = device_config.get("count", 1)
                battery_powered = device_config.get("battery_powered", False)
                metrics_config = device_config.get("metrics", [])

                # Parse metrics
                metrics = []
                for metric_def in metrics_config:
                    metric = MetricConfig(
                        name=metric_def.get("name"),
                        type=metric_def.get("type", "gauge"),
                        unit=metric_def.get("unit", ""),
                        range=metric_def.get("range", [0, 100]),
                        drift=metric_def.get("drift", 0.5),
                        description=metric_def.get("description", ""),
                    )
                    metrics.append(metric)

                # Create device instances
                for device_instance in range(count):
                    # Select location (round-robin through locations)
                    location_idx = device_id_counter % len(locations)
                    location = locations[location_idx]

                    # If department_pool is specified, override department dimension
                    if department_pool:
                        location = location.copy()
                        location["department"] = random.choice(department_pool)

                    device = DeviceProfile(
                        device_id=f"{self.env_prefix}-{category_name}-{device_id_counter}",
                        type=device_type,
                        manufacturer=manufacturer,
                        model=model,
                        firmware=firmware,
                        category=category_name,
                        location=location,
                        battery_powered=battery_powered,
                        metrics=metrics,
                    )
                    self.fleet.append(device)
                    device_id_counter += 1

        logger.info(f"Built fleet with {len(self.fleet)} devices across {len(self.services)} services")

    def _build_services(self) -> None:
        """Build service catalog from config."""
        services_config = self.config.get("services", [])

        for service_config in services_config:
            name = service_config.get("name")
            language = service_config.get("language", "unknown")
            framework = service_config.get("framework", "")
            tags = service_config.get("tags", {})

            # Parse operations
            operations = []
            for op_config in service_config.get("operations", []):
                op = ServiceOperation(
                    name=op_config.get("name"),
                    latency_base_ms=op_config.get("latency_base_ms", 100),
                    latency_p99_ms=op_config.get("latency_p99_ms", 500),
                    error_rate=op_config.get("error_rate", 0.0),
                    description=op_config.get("description", ""),
                )
                operations.append(op)

            # Parse dependencies
            dependencies = []
            for dep_config in service_config.get("dependencies", []):
                dep = ServiceDependency(
                    service=dep_config.get("service"),
                    operation=dep_config.get("operation"),
                    probability=dep_config.get("probability", 1.0),
                )
                dependencies.append(dep)

            service = ServiceProfile(
                name=name,
                language=language,
                framework=framework,
                operations=operations,
                dependencies=dependencies,
                tags=tags,
            )
            self.services[name] = service

    def _create_instruments(self) -> None:
        """Create OTel instruments for all metrics in device config."""
        device_categories = self.config.get("device_categories", {})

        for category_name, category_config in device_categories.items():
            devices_config = category_config.get("devices", [])

            for device_config in devices_config:
                metrics_config = device_config.get("metrics", [])

                for metric_def in metrics_config:
                    metric_name = metric_def.get("name")
                    metric_type = metric_def.get("type", "gauge")
                    unit = metric_def.get("unit", "")
                    description = metric_def.get("description", "")

                    if metric_type == "gauge":
                        self.instruments[metric_name] = self.meter.create_gauge(
                            name=metric_name,
                            unit=unit,
                            description=description,
                        )
                    elif metric_type == "counter":
                        self.instruments[metric_name] = self.meter.create_counter(
                            name=metric_name,
                            unit=unit,
                            description=description,
                        )
                    elif metric_type == "histogram":
                        self.instruments[metric_name] = self.meter.create_histogram(
                            name=metric_name,
                            unit=unit,
                            description=description,
                        )

    def register_plugin(self, plugin: IncidentPlugin) -> None:
        """
        Register an incident plugin.

        Args:
            plugin: IncidentPlugin instance.
        """
        self.plugins.append(plugin)
        logger.info(f"Registered plugin: {plugin.get_incident_name()}")

    def tick(self) -> None:
        """
        Perform one simulation tick.

        - Updates device metrics with drift
        - Emits metrics via OTel
        - Generates service traces
        - Calls registered plugins
        """
        # Call plugins
        for plugin in self.plugins:
            try:
                plugin.on_tick(self.tick_count, self.fleet, self)
            except Exception as e:
                logger.error(f"Plugin {plugin.get_incident_name()} failed: {e}")

        # Update devices and emit metrics
        for device in self.fleet:
            self._update_device(device)
            self._emit_device_metrics(device)

        # Generate service traces
        for service_name in self.services:
            self._generate_service_trace(service_name)

        self.tick_count += 1

    def _update_device(self, device: DeviceProfile) -> None:
        """
        Apply drift to device metrics.

        Each metric is updated with Gaussian noise, then clamped to its range.

        Args:
            device: DeviceProfile to update.
        """
        for metric in device.metrics:
            current = device.state[metric.name]
            # Apply Gaussian drift
            drift_amount = random.gauss(0, metric.drift)
            new_value = current + drift_amount
            # Clamp to range
            new_value = max(metric.range[0], min(metric.range[1], new_value))
            device.state[metric.name] = new_value

    def _emit_device_metrics(self, device: DeviceProfile) -> None:
        """
        Emit device metrics via OTel.

        Args:
            device: DeviceProfile to emit metrics for.
        """
        for metric in device.metrics:
            value = device.state[metric.name]
            instrument = self.instruments.get(metric.name)

            if instrument is None:
                continue

            # Build attributes from device info
            attributes = {
                "device.id": device.device_id,
                "device.type": device.type,
                "device.manufacturer": device.manufacturer,
                "device.model": device.model,
                "device.firmware": device.firmware,
                "category": device.category,
                "battery_powered": str(device.battery_powered),
            }
            # Add location dimensions
            attributes.update(device.location)

            # Emit based on metric type
            if metric.type == "gauge":
                instrument.set(value, attributes=attributes)
            elif metric.type == "counter":
                instrument.add(max(0, value), attributes=attributes)
            elif metric.type == "histogram":
                instrument.record(value, attributes=attributes)

    def _generate_service_trace(self, service_name: str) -> None:
        """
        Generate a trace for a service operation.

        Args:
            service_name: Name of the service to trace.
        """
        service = self.services.get(service_name)
        if not service or not service.operations:
            return

        # Pick a random operation
        operation = random.choice(service.operations)

        # Decide if this call errors
        should_error = random.random() < operation.error_rate

        # Calculate latency (p99 distribution simplified)
        if random.random() < 0.01:  # 1% of requests
            latency_ms = operation.latency_p99_ms
        else:
            # Normal distribution around base
            latency_ms = max(
                operation.latency_base_ms,
                random.gauss(operation.latency_base_ms, operation.latency_base_ms * 0.1),
            )

        # Create parent span
        with self.tracer.start_as_current_span(
            f"{service_name} {operation.name}"
        ) as span:
            span.set_attribute("service.name", service_name)
            span.set_attribute("service.language", service.language)
            span.set_attribute("service.framework", service.framework)
            span.set_attribute("operation", operation.name)
            span.set_attribute("latency_ms", latency_ms)

            if should_error:
                span.set_status(Status(StatusCode.ERROR, "Internal error"))
            else:
                span.set_status(Status(StatusCode.OK))

            # Generate child spans for dependencies
            for dependency in service.dependencies:
                if random.random() < dependency.probability:
                    self._generate_dependency_trace(dependency, span)

    def _generate_dependency_trace(self, dependency: ServiceDependency, parent_span) -> None:
        """
        Generate a child span for a service dependency.

        Args:
            dependency: ServiceDependency to trace.
            parent_span: Parent OTel span.
        """
        with self.tracer.start_as_current_span(
            f"{dependency.service} {dependency.operation}"
        ) as child_span:
            child_span.set_attribute("service.name", dependency.service)
            child_span.set_attribute("operation", dependency.operation)
            child_span.set_attribute("parent_service", parent_span.attributes.get("service.name", "unknown"))

    def run(self, interval_sec: float = 1.0) -> None:
        """
        Run the main simulator loop.

        Ticks at the specified interval with graceful shutdown on SIGINT.

        Args:
            interval_sec: Time between ticks in seconds. Defaults to 1.0.
        """
        def handle_shutdown(signum, frame):
            logger.info("Shutdown signal received, stopping simulator...")
            self._shutdown = True

        # Register signal handler
        signal.signal(signal.SIGINT, handle_shutdown)
        signal.signal(signal.SIGTERM, handle_shutdown)

        logger.info(f"Starting simulator for vertical '{self.display_name}'")
        logger.info(f"Fleet size: {len(self.fleet)} devices, {len(self.services)} services")

        try:
            while not self._shutdown:
                tick_start = time.time()
                try:
                    self.tick()
                except Exception as e:
                    logger.error(f"Tick {self.tick_count} failed: {e}", exc_info=True)

                tick_duration = time.time() - tick_start
                sleep_time = max(0, interval_sec - tick_duration)
                if sleep_time > 0:
                    time.sleep(sleep_time)

                # Log progress periodically
                if self.tick_count % 60 == 0:
                    logger.info(f"Tick {self.tick_count} (elapsed: {self.tick_count * interval_sec:.1f}s)")

        except KeyboardInterrupt:
            logger.info("Interrupted by user")
        finally:
            self.shutdown()

    def shutdown(self) -> None:
        """Gracefully shutdown the simulator and flush providers."""
        if self._owns_providers:
            shutdown_otel(self.meter_provider, self.tracer_provider, self.logger_provider)
        logger.info(f"Simulator shutdown. Total ticks: {self.tick_count}")


@contextmanager
def simulator_context(config: Dict[str, Any], interval_sec: float = 1.0):
    """
    Context manager for running a simulator.

    Usage:
        config = load_vertical_config("healthcare")
        with simulator_context(config, interval_sec=1.0) as engine:
            engine.register_plugin(MyPlugin())

    Args:
        config: Parsed vertical configuration.
        interval_sec: Tick interval in seconds.

    Yields:
        SimulatorEngine instance.
    """
    engine = SimulatorEngine(config)
    try:
        yield engine
    finally:
        engine.shutdown()
