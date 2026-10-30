"""The native OpenTelemetry of FastAPI is off in both APIs, even with
FASTAPI_OTEL_AUTO_CONFIGURE=true; in a subprocess, as a global provider is set once per process."""

import os
import subprocess
import sys

# Builds both apps, runs their lifespans and prints the classes of the three global providers.
# The keys and the base are made in the subprocess: none comes from the environment.
CHECK_APPS = """
import secrets, tempfile
from pathlib import Path
from fastapi.testclient import TestClient
from opentelemetry import _logs, metrics, trace
from pydantic import SecretStr
from k_b.ai_api.app import create_app as create_ai_app
from k_b.ai_api.connectors.data_api_settings import DataApiSettings
from k_b.ai_api.connectors.llm_settings import REPLAY_MODEL, LlmSettings
from k_b.ai_api.connectors.metrics_export import build_resource
from k_b.ai_api.connectors.trace_export import TraceExport
from k_b.ai_api.observability.metric_instruments import ModelMetrics
from k_b.core.config import DIGEST_SUBJECTS, LLM_REPLAY_BASE_URL
from k_b.data_api.app import create_app as create_data_app
data_app = create_data_app(
    Path(tempfile.mkdtemp()) / "k-b.db", {"T": SecretStr(secrets.token_urlsafe(32))}
)
ai_app = create_ai_app(
    {},
    LlmSettings("replay", LLM_REPLAY_BASE_URL, REPLAY_MODEL, None, None, ()),
    DataApiSettings("http://127.0.0.1:8000", SecretStr("")),
    DIGEST_SUBJECTS,
    3,
    ModelMetrics.from_settings(None, build_resource(None)),
    TraceExport.from_settings(None, build_resource(None)),
)
with TestClient(data_app), TestClient(ai_app):
    pass
for provider in (
    trace.get_tracer_provider(),
    metrics.get_meter_provider(),
    _logs.get_logger_provider(),
):
    print(type(provider).__name__)
"""


def test_auto_configure_sets_no_global_provider_in_either_api():
    result = subprocess.run(  # noqa: S603 — this interpreter, fixed arguments
        [sys.executable, "-c", CHECK_APPS],
        capture_output=True,
        text=True,
        env={
            "PATH": os.environ.get("PATH", ""),
            "FASTAPI_OTEL_AUTO_CONFIGURE": "true",
            # Unreachable: nothing would leave even if a provider were set.
            "OTEL_EXPORTER_OTLP_ENDPOINT": "http://127.0.0.1:9",
        },
        timeout=60,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == [
        "ProxyTracerProvider",
        "_ProxyMeterProvider",
        "ProxyLoggerProvider",
    ]
