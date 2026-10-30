"""The export of the metrics of the AI API in OpenTelemetry to an OTLP endpoint: its settings, shared
with the traces (connectors/trace_export.py), and the meter provider; without endpoint and headers,
nothing leaves.

No OTEL_* variable is accepted: the SDK reads the environment behind its caller, the headers too
even when given (_resolve_headers, 1.45.1), and logs a malformed one as it is."""

import logging
import re
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Literal
from urllib.parse import unquote

import requests
from opentelemetry.exporter.otlp.proto.http import Compression
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.sdk.metrics import Counter, Histogram, MeterProvider
from opentelemetry.sdk.metrics.export import (
    AggregationTemporality,
    PeriodicExportingMetricReader,
)
from opentelemetry.sdk.resources import Resource
from pydantic import SecretStr

from k_b.core.config import (
    METRICS_EXPORT_INTERVAL_SECONDS,
    METRICS_EXPORT_TIMEOUT_SECONDS,
    MILLISECONDS_PER_SECOND,
    TRACE_CONTENTS,
    TraceContent,
)
from k_b.core.outbound import find_url_refusal
from k_b.core.quoting import quote_value

# The logs of the exporter and of the reader that calls it: their failures quote the reason phrase
# of the endpoint, the text of an exception or its traceback, which may hold a CR or a control
# character. By the module of each class: the reader lives in a private module of the SDK.
EXPORTER_LOGGERS = (
    logging.getLogger(OTLPMetricExporter.__module__),
    logging.getLogger(PeriodicExportingMetricReader.__module__),
)

ENDPOINT_VARIABLE = "KB_OTLP_ENDPOINT"
HEADERS_VARIABLE = "KB_OTLP_HEADERS"
ENVIRONMENT_VARIABLE = "KB_DEPLOYMENT_ENVIRONMENT"
TRACE_CONTENT_VARIABLE = "KB_TRACE_CONTENT"
OPENTELEMETRY_VARIABLE_PREFIX = "OTEL_"
# The paths K-B adds to the base endpoint, one per signal.
METRICS_PATH = "/v1/metrics"
TRACES_PATH = "/v1/traces"
LOGS_PATH = "/v1/logs"
SIGNAL_PATHS = (METRICS_PATH, TRACES_PATH, LOGS_PATH)
SERVICE_NAME = "k-b-ai-api"
type DeploymentEnvironment = Literal["sandbox", "production"]
DEPLOYMENT_ENVIRONMENTS: tuple[DeploymentEnvironment, ...] = ("sandbox", "production")
# A header name: a token of RFC 9110 (section 5.6.2).
HEADER_NAME = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+")
# A header value, once percent-decoded: words of printable ASCII, one space between two. Stricter
# than the parser of the SDK and than the check of requests, which would quote the value refused.
HEADER_VALUE = re.compile(r"[!-~]+( [!-~]+)*")
# Cumulative, as the SDK gives by default and as Grafana Cloud (Mimir) reads it; set here so that
# no variable changes it.
CUMULATIVE_TEMPORALITY: dict[type, AggregationTemporality] = {
    Counter: AggregationTemporality.CUMULATIVE,
    Histogram: AggregationTemporality.CUMULATIVE,
}


@dataclass(frozen=True)
class MetricsExportSettings:
    # The base endpoint, without a / at its end.
    base_url: str
    # The values hold the token: never logged, never quoted.
    headers: Mapping[str, SecretStr]
    environment: DeploymentEnvironment
    # The content of the requests to the model sent with the traces (core/config.py).
    trace_content: TraceContent = "none"

    @property
    def metrics_url(self) -> str:
        return self.base_url + METRICS_PATH

    @property
    def traces_url(self) -> str:
        return self.base_url + TRACES_PATH

    @property
    def logs_url(self) -> str:
        return self.base_url + LOGS_PATH

    def reveal_headers(self) -> dict[str, str]:
        """The headers as an exporter sends them, the token in clear: never logged."""
        return {name: value.get_secret_value() for name, value in self.headers.items()}


