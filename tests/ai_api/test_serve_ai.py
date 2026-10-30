"""kb serve-ai: the keys and the settings checked at the start, the host and the port, its log,
and its stop on SIGTERM; never from .env."""

import json
import logging
import os
import secrets
import signal
import socket
import subprocess
import sys
import time

import httpx2
import pytest
from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from test_metrics_export import FakeExporter

from k_b import cli
from k_b.ai_api.connectors import metrics_export, trace_export
from k_b.ai_api.connectors.data_api_settings import DATA_API_KEY_VARIABLE
from k_b.ai_api.connectors.trace_export import TraceExport
from k_b.ai_api.observability.metric_instruments import ModelMetrics
from k_b.commands import serve_ai_command
from k_b.core.api_keys import AI_API_KEY_VARIABLE_PREFIX
from k_b.core.config import DIGEST_SUBJECTS

# The variables kb serve-ai reads, which a terminal may export (a .env loaded).
READ_PREFIXES = (
    "KB_AI_API_",
    "KB_LLM_",
    "KB_DATA_API_",
    "KB_DIGEST_",
    "KB_OTLP_",
    "KB_DEPLOYMENT_",
    "OTEL_",
)


def make_key():
    """A key of the test; token_urlsafe(32) gives 43 characters."""
    return secrets.token_urlsafe(32)


# Made for the test: never a real token.
OTLP_TOKEN = make_key()


@pytest.fixture(autouse=True)
def no_setting_of_the_terminal(monkeypatch):
    for name in os.environ:
        if name.startswith(READ_PREFIXES) or name == "KB_LOG_TO_FILE":
            monkeypatch.delenv(name)


@pytest.fixture
def replay_file(tmp_path):
    path = tmp_path / "replay.json"
    path.write_text(json.dumps([{"kind": "completion", "content": "{}"}]))
    return path


@pytest.fixture
def environment(replay_file):
    """The settings kb serve-ai needs, on the replay."""
    return {
        "KB_AI_API_KEY_KB_DIGEST": make_key(),
        "KB_LLM_PROVIDER": "replay",
        "KB_LLM_REPLAY_FILE": str(replay_file),
        "KB_DATA_API_KEY_AI_DIGEST": make_key(),
    }


@pytest.fixture
def data_dir(monkeypatch, tmp_path):
    path = tmp_path / "data"
    path.mkdir()
    monkeypatch.setenv("KB_DATA_DIR", str(path))
    return path


@pytest.fixture
def uvicorn_calls(monkeypatch):
    """The calls kb serve-ai makes to uvicorn.run, which is not run."""
    calls = []
    monkeypatch.setattr(
        serve_ai_command.uvicorn,
        "run",
        lambda app, **options: calls.append((app, options)),
    )
    return calls


def set_environment(monkeypatch, variables):
    for name, value in variables.items():
        monkeypatch.setenv(name, value)


def test_serve_ai_listens_on_127_0_0_1_8001_by_default_with_the_real_subjects(
    monkeypatch, data_dir, uvicorn_calls, environment
):
    set_environment(monkeypatch, environment)

    exit_code = cli.main(["serve-ai"])

    [(app, options)] = uvicorn_calls
    assert exit_code == 0
    assert options == {
        "host": "127.0.0.1",
        "port": 8001,
        "log_config": None,
        "access_log": False,
    }
    assert app.state.default_window_days == 3
    assert app.state.subjects == DIGEST_SUBJECTS
    assert set(app.state.api_keys) == {"KB_DIGEST"}


def test_serve_ai_reads_the_host_the_port_and_the_window_of_the_environment(
    monkeypatch, data_dir, uvicorn_calls, environment
):
    set_environment(
        monkeypatch,
        environment
        | {
            "KB_AI_API_HOST": "0.0.0.0",  # noqa: S104 — the host of a container
            "KB_AI_API_PORT": "9001",
            "KB_DIGEST_WINDOW_DAYS": "14",
        },
    )

    cli.main(["serve-ai"])

    [(app, options)] = uvicorn_calls
    assert (options["host"], options["port"]) == ("0.0.0.0", 9001)  # noqa: S104
    assert app.state.default_window_days == 14


