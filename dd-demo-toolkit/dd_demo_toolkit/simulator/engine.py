"""
Core simulator engine for dd-demo-toolkit.

Config-driven simulation of devices and services with realistic metrics, traces,
and logs.  Follows the per-service provider pattern from the original
sensing-hospital demo so that each application service gets its own
TracerProvider and LoggerProvider.  This enables:

  - Datadog APM Service Map (each span carries the correct service resource)
  - Trace ↔ Log correlation (LoggingHandler auto-injects trace_id/span_id)
  - Host-level views (each service appears on its virtual host)
"""

import os
import random
import signal
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from contextlib import contextmanager
import logging

from opentelemetry.trace import Status, StatusCode

from dd_demo_toolkit.utils.otel import (
    setup_global_meter,
    setup_per_service_providers,
    shutdown_all,
)
from dd_demo_toolkit.simulator.plugins import IncidentPlugin

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data-classes
# ---------------------------------------------------------------------------

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
    # Optional downstream spans within the same service (db, cache, etc.)
    downstream: List[Dict[str, Any]] = field(default_factory=list)


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
    host: str  # virtual hostname e.g. "portal-web-01"
    operations: List[ServiceOperation]
    dependencies: List[ServiceDependency] = field(default_factory=list)
    tags: Dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Default downstream spans per service framework (enriches traces)
# ---------------------------------------------------------------------------

_DOWNSTREAM_TEMPLATES = {
    "java": [
        {"name": "db.query", "system": "postgresql", "latency_ms": (2, 30)},
        {"name": "cache.get", "system": "redis", "latency_ms": (0.5, 5)},
    ],
    "dotnet": [
        {"name": "db.query", "system": "mssql", "latency_ms": (3, 40)},
        {"name": "cache.get", "system": "redis", "latency_ms": (0.5, 5)},
    ],
    "python": [
        {"name": "db.query", "system": "postgresql", "latency_ms": (2, 25)},
    ],
    "go": [
        {"name": "db.query", "system": "postgresql", "latency_ms": (1, 15)},
    ],
    "swift": [],  # Mobile doesn't hit DB directly
}


