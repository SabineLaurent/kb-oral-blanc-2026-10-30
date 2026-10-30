"""The versioned OpenAPI of the data API (docs/api/openapi-donnees.json): the one the app
produces, and valid."""

import json
from pathlib import Path

import pytest
from openapi_spec_validator import OpenAPIV31SpecValidator

from k_b.core.api_access import BEARER_SCHEME_DESCRIPTION
from k_b.data_api.app import ARXIV_ACKNOWLEDGEMENT, create_app

OPENAPI_PATH = Path(__file__).parents[2] / "docs" / "api" / "openapi-donnees.json"


def read_versioned_openapi():
    return json.loads(OPENAPI_PATH.read_text(encoding="utf-8"))


def test_the_versioned_openapi_is_the_one_the_app_produces(tmp_path):
    produced = create_app(tmp_path / "k-b.sqlite", {}).openapi()

    # Through JSON, as kb openapi writes it: a tuple of the schema becomes a list.
    assert read_versioned_openapi() == json.loads(json.dumps(produced))


def test_the_versioned_openapi_is_valid_and_declares_its_security_scheme():
    openapi = read_versioned_openapi()

    OpenAPIV31SpecValidator(openapi).validate()
    assert openapi["info"]["version"] == "0.5.0"
    assert openapi["info"]["description"] == ARXIV_ACKNOWLEDGEMENT
    assert openapi["components"]["securitySchemes"] == {
        "HTTPBearer": {
            "type": "http",
            "scheme": "bearer",
            "description": BEARER_SCHEME_DESCRIPTION,
        }
    }
    for path in openapi["paths"].values():
        assert path["get"]["security"] == [{"HTTPBearer": []}]


def test_since_is_declared_a_date_time():
    parameters = read_versioned_openapi()["paths"]["/items"]["get"]["parameters"]
    [since] = [parameter for parameter in parameters if parameter["name"] == "since"]

    assert {"type": "string", "format": "date-time"} in since["schema"]["anyOf"]


@pytest.mark.parametrize(
    ("path", "not_found"),
    [
        ("/items", None),
        ("/items/{item_id}", "item_not_found"),
        ("/trends", "trend_window_not_found"),
    ],
)
def test_each_data_route_documents_its_errors(path, not_found):
    responses = read_versioned_openapi()["paths"][path]["get"]["responses"]

    expected = {
        "401": "invalid_api_key",
        "422": "invalid_parameter",
        "500": "database_error, internal_error",
        "503": "database_missing",
    }
    if not_found is not None:
        expected["404"] = not_found
    assert {
        status: response["description"]
        for status, response in responses.items()
        if status != "200"
    } == expected


def test_the_detail_is_a_union_discriminated_by_kind():
    openapi = read_versioned_openapi()
    detail = openapi["paths"]["/items/{item_id}"]["get"]["responses"]["200"]
    schema = detail["content"]["application/json"]["schema"]

    assert schema["discriminator"]["propertyName"] == "kind"
    assert set(schema["discriminator"]["mapping"]) == {
        "paper",
        "vulnerability",
        "release",
        "advisory",
        "newsletter_article",
        "zotero_entry",
        "clip",
    }
    assert len(schema["oneOf"]) == 7


@pytest.mark.parametrize(
    ("schema_name", "field", "said"),
    [
        ("VulnerabilityDetailSchema", "revised_at", "A date of K-B"),
        ("ZoteroEntryDetailSchema", "revised_at", "A date of K-B"),
        ("HighlightSchema", "revised_at", "A date of K-B"),
        ("HighlightSchema", "withdrawn_at", "A date of K-B"),
        ("ListedItemSchema", "withdrawn_at", "A date of K-B"),
        ("PaperDetailSchema", "arxiv_updated_at", "A date of arXiv"),
        ("HighlightSchema", "highlighted_at", "A date of Zotero"),
        ("ClipDetailSchema", "published_at", "A date of the page kept"),
    ],
)
def test_the_dates_of_k_b_and_of_the_sources_are_described(schema_name, field, said):
    schemas = read_versioned_openapi()["components"]["schemas"]

    assert said in schemas[schema_name]["properties"][field]["description"]


def test_no_route_documents_a_raw_file_path_or_a_collection_date():
    text = OPENAPI_PATH.read_text(encoding="utf-8")

    assert "raw_file_path" not in text
    assert "collected_at" not in text


def test_window_end_is_declared_a_date():
    parameters = read_versioned_openapi()["paths"]["/trends"]["get"]["parameters"]
    [window_end] = [p for p in parameters if p["name"] == "window_end"]

    assert {"type": "string", "format": "date"} in window_end["schema"]["anyOf"]