def read_metrics_export_settings_or_refusal(
    environ: Mapping[str, str],
) -> MetricsExportSettings | str | None:
    """The settings of the export, None without its endpoint and its headers, or why a variable
    is refused, never quoting the headers nor the endpoint; the content of the requests to the model
    is refused without the export, and all in production."""
    opentelemetry_names = sorted(
        name for name in environ if name.startswith(OPENTELEMETRY_VARIABLE_PREFIX)
    )
    if opentelemetry_names:
        return (
            f"{', '.join(opentelemetry_names)}: read by the OpenTelemetry SDK behind the back"
            f" of K-B, unset them ({ENDPOINT_VARIABLE} and {HEADERS_VARIABLE} set the export)"
        )
    # Empty is not set, as for every variable of kb.
    endpoint = environ.get(ENDPOINT_VARIABLE) or None
    headers_text = environ.get(HEADERS_VARIABLE) or None
    if endpoint is None and headers_text is None:
        return find_content_without_export_refusal(environ)
    if headers_text is None:
        return f"{HEADERS_VARIABLE}: not set, while {ENDPOINT_VARIABLE} is"
    if endpoint is None:
        return f"{ENDPOINT_VARIABLE}: not set, while {HEADERS_VARIABLE} is"
    refusal = find_url_refusal(ENDPOINT_VARIABLE, endpoint, ("https",))
    if refusal is not None:
        return refusal
    # The URL of a signal given in place of the base: K-B would add a path after it.
    signal_path = next(
        (
            path
            for path in SIGNAL_PATHS
            if endpoint.rstrip("/").casefold().endswith(path)
        ),
        None,
    )
    if signal_path is not None:
        return f"{ENDPOINT_VARIABLE}: ends with {signal_path}, give the base URL"
    headers = parse_headers(headers_text)
    if headers is None:
        return (
            f"{HEADERS_VARIABLE}: not name=value pairs separated by commas, each name a"
            " token, each value printable ASCII once percent-decoded"
        )
    environment_and_content = read_environment_and_content_or_refusal(environ)
    if isinstance(environment_and_content, str):
        return environment_and_content
    environment, trace_content = environment_and_content
    return MetricsExportSettings(
        base_url=endpoint.rstrip("/"),
        headers=headers,
        environment=environment,
        trace_content=trace_content,
    )


def find_content_without_export_refusal(environ: Mapping[str, str]) -> str | None:
    """KB_TRACE_CONTENT set while the export is off, refused: texts would be kept in memory for
    nothing, and the setting would say otherwise; a value out of the list, refused as such; None
    if it is not set."""
    trace_content = environ.get(TRACE_CONTENT_VARIABLE) or "none"
    if trace_content == "none":
        return None
    if trace_content not in TRACE_CONTENTS:
        return find_unknown_content_refusal(trace_content)
    return (
        f"{TRACE_CONTENT_VARIABLE}: set, while the export is off"
        f" ({ENDPOINT_VARIABLE} and {HEADERS_VARIABLE})"
    )


def read_environment_and_content_or_refusal(
    environ: Mapping[str, str],
) -> tuple[DeploymentEnvironment, TraceContent] | str:
    """The environment of the export, required, and the content sent with the traces, none by
    default; or why one is refused: all in production, as each request to the model would send its
    items (poc.md, rule 5, exception)."""
    environment = environ.get(ENVIRONMENT_VARIABLE) or None
    if environment is None:
        return (
            f"{ENVIRONMENT_VARIABLE}: not set, {' or '.join(DEPLOYMENT_ENVIRONMENTS)}"
        )
    if environment not in DEPLOYMENT_ENVIRONMENTS:
        return (
            f"{ENVIRONMENT_VARIABLE}: {quote_value(environment)} not"
            f" {' nor '.join(DEPLOYMENT_ENVIRONMENTS)}"
        )
    trace_content = environ.get(TRACE_CONTENT_VARIABLE) or "none"
    if trace_content not in TRACE_CONTENTS:
        return find_unknown_content_refusal(trace_content)
    if trace_content == "all" and environment == "production":
        return f"{TRACE_CONTENT_VARIABLE}: all refused in production, anomalies or none"
    return environment, trace_content


