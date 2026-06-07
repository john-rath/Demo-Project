"""
OpenTelemetry helper for configuring metrics, traces, and logs exporters.

Follows the per-service provider pattern from the original sensing-hospital
demo: each application service gets its own TracerProvider and LoggerProvider
with a Resource that carries service.name, host.name, deployment.environment.name.
This enables:
  - Datadog APM Service Map (each span has the correct service resource)
  - Trace ↔ Log correlation (LoggingHandler auto-injects trace_id/span_id)
  - Host-level views (each service appears on its virtual host)

IoT device metrics use a single global MeterProvider since they represent
infrastructure, not application code.
"""

import logging
import os
from typing import Dict, List, Optional, Tuple

from opentelemetry.sdk.resources import Resource
from opentelemetry.semconv.resource import ResourceAttributes

from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

# Logs SDK — handle both old and new import paths
try:
    from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
    from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
    from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
except ImportError:
    from opentelemetry.sdk.logs import LoggerProvider, LoggingHandler
    from opentelemetry.sdk.logs.export import BatchLogRecordProcessor
    from opentelemetry.exporter.otlp.proto.grpc.log_exporter import OTLPLogExporter


logger = logging.getLogger(__name__)


def setup_global_meter(
    endpoint: Optional[str] = None,
    insecure: bool = True,
    vertical_name: str = "dd-demo-toolkit",
) -> Tuple[MeterProvider, "Meter"]:
    """
    Set up the global MeterProvider for IoT device metrics.

    Device metrics don't carry a service.name resource — they represent
    infrastructure. The resource identifies the simulator itself.

    Returns:
        Tuple of (meter_provider, meter)
    """
    endpoint = endpoint or os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "localhost:4317")

    resource = Resource.create({
        ResourceAttributes.SERVICE_NAME: f"{vertical_name}-simulator",
        ResourceAttributes.SERVICE_VERSION: "1.0.0",
        "deployment.environment.name": "demo",
        "team": f"dd-demo-{vertical_name}",
    })

    metric_exporter = OTLPMetricExporter(endpoint=endpoint, insecure=insecure)
    metric_reader = PeriodicExportingMetricReader(
        metric_exporter,
        export_interval_millis=15_000,
    )
    meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
    meter = meter_provider.get_meter("dd-demo-toolkit")

    return meter_provider, meter


def setup_per_service_providers(
    services: List[Dict],
    endpoint: Optional[str] = None,
    insecure: bool = True,
    display_name: str = "Demo",
    vertical_name: str = "dd-demo-toolkit",
) -> Tuple[
    Dict[str, "Tracer"],
    Dict[str, logging.Logger],
    "BatchSpanProcessor",
    List[TracerProvider],
    List[LoggerProvider],
]:
    """
    Create per-service TracerProvider and LoggerProvider instances.

    IMPORTANT: All providers share ONE exporter and ONE BatchSpanProcessor.
    Multiple gRPC connections (one per service) cause most connections to
    silently fail — only the last service would appear in APM. A shared
    exporter uses a single gRPC channel and correctly groups spans by
    their TracerProvider resource on export.

    Args:
        services: List of service config dicts, each with 'name', 'host',
                  'language', 'framework' fields.
        endpoint: OTLP gRPC endpoint.
        insecure: Whether to use insecure gRPC.
        display_name: Vertical display name for demo.display_name attribute.

    Returns:
        Tuple of:
        - tracers: Dict mapping service name → Tracer
        - service_loggers: Dict mapping service name → Python Logger (with OTel handler)
        - shared_processor: The BatchSpanProcessor (needed for shutdown)
        - tracer_providers: List of all TracerProviders (needed for shutdown)
        - log_providers: List of all LoggerProviders (needed for shutdown)
    """
    endpoint = endpoint or os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "localhost:4317")

    # Single shared exporter + processor (one gRPC connection).
    # Large queue to handle spans from all services + cross-service calls.
    shared_exporter = OTLPSpanExporter(endpoint=endpoint, insecure=insecure)
    shared_processor = BatchSpanProcessor(
        shared_exporter,
        max_queue_size=8192,
        max_export_batch_size=512,
        schedule_delay_millis=1000,
    )

    tracers: Dict[str, object] = {}
    service_loggers: Dict[str, logging.Logger] = {}
    tracer_providers: List[TracerProvider] = []
    log_providers: List[LoggerProvider] = []

    for svc in services:
        svc_name = svc["name"]
        svc_host = svc.get("host", f"{svc_name}-host-01")

        # --- TracerProvider with per-service Resource ---
        resource = Resource.create({
            ResourceAttributes.SERVICE_NAME: svc_name,
            ResourceAttributes.SERVICE_VERSION: "1.0.0",
            "deployment.environment.name": "demo",
            "host.name": svc_host,
            "service.language": svc.get("language", "unknown"),
            "service.framework": svc.get("framework", "unknown"),
            "demo.display_name": display_name,
            "team": f"dd-demo-{vertical_name}",
        })
        tp = TracerProvider(resource=resource)
        tp.add_span_processor(shared_processor)
        tracer_providers.append(tp)
        tracers[svc_name] = tp.get_tracer(svc_name, "1.0.0")

        # --- LoggerProvider with same Resource (for trace-log correlation) ---
        log_provider = LoggerProvider(resource=resource)
        log_exporter = OTLPLogExporter(endpoint=endpoint, insecure=insecure)
        log_provider.add_log_record_processor(BatchLogRecordProcessor(log_exporter))
        log_providers.append(log_provider)

        # Create a dedicated Python logger per service.
        # The LoggingHandler automatically injects trace_id/span_id from
        # the active span context, enabling Datadog trace ↔ log correlation.
        # Use a sanitized display_name as the logger namespace so logs
        # reflect the active vertical (e.g. "hospitality.service.reservations-portal")
        logger_ns = display_name.lower().replace(" ", "-")
        svc_logger = logging.getLogger(f"{logger_ns}.service.{svc_name}")
        svc_logger.setLevel(logging.DEBUG)
        handler = LoggingHandler(level=logging.DEBUG, logger_provider=log_provider)
        svc_logger.addHandler(handler)
        service_loggers[svc_name] = svc_logger

        logger.info(f"  Tracer + Logger created for service: {svc_name} (host: {svc_host})")

    return tracers, service_loggers, shared_processor, tracer_providers, log_providers


def shutdown_all(
    meter_provider: MeterProvider,
    shared_processor: Optional[BatchSpanProcessor],
    tracer_providers: List[TracerProvider],
    log_providers: List[LoggerProvider],
) -> None:
    """Gracefully shut down all OTel providers."""
    meter_provider.force_flush()
    if shared_processor:
        shared_processor.force_flush()
    for tp in tracer_providers:
        tp.force_flush()
    for lp in log_providers:
        lp.force_flush()