@pytest.mark.parametrize(
    ("variables", "message"),
    [
        (
            {"KB_AI_API_KEY_KB_DIGEST": None},
            "no API key: set KB_AI_API_KEY_<CLIENT>",
        ),
        (
            {"KB_AI_API_KEY_KB_DIGEST": make_key()[:31]},
            "'KB_AI_API_KEY_KB_DIGEST': key of 31 characters, minimum 32",
        ),
        (
            {"KB_AI_API_KEY_kb_digest": make_key()},
            "'KB_AI_API_KEY_kb_digest': client name empty or not [A-Z0-9_]+",
        ),
        (
            {"KB_AI_API_HOST": "localhost"},
            "KB_AI_API_HOST: 'localhost' not an IPv4 address",
        ),
        ({"KB_AI_API_PORT": "0"}, "KB_AI_API_PORT: '0' not an integer from 1 to 65535"),
        (
            {"KB_DIGEST_WINDOW_DAYS": "0"},
            "KB_DIGEST_WINDOW_DAYS: '0' not an integer from 1 to 14",
        ),
        (
            {"KB_DIGEST_WINDOW_DAYS": "15"},
            "KB_DIGEST_WINDOW_DAYS: '15' not an integer from 1 to 14",
        ),
        (
            {"KB_DIGEST_WINDOW_DAYS": "3.0"},
            "KB_DIGEST_WINDOW_DAYS: '3.0' not an integer from 1 to 14",
        ),
        (
            {"KB_DIGEST_WINDOW_DAYS": " 3"},
            "KB_DIGEST_WINDOW_DAYS: ' 3' not an integer from 1 to 14",
        ),
        (
            {"KB_LLM_PROVIDER": None},
            "KB_LLM_PROVIDER: not set, openai_compatible or replay",
        ),
        ({"KB_DATA_API_KEY_AI_DIGEST": None}, "KB_DATA_API_KEY_AI_DIGEST: not set"),
        (
            {"KB_DATA_API_KEY_AI_DIGEST": "short-key-of-the-test"},
            "KB_DATA_API_KEY_AI_DIGEST: key of 21 characters, minimum 32",
        ),
        (
            {"KB_OTLP_ENDPOINT": "https://otlp.example.net/otlp"},
            "KB_OTLP_HEADERS: not set, while KB_OTLP_ENDPOINT is",
        ),
        (
            {
                "KB_OTLP_ENDPOINT": "https://otlp.example.net/otlp",
                "KB_OTLP_HEADERS": f"Authorization=Basic%20{OTLP_TOKEN}",
            },
            "KB_DEPLOYMENT_ENVIRONMENT: not set, sandbox or production",
        ),
        (
            {"OTEL_SERVICE_NAME": "k-b"},
            "OTEL_SERVICE_NAME: read by the OpenTelemetry SDK behind the back of K-B",
        ),
        (
            {"KB_TRACE_CONTENT": "all"},
            "KB_TRACE_CONTENT: set, while the export is off",
        ),
        (
            {
                "KB_OTLP_ENDPOINT": "https://otlp.example.net/otlp",
                "KB_OTLP_HEADERS": f"Authorization=Basic%20{OTLP_TOKEN}",
                "KB_DEPLOYMENT_ENVIRONMENT": "production",
                "KB_TRACE_CONTENT": "all",
            },
            "KB_TRACE_CONTENT: all refused in production, anomalies or none",
        ),
    ],
    ids=[
        "no-key",
        "short-key",
        "client-name",
        "host",
        "port",
        "window-zero",
        "window-fifteen",
        "window-float",
        "window-space",
        "no-provider",
        "no-data-api-key",
        "short-data-api-key",
        "otlp-without-headers",
        "otlp-without-environment",
        "otel-variable",
        "content-without-export",
        "content-all-in-production",
    ],
)
def test_serve_ai_exits_2_and_names_the_variable_refused(
    monkeypatch, capsys, data_dir, uvicorn_calls, environment, variables, message
):
    set_environment(
        monkeypatch,
        {
            name: value
            for name, value in (environment | variables).items()
            if value is not None
        },
    )

    exit_code = cli.main(["serve-ai"])

    error_output = capsys.readouterr().err
    assert exit_code == 2
    assert message in error_output
    assert uvicorn_calls == []
    for name in ("KB_AI_API_KEY_KB_DIGEST", "KB_DATA_API_KEY_AI_DIGEST"):
        if environment.get(name):
            assert environment[name] not in error_output
    assert OTLP_TOKEN not in error_output


