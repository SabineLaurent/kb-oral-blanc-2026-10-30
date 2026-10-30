"""The end-to-end test of the export of the metrics: one request to the AI API on the replay,
whose measures are sent to the real OTLP endpoint when the app stops; skipped without the
endpoint and the headers.

Run as written in MEMORY.md (-k real_export), with KB_OTLP_ENDPOINT, KB_OTLP_HEADERS and
KB_DEPLOYMENT_ENVIRONMENT in .env.e2e_test: one of the three missing while the endpoint or the
headers is set, or an OTEL_* variable, fails it. Its proof ends in Grafana Explore, as the SDK
counts a 3xx as a success."""

import json
import os
import secrets
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.sdk.metrics.export import MetricExportResult
from pydantic import SecretStr

from k_b.ai_api.app import create_app
from k_b.ai_api.connectors import metrics_export
from k_b.ai_api.connectors.data_api_settings import DataApiSettings
from k_b.ai_api.connectors.llm_replay import REPLAY_STEPS
from k_b.ai_api.connectors.llm_settings import LlmSettings
from k_b.ai_api.connectors.metrics_export import (
    build_resource,
    read_metrics_export_settings_or_refusal,
)
from k_b.ai_api.connectors.trace_export import TraceExport
from k_b.ai_api.dependencies import open_data_api_client, read_request_time
from k_b.ai_api.observability.metric_instruments import ModelMetrics
from k_b.core.config import (
    DIGEST_SUBJECTS,
    LLM_REPLAY_BASE_URL,
    NO_SUITABLE_SUBJECT_ANSWER,
)
from k_b.data_api.app import create_app as create_data_app

# The window of the test base read by tests/ai_api/test_digest_router.py: ten items.
REQUEST_TIME = datetime(2026, 10, 8, 12, 0, 30, tzinfo=UTC)
WINDOW_DAYS = 14
WINDOW_ITEM_IDS = [3, 4, 6, 7, 8, 2, 1, 12, 11, 13]
# One classification that keeps no item: one call, ok, and no writing.
REPLAY = [
    {
        "kind": "completion",
        "content": json.dumps(
            {f"i{item_id}": NO_SUITABLE_SUBJECT_ANSWER for item_id in WINDOW_ITEM_IDS}
        ),
        "prompt_tokens": 1_000,
        "completion_tokens": 200,
    }
]


@pytest.mark.e2e
def test_the_measures_of_a_request_reach_the_otlp_endpoint(
    items_database, monkeypatch, record_property
):
    settings = read_metrics_export_settings_or_refusal(os.environ)
    if settings is None:
        pytest.skip("KB_OTLP_ENDPOINT and KB_OTLP_HEADERS not set")
    assert not isinstance(settings, str), settings
    results = []

    class RecordingExporter(OTLPMetricExporter):
        """The exporter of the SDK, which keeps the result of each export."""

        def export(self, metrics_data, timeout_millis=10_000, **kwargs):
            result = super().export(metrics_data, timeout_millis, **kwargs)
            results.append(result)
            return result

    monkeypatch.setattr(metrics_export, "OTLPMetricExporter", RecordingExporter)
    resource = build_resource(settings.environment)
    data_api_key = secrets.token_urlsafe(32)
    client_key = secrets.token_urlsafe(32)
    app = create_app(
        {"TERMINAL": SecretStr(client_key)},
        LlmSettings(
            provider="replay",
            base_url=LLM_REPLAY_BASE_URL,
            model="replay",
            api_key=None,
            reasoning_effort=None,
            replay_steps=tuple(REPLAY_STEPS.validate_python(REPLAY)),
        ),
        DataApiSettings(base_url="http://localhost", api_key=SecretStr(data_api_key)),
        DIGEST_SUBJECTS,
        WINDOW_DAYS,
        ModelMetrics.from_settings(settings, resource),
        # The traces are tried by Sabine on kb serve-ai (docs/fiches-veille.md, "Traces").
        TraceExport.from_settings(None, resource),
    )
    with TestClient(
        create_data_app(items_database, {"AI_DIGEST": SecretStr(data_api_key)}),
        base_url="http://localhost",
        headers={"Authorization": f"Bearer {data_api_key}"},
    ) as data_api:
        app.dependency_overrides[open_data_api_client] = lambda: data_api
        app.dependency_overrides[read_request_time] = lambda: REQUEST_TIME
        # Leaving the block ends the lifespan: the last export.
        with TestClient(app) as client:
            response = client.post(
                "/digests",
                json={"window_days": WINDOW_DAYS},
                headers={"Authorization": f"Bearer {client_key}"},
            )

    record_property("status_code", response.status_code)
    record_property("export_results", ", ".join(result.name for result in results))
    record_property("environment", settings.environment)
    assert response.status_code == 200
    assert results
    assert all(result is MetricExportResult.SUCCESS for result in results)