def find_unknown_content_refusal(trace_content: str) -> str:
    return (
        f"{TRACE_CONTENT_VARIABLE}: {quote_value(trace_content)} not"
        f" {' nor '.join(TRACE_CONTENTS)}"
    )


def parse_headers(text: str) -> dict[str, SecretStr] | None:
    """The headers of the OTLP format, `name=value,name=value`, each value percent-decoded; None
    if a pair is not read."""
    headers: dict[str, SecretStr] = {}
    for pair in text.split(","):
        name, separator, encoded_value = pair.partition("=")
        try:
            value = unquote(encoded_value, errors="strict")
        except UnicodeDecodeError:
            return None
        if not (
            separator and HEADER_NAME.fullmatch(name) and HEADER_VALUE.fullmatch(value)
        ):
            return None
        headers[name] = SecretStr(value)
    return headers


def build_resource(environment: DeploymentEnvironment | None) -> Resource:
    """The service and its environment, nothing else, built once for the three providers: not
    Resource.create, which adds OTEL_RESOURCE_ATTRIBUTES, OTEL_SERVICE_NAME and the detectors
    OTEL_EXPERIMENTAL_RESOURCE_DETECTORS names (process gives process.owner, a personal datum).
    service.instance.id is drawn for each process, so that two processes never write the same
    cumulative series."""
    attributes: dict[str, str] = {
        "service.name": SERVICE_NAME,
        "service.instance.id": str(uuid.uuid4()),
    }
    if environment is not None:
        attributes["deployment.environment.name"] = environment
    return Resource(attributes)


def build_meter_provider(
    settings: MetricsExportSettings | None, resource: Resource
) -> MeterProvider:
    """The provider of the meters, with a reader if the export is on; the caller shuts it down
    (no atexit, which would log once the handlers of kb are removed)."""
    if settings is None:
        return MeterProvider(resource=resource, shutdown_on_exit=False)
    reader = PeriodicExportingMetricReader(
        build_exporter(settings),
        export_interval_millis=METRICS_EXPORT_INTERVAL_SECONDS
        * MILLISECONDS_PER_SECOND,
        export_timeout_millis=METRICS_EXPORT_TIMEOUT_SECONDS * MILLISECONDS_PER_SECOND,
    )
    return MeterProvider(
        metric_readers=[reader],
        resource=resource,
        shutdown_on_exit=False,
    )


class QuotingFilter(logging.Filter):
    """The values of a line of the exporter quoted as K-B quotes them (conventions.md, rule 3),
    the line kept."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(quote_argument(argument) for argument in record.args)
        elif isinstance(record.args, Mapping):
            record.args = {
                name: quote_argument(argument) for name, argument in record.args.items()
            }
        if record.exc_info:
            _, error, _ = record.exc_info
            if error is not None:
                record.msg = f"{record.msg} ({type(error).__name__})"
            record.exc_info = None
            record.exc_text = None
        return True


def quote_argument(argument: object) -> object:
    if isinstance(argument, BaseException):
        return type(argument).__name__
    if isinstance(argument, str):
        return quote_value(argument)
    return argument


QUOTING_FILTER = QuotingFilter()


def add_quoting_filter(loggers: Iterable[logging.Logger]) -> None:
    """On the loggers themselves: a filter on the logger "opentelemetry" does not see the lines of
    its children."""
    for logger in loggers:
        if QUOTING_FILTER not in logger.filters:
            logger.addFilter(QUOTING_FILTER)


def build_session() -> requests.Session:
    """A session of an exporter, which reads neither a proxy, a certificate nor a .netrc of the
    environment. The transport of the SDK follows no redirection, but counts a 3xx as a success."""
    session = requests.Session()
    session.trust_env = False
    return session


def build_exporter(settings: MetricsExportSettings) -> OTLPMetricExporter:
    """The OTLP exporter of the metrics over HTTP."""
    add_quoting_filter(EXPORTER_LOGGERS)
    return OTLPMetricExporter(
        endpoint=settings.metrics_url,
        headers=settings.reveal_headers(),
        timeout=METRICS_EXPORT_TIMEOUT_SECONDS,
        compression=Compression.Gzip,
        session=build_session(),
        preferred_temporality=CUMULATIVE_TEMPORALITY,
    )
