"""OpenTelemetry bootstrap.

Telemetry is opt-in: :func:`init_telemetry` is a no-op unless
``settings.otel_enabled`` is true *and* an OTLP endpoint is configured. This
keeps services importable and runnable on a laptop / in CI with no collector.
When enabled it wires OTLP tracer and meter providers. :func:`get_tracer`
always returns a usable tracer (the API's no-op tracer when uninitialized), so
call sites never need to guard on whether telemetry is on.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from opentelemetry import trace

if TYPE_CHECKING:  # avoid import cycle / heavy import at module load
    from edis_platform.settings import Settings

_INITIALIZED = False


def init_telemetry(settings: "Settings") -> None:
    """Configure OTLP tracer + meter providers; no-op when disabled.

    Best-effort: any failure wiring the SDK is swallowed so telemetry can never
    take down a service.
    """

    global _INITIALIZED
    if _INITIALIZED:
        return
    if not settings.otel_enabled or not settings.otel_exporter_otlp_endpoint:
        return

    try:
        from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import (
            OTLPMetricExporter,
        )
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
        from opentelemetry.sdk.resources import SERVICE_NAME, Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry import metrics

        resource = Resource.create(
            {
                SERVICE_NAME: settings.service_name,
                "deployment.environment": settings.environment,
            }
        )
        endpoint = settings.otel_exporter_otlp_endpoint

        tracer_provider = TracerProvider(resource=resource)
        tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
        trace.set_tracer_provider(tracer_provider)

        reader = PeriodicExportingMetricReader(OTLPMetricExporter(endpoint=endpoint))
        meter_provider = MeterProvider(resource=resource, metric_readers=[reader])
        metrics.set_meter_provider(meter_provider)

        _INITIALIZED = True
    except Exception:  # pragma: no cover - defensive: telemetry never blocks startup
        return


def get_tracer(name: str):
    """Return a tracer; the API no-op tracer when telemetry is uninitialized."""

    return trace.get_tracer(name)


def instrument_fastapi(app) -> None:
    """Best-effort FastAPI auto-instrumentation; silently no-ops on failure."""

    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app)
    except Exception:  # pragma: no cover - optional instrumentation dependency
        return