def test_serve_ai_without_export_says_it_and_gives_the_app_its_measures(
    monkeypatch, capsys, data_dir, uvicorn_calls, environment
):
    set_environment(monkeypatch, environment)

    cli.main(["serve-ai"])

    [(app, _)] = uvicorn_calls
    assert isinstance(app.state.model_metrics, ModelMetrics)
    assert isinstance(app.state.trace_export, TraceExport)
    assert "metrics and traces export: off" in capsys.readouterr().err


def test_serve_ai_builds_one_resource_for_its_providers(
    monkeypatch, data_dir, uvicorn_calls, environment
):
    resources = []

    def build_resource(environment):
        resources.append(metrics_export.build_resource(environment))
        return resources[-1]

    monkeypatch.setattr(serve_ai_command, "build_resource", build_resource)
    set_environment(monkeypatch, environment)

    cli.main(["serve-ai"])

    [(app, _)] = uvicorn_calls
    [resource] = resources
    assert app.state.trace_export.tracer_provider.resource is resource
    assert app.state.trace_export.logger_provider.resource is resource


def test_serve_ai_with_export_names_its_environment_never_its_endpoint_nor_its_headers(
    monkeypatch, capsys, data_dir, uvicorn_calls, environment
):
    # No network: the exporters built are fake ones.
    exporters = []
    monkeypatch.setattr(
        metrics_export,
        "OTLPMetricExporter",
        lambda **options: exporters.append(options) or FakeExporter(**options),
    )
    monkeypatch.setattr(
        trace_export,
        "OTLPSpanExporter",
        lambda **options: exporters.append(options) or InMemorySpanExporter(),
    )
    monkeypatch.setattr(
        trace_export,
        "OTLPLogExporter",
        lambda **options: exporters.append(options) or InMemoryLogRecordExporter(),
    )
    set_environment(
        monkeypatch,
        environment
        | {
            "KB_OTLP_ENDPOINT": "https://otlp.example.net/otlp",
            "KB_OTLP_HEADERS": f"Authorization=Basic%20{OTLP_TOKEN}",
            "KB_DEPLOYMENT_ENVIRONMENT": "sandbox",
            "KB_TRACE_CONTENT": "all",
        },
    )

    exit_code = cli.main(["serve-ai"])

    [(app, _)] = uvicorn_calls
    app.state.model_metrics.stop()
    app.state.trace_export.stop_traces()
    app.state.trace_export.stop_logs()
    error_output = capsys.readouterr().err
    assert exit_code == 0
    assert [options["endpoint"] for options in exporters] == [
        "https://otlp.example.net/otlp/v1/metrics",
        "https://otlp.example.net/otlp/v1/traces",
        "https://otlp.example.net/otlp/v1/logs",
    ]
    assert (
        "metrics and traces export: OTLP, environment sandbox, model content all"
        in error_output
    )
    assert app.state.tracing.content == "all"
    assert "otlp.example.net" not in error_output
    assert OTLP_TOKEN not in error_output


def test_serve_ai_logs_its_clients_its_provider_and_its_model_never_a_key(
    monkeypatch, capsys, data_dir, uvicorn_calls, environment
):
    set_environment(monkeypatch, environment)

    cli.main(["serve-ai"])

    error_output = capsys.readouterr().err
    assert "AI API clients loaded: KB_DIGEST" in error_output
    # An AI API set on the replay by mistake says it.
    assert "LLM provider replay, model replay" in error_output
    assert "digest window by default: 3 days" in error_output
    for name in ("KB_AI_API_KEY_KB_DIGEST", "KB_DATA_API_KEY_AI_DIGEST"):
        assert environment[name] not in error_output


