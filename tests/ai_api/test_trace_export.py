"""The export of the traces and of their log records: the exporters and their URLs, the filters of
their loggers, and a stop bounded when the endpoint does not answer; no network but a local
socket."""

import importlib.util
import logging
import socket
import threading
import time
from dataclasses import replace

import pytest
from opentelemetry.exporter.otlp.proto.http import Compression
from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.sdk.trace.sampling import ALWAYS_ON
from test_metrics_export import ENDPOINT, SETTINGS, TOKEN

from k_b.ai_api.connectors import trace_export
from k_b.ai_api.connectors.metrics_export import (
    QUOTING_FILTER,
    MetricsExportSettings,
    build_resource,
)
from k_b.ai_api.connectors.trace_export import EXPORTER_LOGGER_NAMES, TraceExport
from k_b.core.config import (
    MAX_TRACE_ATTRIBUTE_BYTES,
    TRACE_EXPORT_TIMEOUT_SECONDS,
    TRACE_QUEUE_SIZE,
)

# Short, so that the test of the stop lasts less than a second; the bound is the same multiple.
TEST_TIMEOUT_SECONDS = 0.3
# What a slow machine may add to a stop: the compression, the threads.
STOP_MARGIN_SECONDS = 0.5


@pytest.fixture
def built_exporters(monkeypatch):
    """The exporters of the traces as built, each by its signal and options; none sends."""
    built = []

    def record(signal, exporter):
        def build(**options):
            built.append((signal, options))
            return exporter()

        return build

    monkeypatch.setattr(
        trace_export, "OTLPSpanExporter", record("traces", InMemorySpanExporter)
    )
    monkeypatch.setattr(
        trace_export, "OTLPLogExporter", record("logs", InMemoryLogRecordExporter)
    )
    return built


@pytest.fixture
def silent_endpoint():
    """A local endpoint that takes each connection and never answers."""
    server = socket.create_server(("127.0.0.1", 0))
    server.settimeout(0.05)
    connections = []
    stopped = threading.Event()

    def accept():
        while not stopped.is_set():
            try:
                connections.append(server.accept()[0])
            except TimeoutError:
                continue

    thread = threading.Thread(target=accept, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.getsockname()[1]}"
    stopped.set()
    thread.join()
    for connection in connections:
        connection.close()
    server.close()


def test_without_settings_no_exporter_is_built(built_exporters):
    export = TraceExport.from_settings(None, build_resource(None))

    with export.build_tracing().step("read_watch"):
        pass
    export.stop_traces()
    export.stop_logs()

    assert built_exporters == []


@pytest.mark.parametrize(
    ("settings", "content"),
    [
        (None, "none"),
        (SETTINGS, "none"),
        (replace(SETTINGS, trace_content="anomalies"), "anomalies"),
        (replace(SETTINGS, trace_content="all"), "all"),
    ],
    ids=["no-export", "default", "anomalies", "all"],
)
def test_the_tracing_keeps_the_content_the_settings_give(
    built_exporters, settings, content
):
    export = TraceExport.from_settings(settings, build_resource(None))

    assert export.build_tracing().content == content


def test_each_exporter_gets_its_url_the_headers_and_a_session_blind_to_the_environment(
    built_exporters,
):
    export = TraceExport.from_settings(SETTINGS, build_resource("sandbox"))
    export.stop_traces()
    export.stop_logs()

    assert [signal for signal, _ in built_exporters] == ["traces", "logs"]
    (_, traces), (_, logs) = built_exporters
    assert (traces["endpoint"], logs["endpoint"]) == (
        f"{ENDPOINT}/v1/traces",
        f"{ENDPOINT}/v1/logs",
    )
    for options in (traces, logs):
        assert options["headers"] == {"Authorization": f"Basic {TOKEN}=="}
        assert options["timeout"] == TRACE_EXPORT_TIMEOUT_SECONDS
        assert options["compression"] is Compression.Gzip
        assert options["session"].trust_env is False


def test_both_providers_share_the_resource_keep_every_span_and_cut_each_attribute():
    resource = build_resource("sandbox")

    export = TraceExport.from_settings(None, resource)

    assert export.tracer_provider.resource is resource
    assert export.logger_provider.resource is resource
    assert export.tracer_provider.sampler is ALWAYS_ON
    assert (
        trace_export.SPAN_LIMITS.max_attribute_length
        == trace_export.LOG_RECORD_LIMITS.max_attribute_length
        == MAX_TRACE_ATTRIBUTE_BYTES
    )


