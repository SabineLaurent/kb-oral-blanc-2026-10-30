"""The traces of the AI API kept in memory, for the tests that read them."""

from dataclasses import dataclass, replace

import pytest
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import (
    InMemoryLogRecordExporter,
    SimpleLogRecordProcessor,
)
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from k_b.ai_api.connectors.trace_export import TraceExport


@dataclass(frozen=True)
class RecordedTraces:
    trace_export: TraceExport
    spans: InMemorySpanExporter
    log_records: InMemoryLogRecordExporter

    def find_spans(self, name):
        return [span for span in self.spans.get_finished_spans() if span.name == name]

    def build_tracing(self, content):
        """The tracing of these providers, the content of the requests kept as KB_TRACE_CONTENT
        would."""
        return replace(self.trace_export, content=content).build_tracing()

    def find_log_records(self, event_name):
        return [
            record.log_record
            for record in self.log_records.get_finished_logs()
            if record.log_record.event_name == event_name
        ]


@pytest.fixture
def recorded_traces():
    """Providers that keep each span and each log record once ended, at once."""
    spans = InMemorySpanExporter()
    log_records = InMemoryLogRecordExporter()
    tracer_provider = TracerProvider(shutdown_on_exit=False)
    tracer_provider.add_span_processor(SimpleSpanProcessor(spans))
    logger_provider = LoggerProvider(shutdown_on_exit=False)
    logger_provider.add_log_record_processor(SimpleLogRecordProcessor(log_records))
    return RecordedTraces(
        TraceExport(tracer_provider, logger_provider), spans, log_records
    )