def test_serve_ai_writes_its_own_log_file_under_kb_data_dir(
    monkeypatch, data_dir, uvicorn_calls, environment
):
    """A long run beside kb serve-data: each keeps its own file."""
    set_environment(monkeypatch, environment)

    cli.main(["serve-ai"])

    assert "AI API clients loaded: KB_DIGEST" in (
        data_dir / "logs" / "kb-serve-ai.log"
    ).read_text(encoding="utf-8")
    assert not (data_dir / "logs" / "kb.log").exists()
    assert not (data_dir / "logs" / "kb-serve-data.log").exists()


def test_serve_ai_needs_kb_data_dir_for_its_log(
    monkeypatch, capsys, uvicorn_calls, environment
):
    monkeypatch.delenv("KB_DATA_DIR", raising=False)
    set_environment(monkeypatch, environment)

    exit_code = cli.main(["serve-ai"])

    assert exit_code == 2
    assert "KB_DATA_DIR must point to the data directory" in capsys.readouterr().err
    assert uvicorn_calls == []


def find_free_port():
    """A port free on the machine now; 0 is refused by the rule of the port."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def test_serve_ai_stops_on_sigterm_with_the_code_of_a_process_stopped_by_it(
    tmp_path, environment
):
    """uvicorn stops, then sends SIGTERM again: a parent process reads -15, a shell 143; the
    environment is built, never the terminal's."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    port = find_free_port()
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import sys; from k_b import cli; sys.exit(cli.main(['serve-ai']))",
        ],
        env={
            **environment,
            "KB_DATA_DIR": str(data_dir),
            "KB_LOG_TO_FILE": "false",
            "KB_AI_API_PORT": str(port),
            "PATH": os.environ.get("PATH", ""),
        },
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        wait_for_health(port)
        process.send_signal(signal.SIGTERM)
        _, error_output = process.communicate(timeout=20)
    finally:
        process.kill()

    assert process.returncode == -signal.SIGTERM
    assert cli.ExitCode.STOPPED_BY_SIGTERM == 128 + signal.SIGTERM
    assert b"Shutting down" in error_output


def wait_for_health(port, timeout_seconds=20):
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            if httpx2.get(f"http://127.0.0.1:{port}/health").status_code == 200:
                return
        except httpx2.TransportError:
            pass
        time.sleep(0.1)
    raise AssertionError(f"kb serve-ai did not answer on port {port}")


def test_the_log_keeps_no_request_line_of_the_http_client(
    monkeypatch, capsys, data_dir, environment
):
    """httpx2 logs each request at INFO with its URL and no request id; its warnings stay."""
    set_environment(monkeypatch, environment)

    def log_as_the_http_client(app, **options):
        http_client_logger = logging.getLogger("httpx2")
        http_client_logger.info("HTTP Request: POST https://resource.example/v1")
        http_client_logger.warning("warning of the http client")

    monkeypatch.setattr(serve_ai_command.uvicorn, "run", log_as_the_http_client)

    cli.main(["serve-ai"])

    error_output = capsys.readouterr().err
    assert "resource.example" not in error_output
    assert "warning of the http client" in error_output


def test_serve_ai_exits_2_when_a_client_has_the_key_it_sends_to_the_data_api(
    monkeypatch, capsys, data_dir, uvicorn_calls, environment
):
    """A shared key would let the client of the AI API read the data API too (least privilege)."""
    shared_key = make_key()
    set_environment(
        monkeypatch,
        environment
        | {
            f"{AI_API_KEY_VARIABLE_PREFIX}KB_DIGEST": shared_key,
            DATA_API_KEY_VARIABLE: shared_key,
        },
    )

    exit_code = cli.main(["serve-ai"])

    error_output = capsys.readouterr().err
    assert exit_code == 2
    assert (
        f"'{AI_API_KEY_VARIABLE_PREFIX}KB_DIGEST' and '{DATA_API_KEY_VARIABLE}': same key"
        in error_output
    )
    assert shared_key not in error_output
    assert uvicorn_calls == []
