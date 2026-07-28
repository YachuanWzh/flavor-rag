"""OpenTelemetry tracing — optional, fails open to no-op when unavailable.

The RAG pipeline already records every node through ``TraceLogger`` with
explicit start/end timestamps. This module mirrors those records into OTel
spans (created retrospectively with explicit timestamps), so the existing
trace persistence keeps working while Jaeger/Tempo receive standard spans.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.config.logging_config import get_logger
from app.config.settings import settings

_log = get_logger("flavorag.otel")
_tracer = None
_initialized = False
_provider = None


def _to_ns(moment: datetime) -> int:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return int(moment.timestamp() * 1_000_000_000)


def setup_otel(app=None) -> bool:
    """Initialize the OTLP tracer provider and FastAPI instrumentation."""
    global _tracer, _initialized, _provider
    if _initialized:
        return _tracer is not None
    _initialized = True
    if not settings.otel_enabled:
        return False
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        _log.warning(
            "otel_dependencies_missing",
            hint="pip install flavorag-server[otel]",
        )
        return False

    provider = TracerProvider(
        resource=Resource.create({"service.name": settings.otel_service_name})
    )
    provider.add_span_processor(
        BatchSpanProcessor(
            OTLPSpanExporter(
                endpoint=f"{settings.otel_exporter_otlp_endpoint.rstrip('/')}/v1/traces"
            )
        )
    )
    trace.set_tracer_provider(provider)
    _provider = provider
    _tracer = trace.get_tracer("flavorag")

    if app is not None:
        try:
            from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

            FastAPIInstrumentor.instrument_app(app, tracer_provider=provider)
        except ImportError:
            _log.warning("otel_fastapi_instrumentation_missing")
    _log.info(
        "otel_initialized",
        endpoint=settings.otel_exporter_otlp_endpoint,
        service=settings.otel_service_name,
    )
    return True


def shutdown_otel() -> None:
    """Flush and close the batch exporter during application shutdown."""
    global _provider
    if _provider is not None:
        _provider.force_flush(timeout_millis=5000)
        _provider.shutdown()
        _provider = None


def otel_active() -> bool:
    return _tracer is not None


def start_rag_span(name: str, attributes: dict | None = None):
    """Start a long-lived root span for one RAG run; caller must end it."""
    if _tracer is None:
        return None
    return _tracer.start_span(name, attributes=attributes or {})


def end_rag_span(span, *, attributes: dict | None = None, error: str | None = None):
    if span is None:
        return
    from opentelemetry.trace import Status, StatusCode

    for key, value in (attributes or {}).items():
        if value is not None:
            span.set_attribute(key, value)
    if error:
        span.set_status(Status(StatusCode.ERROR, error[:200]))
    span.end()


def record_child_span(
    parent,
    name: str,
    start_time: datetime,
    end_time: datetime,
    *,
    attributes: dict | None = None,
    error: str | None = None,
) -> None:
    """Record an already-finished pipeline node as an OTel child span."""
    if _tracer is None or parent is None:
        return
    from opentelemetry import trace as trace_api
    from opentelemetry.trace import Status, StatusCode

    context = trace_api.set_span_in_context(parent)
    span = _tracer.start_span(name, context=context, start_time=_to_ns(start_time))
    for key, value in (attributes or {}).items():
        if value is not None:
            span.set_attribute(key, value)
    if error:
        span.set_status(Status(StatusCode.ERROR, error[:200]))
    span.end(end_time=_to_ns(end_time))
