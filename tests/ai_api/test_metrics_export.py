"""The export of the metrics in OpenTelemetry: its settings, refused without quoting the headers,
the meter provider, its closed resource, and the last export on stop; no network."""

import io
import logging
import math
import secrets
import uuid
from dataclasses import replace

import pytest
import requests
from opentelemetry.exporter.otlp.proto.http import Compression
from opentelemetry.sdk.metrics import Counter, Histogram, MeterProvider
from opentelemetry.sdk.metrics.export import (
    AggregationTemporality,
    InMemoryMetricReader,
    MetricExporter,
    MetricExportResult,
    MetricsData,
    PeriodicExportingMetricReader,
)
from pydantic import SecretStr

from k_b.ai_api.connectors import metrics_export
from k_b.ai_api.connectors.metrics_export import (
    EXPORTER_LOGGERS,
    MetricsExportSettings,
    build_meter_provider,
    build_resource,
    read_metrics_export_settings_or_refusal,
)
from k_b.ai_api.observability.metric_instruments import ModelMetrics
from k_b.core.config import METRICS_EXPORT_TIMEOUT_SECONDS

ENDPOINT = "https://otlp-gateway-prod-eu-west-2.grafana.net/otlp"
# Made for the test: never a real token. token_urlsafe gives the characters of base64url.
TOKEN = secrets.token_urlsafe(32)
# As Grafana Cloud gives it: the value percent-encoded, the space as %20.
HEADERS = f"Authorization=Basic%20{TOKEN}=="
EXPORT_ENVIRONMENT = {
    "KB_OTLP_ENDPOINT": ENDPOINT,
    "KB_OTLP_HEADERS": HEADERS,
    "KB_DEPLOYMENT_ENVIRONMENT": "sandbox",
}
SETTINGS = MetricsExportSettings(
    base_url=ENDPOINT,
    headers={"Authorization": SecretStr(f"Basic {TOKEN}==")},
    environment="sandbox",
)


class FakeExporter(MetricExporter):
    """An OTLP exporter that sends nothing: it keeps its arguments and what it is given."""

    def __init__(self, **options):
        super().__init__(preferred_temporality=options.get("preferred_temporality"))
        self.options = options
        self.exported: list[MetricsData] = []

    def export(self, metrics_data, timeout_millis=10_000, **kwargs):
        self.exported.append(metrics_data)
        return MetricExportResult.SUCCESS

    def force_flush(self, timeout_millis=10_000):
        return True

    def shutdown(self, timeout_millis=30_000, **kwargs):
        pass


@pytest.fixture
def fake_exporters(monkeypatch):
    exporters = []

    def build(**options):
        exporter = FakeExporter(**options)
        exporters.append(exporter)
        return exporter

    monkeypatch.setattr(metrics_export, "OTLPMetricExporter", build)
    return exporters


def test_without_endpoint_nor_headers_the_export_is_off():
    assert read_metrics_export_settings_or_refusal({}) is None
    assert read_metrics_export_settings_or_refusal({"KB_OTLP_ENDPOINT": ""}) is None


def test_without_export_the_environment_is_not_read():
    assert (
        read_metrics_export_settings_or_refusal(
            {"KB_DEPLOYMENT_ENVIRONMENT": "staging"}
        )
        is None
    )


@pytest.mark.parametrize("value", [None, "", "none"])
def test_the_content_of_the_requests_is_none_by_default(value):
    environ = EXPORT_ENVIRONMENT | (
        {} if value is None else {"KB_TRACE_CONTENT": value}
    )

    assert read_metrics_export_settings_or_refusal(environ) == SETTINGS


@pytest.mark.parametrize(
    ("environment", "content"),
    [("sandbox", "all"), ("sandbox", "anomalies"), ("production", "anomalies")],
)
def test_the_content_of_the_requests_is_read_with_the_export(environment, content):
    settings = read_metrics_export_settings_or_refusal(
        EXPORT_ENVIRONMENT
        | {"KB_DEPLOYMENT_ENVIRONMENT": environment, "KB_TRACE_CONTENT": content}
    )

    assert settings == replace(SETTINGS, environment=environment, trace_content=content)


def test_an_unknown_content_of_the_requests_without_export_is_refused_as_unknown():
    assert read_metrics_export_settings_or_refusal(
        {"KB_TRACE_CONTENT": "anomalie"}
    ) == ("KB_TRACE_CONTENT: 'anomalie' not none nor anomalies nor all")


@pytest.mark.parametrize("content", ["all", "anomalies"])
def test_the_content_of_the_requests_without_export_is_refused(content):
    assert read_metrics_export_settings_or_refusal({"KB_TRACE_CONTENT": content}) == (
        "KB_TRACE_CONTENT: set, while the export is off (KB_OTLP_ENDPOINT and"
        " KB_OTLP_HEADERS)"
    )


