"""OpenTelemetry tracing configuration.

Enable tracing by setting OTEL_TRACING_ENABLED=true and running Jaeger:
    docker compose -f docker-compose.observability.yml up -d

View traces at: http://localhost:16686
"""

import logging
import os
from contextlib import contextmanager
from typing import Any, Generator

from fastapi import FastAPI

logger = logging.getLogger(__name__)

# Check if tracing is enabled
TRACING_ENABLED = os.getenv("OTEL_TRACING_ENABLED", "false").lower() == "true"
OTEL_EXPORTER_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "emotion-machine-backend")

# Lazy-loaded tracer
_tracer = None


def setup_tracing(app: FastAPI) -> None:
    """Initialize OpenTelemetry tracing if enabled.

    Call this in your FastAPI lifespan or startup.
    """
    if not TRACING_ENABLED:
        logger.info("Tracing disabled (set OTEL_TRACING_ENABLED=true to enable)")
        return

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.asyncpg import AsyncPGInstrumentor
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        from opentelemetry.instrumentation.logging import LoggingInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        # Create resource with service name
        resource = Resource.create({"service.name": SERVICE_NAME})

        # Create tracer provider
        provider = TracerProvider(resource=resource)

        # Add OTLP HTTP exporter (sends to Jaeger on port 4318)
        # Append /v1/traces for HTTP OTLP endpoint
        otlp_endpoint = OTEL_EXPORTER_ENDPOINT.rstrip("/")
        if not otlp_endpoint.endswith("/v1/traces"):
            otlp_endpoint = f"{otlp_endpoint}/v1/traces"
        otlp_exporter = OTLPSpanExporter(endpoint=otlp_endpoint)
        provider.add_span_processor(BatchSpanProcessor(otlp_exporter))

        # Set as global tracer provider
        trace.set_tracer_provider(provider)

        # Store tracer for manual instrumentation
        global _tracer
        _tracer = trace.get_tracer(__name__)

        # Auto-instrument FastAPI
        FastAPIInstrumentor.instrument_app(app)

        # Auto-instrument asyncpg (database queries)
        AsyncPGInstrumentor().instrument()

        # Auto-instrument httpx (external HTTP calls)
        HTTPXClientInstrumentor().instrument()

        # Add trace IDs to log records
        LoggingInstrumentor().instrument(set_logging_format=True)

        logger.info(
            "Tracing enabled: exporting to %s, service=%s",
            OTEL_EXPORTER_ENDPOINT,
            SERVICE_NAME,
        )

    except ImportError as e:
        logger.warning("OpenTelemetry packages not installed, tracing disabled: %s", e)
    except Exception as e:
        logger.error("Failed to initialize tracing: %s", e)


def get_tracer():
    """Get the tracer instance for manual instrumentation."""
    if _tracer is None:
        # Return a no-op tracer if tracing is disabled
        from opentelemetry import trace

        return trace.get_tracer(__name__)
    return _tracer


@contextmanager
def trace_span(name: str, attributes: dict[str, Any] | None = None) -> Generator:
    """Create a trace span for manual instrumentation.

    Usage:
        with trace_span("my_operation", {"key": "value"}):
            do_something()
    """
    tracer = get_tracer()
    with tracer.start_as_current_span(name) as span:
        if attributes:
            for key, value in attributes.items():
                span.set_attribute(key, value)
        yield span


def add_span_attributes(attributes: dict[str, Any]) -> None:
    """Add attributes to the current span.

    Usage:
        add_span_attributes({"user_id": user.id, "action": "create"})
    """
    from opentelemetry import trace

    span = trace.get_current_span()
    if span:
        for key, value in attributes.items():
            span.set_attribute(key, value)


def record_exception(exception: Exception) -> None:
    """Record an exception in the current span."""
    from opentelemetry import trace

    span = trace.get_current_span()
    if span:
        span.record_exception(exception)
        span.set_status(trace.Status(trace.StatusCode.ERROR, str(exception)))