class SimulatorEngine:
    """
    Config-driven simulator engine for generating realistic metrics, traces,
    and logs.

    The engine:
    - Loads vertical configs and builds a fleet of devices
    - Tracks device state and applies drift to metrics each tick
    - Emits metrics via OpenTelemetry (global MeterProvider)
    - Generates service traces with per-service TracerProviders
    - Emits structured logs via per-service LoggerProviders (trace-log correlation)
    - Supports plugin-based incident injection
    """

    def __init__(self, config: Dict[str, Any]):
        """
        Initialize the simulator engine.

        Args:
            config: Parsed vertical configuration dictionary.
        """
        self.config = config
        self.vertical_name = config["vertical"]["name"]
        self.env_prefix = config["vertical"]["env_prefix"]
        self.display_name = config["vertical"]["display_name"]

        # Build service catalog first (needed for per-service provider setup)
        self.services: Dict[str, ServiceProfile] = {}
        self._build_services()

        # --- OTel Setup ---
        # 1. Global MeterProvider for device / IoT metrics
        self.meter_provider, self.meter = setup_global_meter(
            vertical_name=self.vertical_name,
        )

        # 2. Per-service TracerProviders + LoggerProviders
        service_dicts = [
            {
                "name": svc.name,
                "host": svc.host,
                "language": svc.language,
                "framework": svc.framework,
            }
            for svc in self.services.values()
        ]
        (
            self.tracers,
            self.service_loggers,
            self.shared_processor,
            self.tracer_providers,
            self.log_providers,
        ) = setup_per_service_providers(
            services=service_dicts,
            display_name=self.display_name,
        )

        # Build device fleet
        self.fleet: List[DeviceProfile] = []
        self._build_fleet()

        # OTel instruments (cached)
        self.instruments: Dict[str, Any] = {}
        self._create_instruments()
        self._create_service_instruments()

        # Plugin system
        self.plugins: List[IncidentPlugin] = []
        self.tick_count = 0
        self._shutdown = False

        # Shared incident state — plugins write, other subsystems (e.g. RUM) read
        # Keys are incident names, values are dicts like {"phase": "degraded", "severity": 0.7}
        self.incident_state: Dict[str, Dict[str, Any]] = {}

        # LLM Observability — OTel GenAI spans through the collector
        self.llm_obs = None
        try:
            from dd_demo_toolkit.simulator.llm_obs import LLMObsSubmitter
            otel_endpoint = os.environ.get(
                "OTEL_EXPORTER_OTLP_ENDPOINT", "otel-collector:4317"
            )
            self.llm_obs = LLMObsSubmitter(endpoint=otel_endpoint)
            logger.info("LLM Observability trace generation enabled (OTel GenAI)")
        except Exception as exc:
            logger.warning(f"LLM Observability init failed: {exc}")

        # RUM — custom metrics through the engine's shared meter
        self.rum = None
        try:
            from dd_demo_toolkit.simulator.rum import RUMSubmitter
            self.rum = RUMSubmitter(meter=self.meter)
            logger.info("RUM simulation enabled (shared OTel meter)")
        except Exception as exc:
            logger.warning(f"RUM simulation init failed: {exc}")

    # ------------------------------------------------------------------
    # Fleet & service builders
    # ------------------------------------------------------------------

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
                for _ in range(count):
                    location_idx = device_id_counter % len(locations)
                    location = locations[location_idx]

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

        logger.info(
            f"Built fleet with {len(self.fleet)} devices across "
            f"{len(self.services)} services"
        )

    def _build_services(self) -> None:
        """Build service catalog from config."""
        services_config = self.config.get("services", [])

        for service_config in services_config:
            name = service_config.get("name")
            language = service_config.get("language", "unknown")
            framework = service_config.get("framework", "")
            host = service_config.get("host", f"{name}-host-01")
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
                host=host,
                operations=operations,
                dependencies=dependencies,
                tags=tags,
            )
            self.services[name] = service

    # ------------------------------------------------------------------
    # OTel instrument creation
    # ------------------------------------------------------------------

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

                    if metric_name in self.instruments:
                        continue  # Already registered

                    if metric_type == "gauge":
                        self.instruments[metric_name] = self.meter.create_gauge(
                            name=metric_name, unit=unit, description=description,
                        )
                    elif metric_type == "counter":
                        self.instruments[metric_name] = self.meter.create_counter(
                            name=metric_name, unit=unit, description=description,
                        )
                    elif metric_type == "histogram":
                        self.instruments[metric_name] = self.meter.create_histogram(
                            name=metric_name, unit=unit, description=description,
                        )

    def _create_service_instruments(self) -> None:
        """Create OTel instruments for application-level service metrics.

        These counters emit ``{env_prefix}.app.requests_total`` and
        ``{env_prefix}.app.errors_total`` so that monitors, dashboards, and
        SLOs that reference those metrics are self-contained.
        """
        prefix = self.env_prefix
        self.instruments[f"{prefix}.app.requests_total"] = self.meter.create_counter(
            name=f"{prefix}.app.requests_total",
            unit="1",
            description="Total application requests per service",
        )
        self.instruments[f"{prefix}.app.errors_total"] = self.meter.create_counter(
            name=f"{prefix}.app.errors_total",
            unit="1",
            description="Total application errors per service",
        )
        self.instruments[f"{prefix}.app.latency_ms"] = self.meter.create_histogram(
            name=f"{prefix}.app.latency_ms",
            unit="ms",
            description="Application request latency",
        )

    # ------------------------------------------------------------------
    # Plugin system
    # ------------------------------------------------------------------

    def register_plugin(self, plugin: IncidentPlugin) -> None:
        """Register an incident plugin."""
        self.plugins.append(plugin)
        logger.info(f"Registered plugin: {plugin.get_incident_name()}")

    # ------------------------------------------------------------------
    # Tick loop
    # ------------------------------------------------------------------

    def tick(self) -> None:
        """
        Perform one simulation tick.

        - Calls registered plugins
        - Updates device metrics with drift and emits via OTel
        - Generates per-service traces with logs
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

        # Generate service traces (with logs and cross-service calls)
        for service_name in self.services:
            self._generate_service_trace(service_name)

        # LLM Observability traces (OTel GenAI via collector)
        if self.llm_obs:
            try:
                self.llm_obs.tick()
            except Exception as exc:
                logger.debug(f"LLM Obs tick error: {exc}")

        # RUM sessions (OTel metrics + native Datadog RUM intake)
        if self.rum:
            try:
                self.rum.tick(incident_state=self.incident_state)
            except Exception as exc:
                logger.debug(f"RUM tick error: {exc}")

        self.tick_count += 1

    # ------------------------------------------------------------------
    # Device metric generation
    # ------------------------------------------------------------------

    def _update_device(self, device: DeviceProfile) -> None:
        """Apply drift to device metrics."""
        for metric in device.metrics:
            current = device.state[metric.name]
            drift_amount = random.gauss(0, metric.drift)
            new_value = current + drift_amount
            new_value = max(metric.range[0], min(metric.range[1], new_value))
            device.state[metric.name] = new_value

    def _emit_device_metrics(self, device: DeviceProfile) -> None:
        """Emit device metrics via OTel."""
        for metric in device.metrics:
            value = device.state[metric.name]
            instrument = self.instruments.get(metric.name)
            if instrument is None:
                continue

            attributes = {
                "device_id": device.device_id,
                "device_type": device.type,
                "device_manufacturer": device.manufacturer,
                "device_model": device.model,
                "device_firmware": device.firmware,
                "category": device.category,
                "battery_powered": str(device.battery_powered),
            }
            attributes.update(device.location)

            if metric.type == "gauge":
                instrument.set(value, attributes=attributes)
            elif metric.type == "counter":
                instrument.add(max(0, value), attributes=attributes)
            elif metric.type == "histogram":
                instrument.record(value, attributes=attributes)

    # ------------------------------------------------------------------
    # Service trace & log generation (per-service providers)
    # ------------------------------------------------------------------

    def _generate_service_trace(self, service_name: str) -> None:
        """
        Generate a trace for a service operation using that service's own
        TracerProvider (so the span carries the correct service.name resource).

        Also emits structured logs via the service's LoggingHandler for
        automatic trace-log correlation.
        """
        service = self.services.get(service_name)
        if not service or not service.operations:
            return

        tracer = self.tracers.get(service_name)
        svc_log = self.service_loggers.get(service_name)
        if tracer is None:
            return

        # Pick a random operation
        operation = random.choice(service.operations)

        # Decide if this call errors
        should_error = random.random() < operation.error_rate

        # Calculate latency (p99 distribution simplified)
        if random.random() < 0.01:
            latency_ms = operation.latency_p99_ms
        else:
            latency_ms = max(
                operation.latency_base_ms,
                random.gauss(
                    operation.latency_base_ms,
                    operation.latency_base_ms * 0.1,
                ),
            )

        # Correlation / session IDs for log enrichment
        correlation_id = str(uuid.uuid4())
        session_id = str(uuid.uuid4())

        # Base log extra dict (matches original sensing-hospital pattern)
        log_extra = {
            "service.name": service_name,
            "host.name": service.host,
            "env": "demo",
            "demo.display_name": self.display_name,
            "operation.name": operation.name,
            "session.id": session_id,
            "correlation.id": correlation_id,
        }

        http_method = "GET" if operation.name.startswith("GET") else "POST"
        http_status = 500 if should_error else 200

        # --- Root span (service's own tracer) ---
        with tracer.start_as_current_span(
            operation.name,
            attributes={
                "operation.name": operation.name,
                "session.id": session_id,
                "correlation.id": correlation_id,
                "http.method": http_method,
                "http.status_code": http_status,
                "http.url": operation.name.split(" ", 1)[-1] if " " in operation.name else operation.name,
                "demo.display_name": self.display_name,
                "env": "demo",
            },
        ) as root_span:
            if should_error:
                root_span.set_status(Status(StatusCode.ERROR, "Internal error"))
                if svc_log:
                    svc_log.error(
                        "ERROR %s %s status=%d correlation_id=%s host=%s",
                        http_method, operation.name, http_status,
                        correlation_id[:8], service.host,
                        extra={**log_extra, "http.status_code": http_status, "error": True},
                    )
            else:
                root_span.set_status(Status(StatusCode.OK))
                if svc_log:
                    svc_log.info(
                        "Request received: %s session=%s correlation_id=%s host=%s",
                        operation.name, session_id[:8],
                        correlation_id[:8], service.host,
                        extra={**log_extra, "http.status_code": http_status},
                    )

            # --- Emit application-level custom metrics ---
            svc_attrs = {"service_name": service_name}
            prefix = self.env_prefix
            req_counter = self.instruments.get(f"{prefix}.app.requests_total")
            if req_counter:
                req_counter.add(1, attributes=svc_attrs)
            if should_error:
                err_counter = self.instruments.get(f"{prefix}.app.errors_total")
                if err_counter:
                    err_counter.add(1, attributes=svc_attrs)
            latency_hist = self.instruments.get(f"{prefix}.app.latency_ms")
            if latency_hist:
                latency_hist.record(latency_ms, attributes=svc_attrs)

            # --- Downstream spans within this service (db, cache) ---
            self._generate_downstream_spans(
                tracer, svc_log, service, operation, log_extra,
            )

            # --- Cross-service dependency calls ---
            for dependency in service.dependencies:
                if random.random() < dependency.probability:
                    self._generate_cross_service_span(
                        caller_service=service,
                        dependency=dependency,
                        correlation_id=correlation_id,
                    )

            # Completion log
            if svc_log and not should_error:
                svc_log.info(
                    "Request completed: %s latency=%.1fms correlation_id=%s",
                    operation.name, latency_ms, correlation_id[:8],
                    extra={**log_extra, "latency_ms": latency_ms},
                )

    def _generate_downstream_spans(
        self,
        tracer,
        svc_log,
        service: ServiceProfile,
        operation: ServiceOperation,
        log_extra: Dict[str, Any],
    ) -> None:
        """
        Generate downstream spans (db.query, cache.get) within the same
        service's tracer.  These show up as children of the root span and
        add realistic depth to traces.
        """
        templates = _DOWNSTREAM_TEMPLATES.get(service.language, [])
        for tmpl in templates:
            span_name = tmpl["name"]
            db_system = tmpl["system"]
            lat_min, lat_max = tmpl["latency_ms"]
            downstream_latency = random.uniform(lat_min, lat_max)

            with tracer.start_as_current_span(
                span_name,
                attributes={
                    "db.system": db_system,
                    "db.operation": span_name.split(".")[1] if "." in span_name else "query",
                    "db.statement": f"SELECT ... /* {operation.name} */",
                    "peer.service": db_system,
                },
            ):
                if svc_log:
                    svc_log.debug(
                        "%s %s latency=%.1fms",
                        span_name, db_system, downstream_latency,
                        extra={
                            **log_extra,
                            "db.system": db_system,
                            "db.latency_ms": downstream_latency,
                        },
                    )

    def _generate_cross_service_span(
        self,
        caller_service: ServiceProfile,
        dependency: ServiceDependency,
        correlation_id: str,
    ) -> None:
        """
        Generate a cross-service span using the TARGET service's tracer.

        This is the critical pattern for Datadog APM Service Map:
        the child span is created with the target service's TracerProvider,
        so it carries the target's service.name resource.  Because it shares
        the same trace_id (via OTel context propagation), Datadog draws an
        edge from caller → target on the Service Map.
        """
        target_tracer = self.tracers.get(dependency.service)
        target_log = self.service_loggers.get(dependency.service)
        target_service = self.services.get(dependency.service)

        if target_tracer is None:
            return

        target_host = target_service.host if target_service else "unknown"
        http_method = "POST" if "POST" in dependency.operation else "GET"

        with target_tracer.start_as_current_span(
            dependency.operation,
            attributes={
                "http.method": http_method,
                "http.url": dependency.operation.split(" ", 1)[-1] if " " in dependency.operation else dependency.operation,
                "peer.service": dependency.service,
                "span.kind": "client",
                "demo.display_name": self.display_name,
                "env": "demo",
            },
        ):
            if target_log:
                target_log.info(
                    "Inbound call from %s: %s correlation_id=%s host=%s",
                    caller_service.name, dependency.operation,
                    correlation_id[:8], target_host,
                    extra={
                        "service.name": dependency.service,
                        "host.name": target_host,
                        "env": "demo",
                        "demo.display_name": self.display_name,
                        "peer.service": caller_service.name,
                        "correlation.id": correlation_id,
                        "operation.name": dependency.operation,
                    },
                )

            # Target service may also hit its own DB
            if target_service:
                templates = _DOWNSTREAM_TEMPLATES.get(target_service.language, [])
                if templates:
                    tmpl = random.choice(templates)
                    lat_min, lat_max = tmpl["latency_ms"]
                    with target_tracer.start_as_current_span(
                        tmpl["name"],
                        attributes={
                            "db.system": tmpl["system"],
                            "db.operation": "query",
                            "peer.service": tmpl["system"],
                        },
                    ):
                        pass  # Span exists for trace depth

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run(self, interval_sec: float = 1.0) -> None:
        """
        Run the main simulator loop.

        Ticks at the specified interval with graceful shutdown on SIGINT.
        """
        def handle_shutdown(signum, frame):
            logger.info("Shutdown signal received, stopping simulator...")
            self._shutdown = True

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
                    logger.info(
                        f"Tick {self.tick_count} "
                        f"(elapsed: {self.tick_count * interval_sec:.1f}s)"
                    )

        except KeyboardInterrupt:
            logger.info("Interrupted by user")
        finally:
            self.shutdown()

    def shutdown(self) -> None:
        """Gracefully shutdown the simulator and flush all OTel providers."""
        if self.llm_obs:
            try:
                self.llm_obs.shutdown()
            except Exception:
                pass
        if self.rum:
            try:
                self.rum.shutdown()
            except Exception:
                pass
        shutdown_all(
            meter_provider=self.meter_provider,
            shared_processor=self.shared_processor,
            tracer_providers=self.tracer_providers,
            log_providers=self.log_providers,
        )
        logger.info(f"Simulator shutdown. Total ticks: {self.tick_count}")


@contextmanager
def simulator_context(config: Dict[str, Any], interval_sec: float = 1.0):
    """
    Context manager for running a simulator.

    Usage:
        config = load_vertical_config("healthcare")
        with simulator_context(config, interval_sec=1.0) as engine:
            engine.register_plugin(MyPlugin())

    Yields:
        SimulatorEngine instance.
    """
    engine = SimulatorEngine(config)
    try:
        yield engine
    finally:
        engine.shutdown()