def test_the_content_of_the_requests_all_in_production_quotes_no_value():
    refusal = read_metrics_export_settings_or_refusal(
        EXPORT_ENVIRONMENT
        | {"KB_DEPLOYMENT_ENVIRONMENT": "production", "KB_TRACE_CONTENT": "all"}
    )

    assert "'" not in refusal
    assert ENDPOINT not in refusal
    assert TOKEN not in refusal


def test_a_variable_of_opentelemetry_is_refused_before_the_content_of_the_requests():
    refusal = read_metrics_export_settings_or_refusal(
        {"OTEL_SDK_DISABLED": "true", "KB_TRACE_CONTENT": "all"}
    )

    assert refusal.startswith("OTEL_SDK_DISABLED: read by the OpenTelemetry SDK")


def test_the_settings_give_the_base_and_the_headers_decoded():
    assert read_metrics_export_settings_or_refusal(EXPORT_ENVIRONMENT) == SETTINGS


def test_the_settings_give_the_path_of_each_signal():
    assert (SETTINGS.metrics_url, SETTINGS.traces_url, SETTINGS.logs_url) == (
        f"{ENDPOINT}/v1/metrics",
        f"{ENDPOINT}/v1/traces",
        f"{ENDPOINT}/v1/logs",
    )


def test_an_endpoint_ending_with_a_slash_gets_one_slash_before_the_path():
    settings = read_metrics_export_settings_or_refusal(
        EXPORT_ENVIRONMENT | {"KB_OTLP_ENDPOINT": f"{ENDPOINT}/"}
    )

    assert isinstance(settings, MetricsExportSettings)
    assert settings.metrics_url == f"{ENDPOINT}/v1/metrics"


def test_several_headers_are_read():
    settings = read_metrics_export_settings_or_refusal(
        EXPORT_ENVIRONMENT | {"KB_OTLP_HEADERS": f"x-honeycomb-team={TOKEN},x-a=b"}
    )

    assert isinstance(settings, MetricsExportSettings)
    assert {
        name: value.get_secret_value() for name, value in settings.headers.items()
    } == {"x-honeycomb-team": TOKEN, "x-a": "b"}


