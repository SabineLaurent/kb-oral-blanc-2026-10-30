"""The native OpenTelemetry of FastAPI, off in both APIs.

On, it would send to the global providers traces that carry the raw query of a request, to a
recipient the register does not name; the measures of K-B go by ai_api/observability/metric_instruments.py."""

from fastapi.telemetry import TelemetryConfig

NATIVE_TELEMETRY_OFF: TelemetryConfig = {
    "tracing": False,
    "metrics": False,
    "logs": False,
    "operation_spans": False,
    "auto_configure": False,
}
