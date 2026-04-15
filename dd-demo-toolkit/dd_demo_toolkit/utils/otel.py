"""
OpenTelemetry helper for configuring metrics, traces, and logs exporters.
"""

import os
from typing import Tuple, Optional
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.metrics import get_meter
from opentelemetry.trace import get_tracer

# Logs SDK — handle both old and new import paths
try:
    from opentelemetry.sdk._logs import LoggerProvider
    from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
    from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
except ImportError:
    from opentelemetry.sdk.logs import LoggerProvider
    from opentelemetry.sdk.logs.export import BatchLogRecordProcessor
    from opentelemetry.exporter.otlp.proto.grpc.log_exporter import OTLPLogExporter


def setup_otel(
    endpoint: Optional[str] = None,
    insecure: bool = True,
) -> Tuple:
    """
    Set up OpenTelemetry providers and exporters for metrics, traces, and logs.

    Args:
        endpoint: OTLP gRPC endpoint. Defaults to localhost:4317 or OTEL_EXPORTER_OTLP_ENDPOINT env var.
        insecure: Whether to use insecure gRPC connection. Defaults to True.

    Returns:
        Tuple of (meter, tracer, log_emitter, meter_provider, tracer_provider, logger_provider)
    """
    endpoint = endpoint or os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "localhost:4317")

    # Metrics
    metric_exporter = OTLPMetricExporter(endpoint=endpoint, insecure=insecure)
    metric_reader = PeriodicExportingMetricReader(metric_exporter)
    meter_provider = MeterProvider(metric_readers=[metric_reader])
    meter = meter_provider.get_meter("dd-demo-toolkit")

    # Traces
    span_exporter = OTLPSpanExporter(endpoint=endpoint, insecure=insecure)
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(BatchSpanProcessor(span_exporter))
    tracer = tracer_provider.get_tracer("dd-demo-toolkit")

    # Logs
    log_exporter = OTLPLogExporter(endpoint=endpoint, insecure=insecure)
    logger_provider = LoggerProvider()
    logger_provider.add_log_record_processor(BatchLogRecordProcessor(log_exporter))
    log_emitter = logger_provider.get_logger("dd-demo-toolkit")

    return meter, tracer, log_emitter, meter_provider, tracer_provider, logger_provider


def shutdown_otel(meter_provider, tracer_provider, logger_provider) -> None:
    """
    Gracefully shut down all OTel providers.

    Args:
        meter_provider: MeterProvider instance.
        tracer_provider: TracerProvider instance.
        logger_provider: LoggerProvider instance.
    """
    meter_provider.force_flush()
    tracer_provider.force_flush()
    logger_provider.force_flush()