@pytest.mark.parametrize(
    ("variables", "message"),
    [
        (
            {"KB_OTLP_HEADERS": None},
            "KB_OTLP_HEADERS: not set, while KB_OTLP_ENDPOINT is",
        ),
        (
            {"KB_OTLP_ENDPOINT": None},
            "KB_OTLP_ENDPOINT: not set, while KB_OTLP_HEADERS is",
        ),
        (
            {"KB_OTLP_ENDPOINT": "http://otlp.example.net/otlp"},
            "KB_OTLP_ENDPOINT: scheme not https",
        ),
        (
            {"KB_OTLP_ENDPOINT": f"https://user:{TOKEN}@otlp.example.net"},
            "KB_OTLP_ENDPOINT: with user information",
        ),
        (
            {"KB_OTLP_ENDPOINT": f"https://otlp.example.net/otlp?key={TOKEN}"},
            "KB_OTLP_ENDPOINT: with a query or a fragment",
        ),
        (
            {"KB_OTLP_ENDPOINT": f"{ENDPOINT}/v1/metrics/"},
            "KB_OTLP_ENDPOINT: ends with /v1/metrics, give the base URL",
        ),
        (
            {"KB_OTLP_ENDPOINT": f"{ENDPOINT}/v1/metrics//"},
            "KB_OTLP_ENDPOINT: ends with /v1/metrics, give the base URL",
        ),
        (
            {"KB_OTLP_ENDPOINT": f"{ENDPOINT}/V1/Metrics"},
            "KB_OTLP_ENDPOINT: ends with /v1/metrics, give the base URL",
        ),
        (
            {"KB_OTLP_ENDPOINT": f"{ENDPOINT}/v1/traces"},
            "KB_OTLP_ENDPOINT: ends with /v1/traces, give the base URL",
        ),
        (
            {"KB_OTLP_ENDPOINT": f"{ENDPOINT}/v1/logs/"},
            "KB_OTLP_ENDPOINT: ends with /v1/logs, give the base URL",
        ),
        ({"KB_OTLP_HEADERS": f"Authorization{TOKEN}"}, "KB_OTLP_HEADERS: not"),
        ({"KB_OTLP_HEADERS": f"={TOKEN}"}, "KB_OTLP_HEADERS: not"),
        ({"KB_OTLP_HEADERS": "Authorization="}, "KB_OTLP_HEADERS: not"),
        ({"KB_OTLP_HEADERS": f"{HEADERS},"}, "KB_OTLP_HEADERS: not"),
        ({"KB_OTLP_HEADERS": f"Author ization={TOKEN}"}, "KB_OTLP_HEADERS: not"),
        ({"KB_OTLP_HEADERS": f"Authorization=Basic%0A{TOKEN}"}, "KB_OTLP_HEADERS: not"),
        (
            {"KB_OTLP_HEADERS": f"Authorization=Basic%20%20{TOKEN}"},
            "KB_OTLP_HEADERS: not",
        ),
        (
            {"KB_OTLP_HEADERS": f"Authorization=Basic%C3%A9{TOKEN}"},
            "KB_OTLP_HEADERS: not",
        ),
        ({"KB_OTLP_HEADERS": f"Authorization=Basic%FF{TOKEN}"}, "KB_OTLP_HEADERS: not"),
        (
            {"KB_DEPLOYMENT_ENVIRONMENT": None},
            "KB_DEPLOYMENT_ENVIRONMENT: not set, sandbox or production",
        ),
        (
            {"KB_DEPLOYMENT_ENVIRONMENT": "staging"},
            "KB_DEPLOYMENT_ENVIRONMENT: 'staging' not sandbox nor production",
        ),
        (
            {"KB_TRACE_CONTENT": "everything"},
            "KB_TRACE_CONTENT: 'everything' not none nor anomalies nor all",
        ),
        (
            {"KB_TRACE_CONTENT": "all", "KB_DEPLOYMENT_ENVIRONMENT": "production"},
            "KB_TRACE_CONTENT: all refused in production, anomalies or none",
        ),
    ],
    ids=[
        "no-headers",
        "no-endpoint",
        "http",
        "user",
        "query",
        "metrics-path",
        "metrics-path-two-slashes",
        "metrics-path-case",
        "traces-path",
        "logs-path",
        "no-equal",
        "no-name",
        "no-value",
        "empty-pair",
        "space-in-name",
        "line-feed",
        "two-spaces",
        "not-ascii",
        "not-utf-8",
        "no-environment",
        "staging",
        "content-unknown",
        "content-all-in-production",
    ],
)
def test_a_setting_of_the_export_refused_is_named_without_its_headers(
    variables, message
):
    environ = {
        name: value
        for name, value in (EXPORT_ENVIRONMENT | variables).items()
        if value is not None
    }

    refusal = read_metrics_export_settings_or_refusal(environ)

    assert isinstance(refusal, str)
    assert refusal.startswith(message)
    assert TOKEN not in refusal


def test_any_variable_of_opentelemetry_is_refused_by_its_name_only():
    refusal = read_metrics_export_settings_or_refusal(
        {"OTEL_SDK_DISABLED": "true", "OTEL_EXPORTER_OTLP_HEADERS": HEADERS}
    )

    assert refusal == (
        "OTEL_EXPORTER_OTLP_HEADERS, OTEL_SDK_DISABLED: read by the OpenTelemetry SDK"
        " behind the back of K-B, unset them (KB_OTLP_ENDPOINT and KB_OTLP_HEADERS set"
        " the export)"
    )


def test_without_settings_no_exporter_is_built(fake_exporters):
    model_metrics = ModelMetrics(build_meter_provider(None, build_resource(None)))

    model_metrics.record_data_fetch(120)
    model_metrics.stop()

    assert fake_exporters == []


def test_the_exporter_gets_the_settings_and_a_session_blind_to_the_environment(
    fake_exporters,
):
    build_meter_provider(SETTINGS, build_resource("sandbox")).shutdown()

    [exporter] = fake_exporters
    options = exporter.options
    assert options["endpoint"] == f"{ENDPOINT}/v1/metrics"
    assert options["headers"] == {"Authorization": f"Basic {TOKEN}=="}
    assert options["timeout"] == METRICS_EXPORT_TIMEOUT_SECONDS
    assert options["compression"] is Compression.Gzip
    assert options["preferred_temporality"] == {
        Counter: AggregationTemporality.CUMULATIVE,
        Histogram: AggregationTemporality.CUMULATIVE,
    }
    # Neither a proxy, a certificate nor a .netrc of the environment (core/outbound.py).
    assert options["session"].trust_env is False