def test_each_processor_waits_one_batch_at_most(built_exporters, monkeypatch):
    processors = []

    def record(processor):
        def build(exporter, **options):
            processors.append(options)
            return processor(exporter, **options)

        return build

    monkeypatch.setattr(
        trace_export, "BatchSpanProcessor", record(trace_export.BatchSpanProcessor)
    )
    monkeypatch.setattr(
        trace_export,
        "BatchLogRecordProcessor",
        record(trace_export.BatchLogRecordProcessor),
    )

    export = TraceExport.from_settings(SETTINGS, build_resource("sandbox"))
    export.stop_traces()
    export.stop_logs()

    # A stop sends the batch under way, then one more: within 2 × TRACE_EXPORT_TIMEOUT_SECONDS.
    assert (
        processors
        == [
            {
                "max_queue_size": TRACE_QUEUE_SIZE,
                "max_export_batch_size": TRACE_QUEUE_SIZE,
            }
        ]
        * 2
    )


def test_the_four_loggers_of_the_export_get_the_quoting_filter(built_exporters):
    TraceExport.from_settings(SETTINGS, build_resource("sandbox"))

    for name in EXPORTER_LOGGER_NAMES:
        # A module renamed by a version of the SDK would leave its logger without filter.
        assert importlib.util.find_spec(name) is not None, name
        assert QUOTING_FILTER in logging.getLogger(name).filters, name


def test_a_stop_against_an_endpoint_that_never_answers_is_bounded_and_quotes_nothing(
    monkeypatch, silent_endpoint, caplog
):
    monkeypatch.setattr(
        trace_export, "TRACE_EXPORT_TIMEOUT_SECONDS", TEST_TIMEOUT_SECONDS
    )
    export = TraceExport.from_settings(
        MetricsExportSettings(
            base_url=silent_endpoint, headers={}, environment="sandbox"
        ),
        build_resource("sandbox"),
    )
    with export.build_tracing().step("read_watch"):
        pass

    started = time.monotonic()
    with caplog.at_level(logging.WARNING):
        export.stop_traces()
    elapsed = time.monotonic() - started

    assert elapsed <= 2 * TEST_TIMEOUT_SECONDS + STOP_MARGIN_SECONDS
    lines = [
        record.getMessage()
        for record in caplog.records
        if record.name == "opentelemetry.exporter.otlp.proto.http.trace_exporter"
    ]
    assert lines
    assert not any("127.0.0.1" in line for line in lines), lines
    export.stop_logs()


class RaisingSpanExporter(InMemorySpanExporter):
    """An exporter whose error quotes a value, as the text of an exception of requests may."""

    def export(self, spans):
        raise ValueError("Mistral Large 4 (9 minute read)")


def test_an_exporter_that_raises_is_logged_by_the_type_of_its_error_never_its_text(
    monkeypatch, caplog
):
    monkeypatch.setattr(
        trace_export, "OTLPSpanExporter", lambda **options: RaisingSpanExporter()
    )
    monkeypatch.setattr(
        trace_export, "OTLPLogExporter", lambda **options: InMemoryLogRecordExporter()
    )
    export = TraceExport.from_settings(SETTINGS, build_resource("sandbox"))
    with export.build_tracing().step("read_watch"):
        pass

    with caplog.at_level(logging.ERROR):
        export.stop_traces()

    [record] = [
        record
        for record in caplog.records
        if record.name == "opentelemetry.sdk._shared_internal"
    ]
    assert record.getMessage() == "Exception while exporting 'Span'. (ValueError)"
    assert "Mistral" not in caplog.text
    assert record.exc_info is None
    export.stop_logs()


def test_a_stop_that_hangs_is_left_behind_after_its_bound_and_says_it(
    monkeypatch, caplog
):
    monkeypatch.setattr(trace_export, "TRACE_STOP_TIMEOUT_SECONDS", 0.2)
    export = TraceExport.from_settings(None, build_resource(None))
    # A shutdown slower than the bound, as an export whose socket trickles its answer.
    released = threading.Event()
    export.tracer_provider.shutdown = lambda: released.wait(5)

    started = time.monotonic()
    with caplog.at_level(logging.ERROR, logger=trace_export.logger.name):
        export.stop_traces()
    elapsed = time.monotonic() - started
    released.set()

    assert elapsed < 0.2 + STOP_MARGIN_SECONDS
    assert [record.getMessage() for record in caplog.records] == [
        "traces: stop not finished within 0.2 s"
    ]


def test_a_stop_that_fails_is_logged_by_its_type_only(caplog):
    export = TraceExport.from_settings(None, build_resource(None))

    def fail():
        raise RuntimeError("Mistral Large 4 (9 minute read)")

    export.tracer_provider.shutdown = fail

    with caplog.at_level(logging.ERROR, logger=trace_export.logger.name):
        export.stop_traces()

    assert [record.getMessage() for record in caplog.records] == [
        "traces: stop failed, RuntimeError"
    ]
