"""The export of the traces of the AI API and of their log records, to the OTLP endpoint of the
metrics: the two providers and their exporters; without settings, nothing leaves.

The one module of K-B that imports the private modules of the SDK and of its exporter (the log
records), which move from one version to the next: pyproject.toml bounds them."""

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass

from opentelemetry.exporter.otlp.proto.common._internal import _encode_attributes
from opentelemetry.exporter.otlp.proto.http import Compression
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk._logs import LoggerProvider, LogRecordLimits
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk._shared_internal import BatchProcessor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import SpanLimits, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import ALWAYS_ON

from k_b.ai_api.connectors.metrics_export import (
    MetricsExportSettings,
    add_quoting_filter,
    build_session,
)
from k_b.ai_api.tracing import SCOPE_NAME, Tracing
from k_b.core.config import (
    MAX_TRACE_ATTRIBUTE_BYTES,
    TRACE_EXPORT_TIMEOUT_SECONDS,
    TRACE_QUEUE_SIZE,
    TRACE_STOP_TIMEOUT_SECONDS,
    TraceContent,
)

logger = logging.getLogger(__name__)

# The loggers whose lines may quote what the endpoint or an exception says: the batch processor
# of the spans and of the log records, the two exporters (each gets the lines of its retry loop
# too) and the encoder of the attributes. Each by the module of what logs, as each logs under the
# name of its module: a filter on a parent logger does not see the lines of its children, and a
# module moved by a version of the SDK moves its name here.
EXPORTER_LOGGER_NAMES = (
    BatchProcessor.__module__,
    OTLPSpanExporter.__module__,
    OTLPLogExporter.__module__,
    _encode_attributes.__module__,
)
SPAN_LIMITS = SpanLimits(max_attribute_length=MAX_TRACE_ATTRIBUTE_BYTES)
LOG_RECORD_LIMITS = LogRecordLimits(max_attribute_length=MAX_TRACE_ATTRIBUTE_BYTES)


@dataclass(frozen=True)
class TraceExport:
    """The providers of the spans and of the log records of kb serve-ai."""

    tracer_provider: TracerProvider
    logger_provider: LoggerProvider
    # KB_TRACE_CONTENT, none without the export (connectors/metrics_export.py).
    content: TraceContent = "none"

    @classmethod
    def from_settings(
        cls, settings: MetricsExportSettings | None, resource: Resource
    ) -> "TraceExport":
        """Exported if the settings are given, dropped if not; the caller stops them (no atexit,
        which would log once the handlers of kb are removed)."""
        # Every span kept: a request makes a few dozen, and the one that fails is the one to read.
        tracer_provider = TracerProvider(
            sampler=ALWAYS_ON,
            resource=resource,
            shutdown_on_exit=False,
            span_limits=SPAN_LIMITS,
        )
        logger_provider = LoggerProvider(
            resource=resource,
            shutdown_on_exit=False,
            log_record_limits=LOG_RECORD_LIMITS,
        )
        if settings is not None:
            add_quoting_filter(
                logging.getLogger(name) for name in EXPORTER_LOGGER_NAMES
            )
            tracer_provider.add_span_processor(
                BatchSpanProcessor(
                    build_span_exporter(settings),
                    max_queue_size=TRACE_QUEUE_SIZE,
                    max_export_batch_size=TRACE_QUEUE_SIZE,
                )
            )
            logger_provider.add_log_record_processor(
                BatchLogRecordProcessor(
                    build_log_exporter(settings),
                    max_queue_size=TRACE_QUEUE_SIZE,
                    max_export_batch_size=TRACE_QUEUE_SIZE,
                )
            )
        return cls(
            tracer_provider,
            logger_provider,
            "none" if settings is None else settings.trace_content,
        )

    def build_tracing(self) -> Tracing:
        return Tracing(
            self.tracer_provider.get_tracer(SCOPE_NAME),
            self.logger_provider.get_logger(SCOPE_NAME),
            self.content,
        )

    def stop_traces(self) -> None:
        """Send the spans not sent yet: the export under way, then the rest, one batch, within
        TRACE_STOP_TIMEOUT_SECONDS."""
        stop("traces", self.tracer_provider.shutdown)

    def stop_logs(self) -> None:
        """Send the log records not sent yet, as stop_traces."""
        stop("logs", self.logger_provider.shutdown)


def build_span_exporter(settings: MetricsExportSettings) -> OTLPSpanExporter:
    return OTLPSpanExporter(
        endpoint=settings.traces_url,
        headers=settings.reveal_headers(),
        timeout=TRACE_EXPORT_TIMEOUT_SECONDS,
        compression=Compression.Gzip,
        session=build_session(),
    )


def build_log_exporter(settings: MetricsExportSettings) -> OTLPLogExporter:
    return OTLPLogExporter(
        endpoint=settings.logs_url,
        headers=settings.reveal_headers(),
        timeout=TRACE_EXPORT_TIMEOUT_SECONDS,
        compression=Compression.Gzip,
        session=build_session(),
    )


def stop(signal: str, shutdown: Callable[[], None]) -> None:
    """Run shutdown in a thread of its own, waited TRACE_STOP_TIMEOUT_SECONDS at most, then left
    behind: a daemon, it ends with the process. A failure is logged by its type only, as the SDK
    builds its message from its exporters."""
    errors: list[Exception] = []

    def run() -> None:
        try:
            shutdown()
        except Exception as error:  # noqa: BLE001 — the SDK may raise a bare Exception
            errors.append(error)

    thread = threading.Thread(target=run, name=f"stop-{signal}", daemon=True)
    thread.start()
    thread.join(TRACE_STOP_TIMEOUT_SECONDS)
    if thread.is_alive():
        logger.error(
            "%s: stop not finished within %s s", signal, TRACE_STOP_TIMEOUT_SECONDS
        )
    elif errors:
        logger.error("%s: stop failed, %s", signal, type(errors[0]).__name__)