def test_the_resource_names_the_service_and_the_environment_only(
    monkeypatch, fake_exporters
):
    # Read by Resource.create, never by K-B: the start refuses them, the resource ignores them.
    monkeypatch.setenv("OTEL_RESOURCE_ATTRIBUTES", "host.name=mac-of-the-user")
    monkeypatch.setenv("OTEL_SERVICE_NAME", "other")
    monkeypatch.setenv("OTEL_EXPERIMENTAL_RESOURCE_DETECTORS", "process,os")
    model_metrics = ModelMetrics(
        build_meter_provider(SETTINGS, build_resource("sandbox"))
    )

    model_metrics.record_data_fetch(120)
    model_metrics.stop()

    [exporter] = fake_exporters
    [metrics_data] = exporter.exported
    [resource_metrics] = metrics_data.resource_metrics
    attributes = dict(resource_metrics.resource.attributes)
    instance_id = attributes.pop("service.instance.id")
    assert attributes == {
        "service.name": "k-b-ai-api",
        "deployment.environment.name": "sandbox",
    }
    assert uuid.UUID(instance_id).version == 4


def test_each_build_draws_its_own_instance():
    first, second = build_resource("sandbox"), build_resource("sandbox")

    assert (
        first.attributes["service.instance.id"]
        != second.attributes["service.instance.id"]
    )


def test_without_export_the_resource_names_the_service_and_its_instance_only():
    assert set(build_resource(None).attributes) == {
        "service.name",
        "service.instance.id",
    }


def test_stop_sends_the_measures_not_sent_yet(fake_exporters):
    model_metrics = ModelMetrics(
        build_meter_provider(SETTINGS, build_resource("sandbox"))
    )
    model_metrics.record_data_fetch(1_500)

    model_metrics.stop()

    [exporter] = fake_exporters
    [metrics_data] = exporter.exported
    [resource_metrics] = metrics_data.resource_metrics
    [scope_metrics] = resource_metrics.scope_metrics
    [metric] = scope_metrics.metrics
    assert metric.name == "k_b.digest.data_fetch.duration"
    [point] = metric.data.data_points
    assert (point.count, point.sum) == (1, 1.5)


class RefusingAdapter(requests.adapters.BaseAdapter):
    """An HTTP adapter that answers every request 400 with a reason phrase holding a CR and an
    escape character; nothing leaves the test."""

    def send(self, request, **kwargs):
        response = requests.Response()
        response.status_code = 400
        response.reason = "Bad\rRequest\x1b[31m"
        response.url = request.url
        response.request = request
        response.raw = io.BytesIO(b"")
        return response

    def close(self):
        pass


def test_a_failed_export_is_logged_with_its_reason_quoted(monkeypatch, caplog):
    class LocalSession(requests.Session):
        def __init__(self):
            super().__init__()
            self.mount("https://", RefusingAdapter())

    monkeypatch.setattr(metrics_export.requests, "Session", LocalSession)
    reader = InMemoryMetricReader()
    model_metrics = ModelMetrics(MeterProvider(metric_readers=[reader]))
    model_metrics.record_data_fetch(120)
    exporter = metrics_export.build_exporter(SETTINGS)

    with caplog.at_level(logging.ERROR):
        result = exporter.export(reader.get_metrics_data())

    assert result is MetricExportResult.FAILURE
    [record] = [
        record for record in caplog.records if record.name == EXPORTER_LOGGERS[0].name
    ]
    message = record.getMessage()
    assert "'Bad\\rRequest\\x1b[31m'" in message
    assert "\r" not in message
    assert "\x1b" not in message
    assert TOKEN not in message


class RaisingExporter(FakeExporter):
    """An exporter whose export raises, with a CR in the message of its error."""

    def export(self, metrics_data, timeout_millis=10_000, **kwargs):
        raise RuntimeError(f"refused\r{TOKEN}")


def test_an_error_of_the_reader_is_logged_by_its_type_without_its_traceback(caplog):
    metrics_export.build_exporter(SETTINGS)  # sets the filters, sends nothing
    reader = PeriodicExportingMetricReader(
        RaisingExporter(), export_interval_millis=math.inf
    )
    model_metrics = ModelMetrics(MeterProvider(metric_readers=[reader]))
    model_metrics.record_data_fetch(120)

    # Without a thread (an infinite interval), force_flush collects and exports at once.
    with caplog.at_level(logging.ERROR):
        reader.force_flush()

    [record] = [
        record for record in caplog.records if record.name == EXPORTER_LOGGERS[1].name
    ]
    assert record.getMessage() == "Exception while exporting metrics (RuntimeError)"
    assert record.exc_info is None
    assert TOKEN not in caplog.text
    assert "\r" not in caplog.text


def test_a_mapping_of_arguments_is_quoted():
    record = logging.LogRecord(
        "name",
        logging.ERROR,
        "path",
        1,
        "reason %(reason)s",
        ({"reason": "a\rb"},),
        None,
    )

    metrics_export.QUOTING_FILTER.filter(record)

    assert record.getMessage() == "reason 'a\\rb'"
