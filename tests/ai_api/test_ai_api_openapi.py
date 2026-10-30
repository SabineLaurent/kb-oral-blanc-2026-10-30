"""The versioned OpenAPI of the AI API (docs/api/openapi-ia.json): the one the app produces,
valid, and the same whatever the settings."""

import json
import secrets
from pathlib import Path

from openapi_spec_validator import OpenAPIV31SpecValidator
from pydantic import SecretStr

from k_b import cli
from k_b.ai_api.app import create_app
from k_b.ai_api.connectors.data_api_settings import DataApiSettings
from k_b.ai_api.connectors.llm_settings import LlmSettings
from k_b.ai_api.connectors.metrics_export import build_resource
from k_b.ai_api.connectors.trace_export import TraceExport
from k_b.ai_api.observability.metric_instruments import ModelMetrics
from k_b.commands.openapi_command import describe_ai_api
from k_b.core.api_access import BEARER_SCHEME_DESCRIPTION
from k_b.core.config import DigestSubject

DOCS_API_DIR = Path(__file__).parents[2] / "docs" / "api"
OPENAPI_PATH = DOCS_API_DIR / "openapi-ia.json"


def read_versioned_openapi():
    return json.loads(OPENAPI_PATH.read_text(encoding="utf-8"))


def test_the_versioned_openapi_is_the_one_the_app_produces():
    # Through JSON, as kb openapi writes it: a tuple of the schema becomes a list.
    assert read_versioned_openapi() == json.loads(json.dumps(describe_ai_api()))


def test_the_openapi_depends_on_neither_the_keys_nor_the_settings():
    settings = LlmSettings(
        provider="openai_compatible",
        base_url="https://models.example/openai/v1",
        model="model-of-the-test",
        api_key=SecretStr(secrets.token_urlsafe(32)),
        reasoning_effort="none",
        replay_steps=None,
    )
    app = create_app(
        {"KB_DIGEST": SecretStr(secrets.token_urlsafe(32))},
        settings,
        DataApiSettings(
            base_url="http://api-donnees:8000",
            api_key=SecretStr(secrets.token_urlsafe(32)),
        ),
        (DigestSubject("subject_of_the_test", "défini par le test"),),
        14,
        ModelMetrics.from_settings(None, build_resource(None)),
        TraceExport.from_settings(None, build_resource(None)),
    )

    assert json.loads(json.dumps(app.openapi())) == read_versioned_openapi()


def test_the_versioned_openapi_is_valid_and_closes_its_route_by_a_key():
    openapi = read_versioned_openapi()

    OpenAPIV31SpecValidator(openapi).validate()
    # 0.2.0: model in the response (#209); 0.3.0: unanswered (#210); 0.4.0: three fields renamed.
    assert openapi["info"] == {"title": "K-B AI API", "version": "0.4.0"}
    assert "model" in openapi["components"]["schemas"]["DigestsSchema"]["required"]
    assert list(openapi["paths"]) == ["/digests"]
    assert openapi["components"]["securitySchemes"] == {
        "HTTPBearer": {
            "type": "http",
            "scheme": "bearer",
            "description": BEARER_SCHEME_DESCRIPTION,
        }
    }
    assert openapi["paths"]["/digests"]["post"]["security"] == [{"HTTPBearer": []}]


def test_the_route_documents_each_named_state():
    responses = read_versioned_openapi()["paths"]["/digests"]["post"]["responses"]

    assert {
        status: response["description"]
        for status, response in responses.items()
        if status != "200"
    } == {
        "401": "invalid_api_key",
        "422": "invalid_parameter",
        "500": "internal_error",
        "502": "data_api_invalid_response, data_api_unauthorized, data_api_unavailable,"
        " llm_content_filtered, llm_invalid_output, llm_truncated, llm_unavailable",
        "503": "llm_rate_limited",
        "504": "data_api_timeout, llm_timeout",
    }


def test_the_request_refuses_any_other_field_and_bounds_the_window():
    request = read_versioned_openapi()["components"]["schemas"]["DigestRequestSchema"]

    assert request["additionalProperties"] is False
    assert {"type": "integer", "minimum": 1, "maximum": 14} in request["properties"][
        "window_days"
    ]["anyOf"]


def test_kb_openapi_writes_each_api_in_its_own_file(monkeypatch, tmp_path):
    monkeypatch.delenv("KB_DATA_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "docs" / "api").mkdir(parents=True)

    exit_code = cli.main(["openapi"])

    assert exit_code == 0
    for name in ("openapi-donnees.json", "openapi-ia.json"):
        assert (tmp_path / "docs" / "api" / name).read_text(encoding="utf-8") == (
            DOCS_API_DIR / name
        ).read_text(encoding="utf-8")


def test_a_failed_subject_never_announces_ok_as_its_error():
    schemas = read_versioned_openapi()["components"]["schemas"]
    reference = schemas["FailedSubjectSchema"]["properties"]["error"]["$ref"]
    error = schemas[reference.removeprefix("#/components/schemas/")]

    assert "ok" not in error["enum"]
    assert "llm_timeout" in error["enum"]
