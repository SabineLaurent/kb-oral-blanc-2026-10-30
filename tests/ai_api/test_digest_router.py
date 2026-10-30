"""POST /digests on the replay provider and the data API of the test base, without network nor
key of a provider."""

import json
import logging
import secrets
import sys
import threading
from dataclasses import replace
from datetime import UTC, datetime
from typing import get_args

import httpx2
import pytest
from fastapi.routing import iter_route_contexts
from fastapi.testclient import TestClient
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.trace import SpanKind, StatusCode
from pydantic import SecretStr

from k_b.ai_api.app import create_app
from k_b.ai_api.connectors.data_api_client import DataApiState
from k_b.ai_api.connectors.data_api_settings import DataApiSettings
from k_b.ai_api.connectors.llm_client import LlmFailure, build_http_client
from k_b.ai_api.connectors.llm_replay import REPLAY_STEPS, ReplayTransport
from k_b.ai_api.connectors.llm_settings import LlmSettings
from k_b.ai_api.dependencies import (
    open_data_api_client,
    open_llm_client,
    read_request_time,
)
from k_b.ai_api.errors import STATUS_BY_STATE
from k_b.ai_api.observability.metric_instruments import ModelMetrics
from k_b.ai_api.routers import digest_router
from k_b.ai_api.tracing import TRACING_OFF
from k_b.core.config import (
    LLM_INPUT_USD_PER_MILLION_TOKENS,
    LLM_OUTPUT_USD_PER_MILLION_TOKENS,
    LLM_REPLAY_BASE_URL,
    NO_SUITABLE_SUBJECT_ANSWER,
    DigestSubject,
)
from k_b.data_api.app import create_app as create_data_app

# The subjects of the tests, never the real ones: a change of the real list breaks no test.
TEST_SUBJECTS = (
    DigestSubject("security_alerts", "failles et alertes de sécurité"),
    DigestSubject("agent_tools", "outils et cadres d'agents"),
    DigestSubject("research", "articles de recherche"),
)
REPLAY_SETTINGS = LlmSettings(
    provider="replay",
    base_url=LLM_REPLAY_BASE_URL,
    model="replay",
    api_key=None,
    reasoning_effort=None,
    replay_steps=None,
)
# Made for the test: never a real key.
CLIENT_KEY = secrets.token_urlsafe(32)
AUTHORIZATION = {"Authorization": f"Bearer {CLIENT_KEY}"}
# The time of the requests, set by the test. Over 14 days, the window reads the vulnerabilities 3
# and 4, the papers 2 and 1, the releases 6, 7 and 8 and the newsletter articles 12, 11 and 13 of
# the test base; the advisories, of June, are older.
REQUEST_TIME = datetime(2026, 10, 8, 12, 0, 30, 500_000, tzinfo=UTC)
WINDOW_DAYS = 14
SECURITY_ITEMS = [3, 4]
AGENT_ITEMS = [6, 7, 8]
RESEARCH_ITEMS = [2, 1]
NO_SUITABLE_SUBJECT_ITEMS = [12, 11, 13]
# Facts copied from the items of the test base: the CVE of vulnerability 3, the tag of release 6.
SECURITY_CLAIM = {
    "text": "La faille CVE-2026-86950 touche des produits Apple.",
    "item_ids": ["i3"],
    "facts": [{"value": "CVE-2026-86950", "item_id": "i3"}],
}
AGENT_CLAIM = {
    "text": "Claude Code publie la version v2.1.287.",
    "item_ids": ["i6"],
    "facts": [{"value": "v2.1.287", "item_id": "i6"}],
}
TOKENS_IN = 1_000
TOKENS_OUT = 200


def completion(output):
    content = output if isinstance(output, str) else json.dumps(output)
    return {
        "kind": "completion",
        "content": content,
        "prompt_tokens": TOKENS_IN,
        "completion_tokens": TOKENS_OUT,
    }


def classified(
    security=SECURITY_ITEMS,
    agent=AGENT_ITEMS,
    research=RESEARCH_ITEMS,
    no_suitable_subject=NO_SUITABLE_SUBJECT_ITEMS,
):
    return completion(
        {
            f"i{item_id}": answer
            for answer, item_ids in (
                ("security_alerts", security),
                ("agent_tools", agent),
                ("research", research),
                (NO_SUITABLE_SUBJECT_ANSWER, no_suitable_subject),
            )
            for item_id in item_ids
        }
    )


def written(*claims):
    return completion({"claims": list(claims)})


# A valid request: the classification, then the writing of each subject, in the order of the list.
VALID_STEPS = [
    classified(),
    written(SECURITY_CLAIM),
    written(AGENT_CLAIM),
    written(),
]
# An output the schema refuses, twice: the classification fails the request.
INVALID_OUTPUT = completion("not JSON")


@pytest.fixture
def data_api(items_database):
    """The data API of the test base, as the AI API reads it, with the key of AI_DIGEST."""
    key = secrets.token_urlsafe(32)
    with TestClient(
        create_data_app(items_database, {"AI_DIGEST": SecretStr(key)}),
        base_url="http://localhost",
        headers={"Authorization": f"Bearer {key}"},
    ) as client:
        yield client


@pytest.fixture
def metric_reader():
    """The reader of the OpenTelemetry measures of the AI API of the test, in memory."""
    return InMemoryMetricReader()


@pytest.fixture
def make_ai_api(data_api, metric_reader, recorded_traces):
    """A client of the AI API on the replay of these steps, and the replay transport, which keeps
    each request; replay_from_settings uses the replay of the settings, as kb serve-ai does. Its
    spans and log records are kept by recorded_traces."""

    def make(
        steps,
        default_window_days=WINDOW_DAYS,
        data_api_client=None,
        replay_from_settings=False,
        trace_content="none",
    ):
        settings = replace(
            REPLAY_SETTINGS, replay_steps=tuple(REPLAY_STEPS.validate_python(steps))
        )
        app = create_app(
            {"TERMINAL": SecretStr(CLIENT_KEY)},
            settings,
            DataApiSettings(
                base_url="http://localhost",
                api_key=SecretStr(secrets.token_urlsafe(32)),
            ),
            TEST_SUBJECTS,
            default_window_days,
            ModelMetrics(
                MeterProvider(metric_readers=[metric_reader], shutdown_on_exit=False)
            ),
            replace(recorded_traces.trace_export, content=trace_content),
        )
        transport = ReplayTransport(steps)

        def open_replay():
            with build_http_client(settings, transport=transport) as http_client:
                yield http_client

        if not replay_from_settings:
            app.dependency_overrides[open_llm_client] = open_replay
        app.dependency_overrides[open_data_api_client] = lambda: (
            data_api if data_api_client is None else data_api_client
        )
        app.dependency_overrides[read_request_time] = lambda: REQUEST_TIME
        return TestClient(app, raise_server_exceptions=False), transport

    return make


def post_digests(client, body=None, headers=AUTHORIZATION):
    return client.post(
        "/digests",
        json={"window_days": WINDOW_DAYS} if body is None else body,
        headers=headers,
    )


def read_metric_lines(capsys):
    return [json.loads(line) for line in capsys.readouterr().out.splitlines()]


def test_a_request_gives_a_digest_per_subject_with_its_window_and_its_id(make_ai_api):
    client, _ = make_ai_api(VALID_STEPS, replay_from_settings=True)

    response = post_digests(client)

    assert response.status_code == 200
    body = response.json()
    assert body["request_id"] == response.headers["X-Request-ID"]
    # The model of the settings of kb serve-ai, written by kb digest in each sheet (#209).
    assert body["model"] == "replay"
    assert body["window"] == {
        "days": 14,
        "since": "2026-09-24T12:00:30Z",
        "until": "2026-10-08T12:00:30Z",
        "capped_kind": None,
        "covered_after": None,
    }
    assert [digest["subject"] for digest in body["digests"]] == [
        "security_alerts",
        "agent_tools",
        "research",
    ]
    security = body["digests"][0]
    assert security["claims"] == [{"text": SECURITY_CLAIM["text"], "item_ids": [3]}]
    [source] = security["sources"]
    assert (source["item_id"], source["kind"]) == (3, "vulnerability")
    assert source["url"].startswith("https://")
    assert security["items_not_retained_in_output"] == [4]
    assert body["digests"][2]["claims"] == []
    assert body["failed_subjects"] == []
    assert body["no_suitable_subject"] == NO_SUITABLE_SUBJECT_ITEMS
    assert (
        body["items_in"],
        body["items_dropped_before_writing_stage"],
        body["items_capped"],
    ) == (
        10,
        0,
        False,
    )


@pytest.mark.parametrize("body", [{}, {"window_days": None}])
def test_a_request_without_window_takes_the_window_of_the_settings(make_ai_api, body):
    # Over 3 days, the newsletter articles 12 and 11 only.
    client, _ = make_ai_api(
        [classified([], [], [], no_suitable_subject=[12, 11])], default_window_days=3
    )

    response = post_digests(client, body)

    assert response.status_code == 200
    assert response.json()["window"]["days"] == 3
    assert response.json()["window"]["since"] == "2026-10-05T12:00:30Z"
    assert response.json()["items_in"] == 2


def test_an_empty_window_answers_without_calling_the_model(make_ai_api, capsys):
    client, transport = make_ai_api([])
    client.app.dependency_overrides[read_request_time] = lambda: datetime(
        2030, 1, 1, tzinfo=UTC
    )

    response = post_digests(client)

    assert response.status_code == 200
    assert response.json()["digests"] == []
    assert response.json()["items_in"] == 0
    assert transport.requests == []
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer wrong-key-of-the-test"},
        {"Authorization": "Bearer"},
    ],
)
def test_a_request_without_a_valid_key_is_refused_before_any_reading(
    make_ai_api, headers
):
    client, transport = make_ai_api(VALID_STEPS)
    failing_reading = httpx2.Client(
        base_url="http://localhost", transport=httpx2.MockTransport(fail_the_test)
    )
    client.app.dependency_overrides[open_data_api_client] = lambda: failing_reading

    response = post_digests(client, headers=headers)

    assert response.status_code == 401
    assert response.json() == {"error": "invalid_api_key"}
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert transport.requests == []


def fail_the_test(request):
    raise AssertionError("the data API was read")


@pytest.mark.parametrize(
    ("content", "parameter"),
    [
        ({"window_days": 0}, "window_days"),
        ({"window_days": 15}, "window_days"),
        ({"window_days": True}, "window_days"),
        ({"window_days": 3.0}, "window_days"),
        ({"window_days": "3"}, "window_days"),
        ({"window_days": 3, "field\nof the client": 1}, "body"),
        ([3], "body"),
        (None, "body"),
        (b"not JSON", "body"),
        (b"\xff", "body"),
    ],
    ids=[
        "zero",
        "fifteen",
        "true",
        "float",
        "string",
        "extra-field",
        "not-an-object",
        "absent",
        "not-json",
        "not-utf-8",
    ],
)
def test_a_malformed_request_is_refused_422_before_any_call(
    make_ai_api, content, parameter
):
    client, transport = make_ai_api(VALID_STEPS)

    response = send(client, content, AUTHORIZATION)

    assert response.status_code == 422
    assert response.json() == {"error": "invalid_parameter", "parameters": [parameter]}
    assert transport.requests == []


def send(client, content, headers):
    """A body as given: JSON, raw bytes, or none."""
    if content is None:
        return client.post("/digests", headers=headers)
    if isinstance(content, bytes):
        return client.post(
            "/digests",
            content=content,
            headers={**headers, "Content-Type": "application/json"},
        )
    return client.post("/digests", json=content, headers=headers)


@pytest.mark.parametrize(
    ("content", "status"),
    [({"window_days": 0}, 401), (None, 401), (b"not JSON", 422), (b"\xff", 422)],
)
def test_without_key_only_a_body_that_cannot_be_read_gets_422(
    make_ai_api, content, status
):
    """FastAPI reads the JSON of the body before the dependencies, a limit written in
    docs/fiches-veille.md; nothing is read nor sent."""
    client, transport = make_ai_api(VALID_STEPS)

    response = send(client, content, {})

    assert response.status_code == status
    assert transport.requests == []


@pytest.mark.parametrize(
    ("steps", "status", "state", "retry_after"),
    [
        ([INVALID_OUTPUT, INVALID_OUTPUT], 502, "llm_invalid_output", None),
        (
            [{"kind": "response", "status": 429, "retry_after": "30"}],
            503,
            "llm_rate_limited",
            "30",
        ),
        (
            [
                {"kind": "response", "status": 429, "retry_after": "0"},
                {"kind": "response", "status": 429},
            ],
            503,
            "llm_rate_limited",
            None,
        ),
        ([{"kind": "timeout"}], 504, "llm_timeout", None),
    ],
    ids=[
        "invalid-output",
        "rate-limited",
        "rate-limited-without-retry-after",
        "timeout",
    ],
)
def test_a_classification_that_fails_fails_the_request_with_its_lines(
    make_ai_api, capsys, steps, status, state, retry_after
):
    client, _ = make_ai_api(steps)

    response = post_digests(client)

    assert response.status_code == status
    assert response.json() == {"error": state}
    assert response.headers.get("Retry-After") == retry_after
    [line] = read_metric_lines(capsys)
    assert (line["stage"], line["outcome"]) == ("classify", state)
    assert line["request_id"] == response.headers["X-Request-ID"]
    assert isinstance(line["data_fetch_ms"], int)
    assert (line["items_dropped_before_writing_stage"], line["items_capped"]) == (
        0,
        False,
    )


def test_a_writing_that_fails_names_its_subject_and_the_others_are_returned(
    make_ai_api,
):
    client, _ = make_ai_api(
        [classified(), {"kind": "timeout"}, written(AGENT_CLAIM), written()]
    )

    response = post_digests(client)

    assert response.status_code == 200
    assert response.json()["failed_subjects"] == [
        {"subject": "security_alerts", "stage": "write", "error": "llm_timeout"}
    ]
    assert [digest["subject"] for digest in response.json()["digests"]] == [
        "agent_tools",
        "research",
    ]


def raise_timeout(request):
    raise httpx2.ReadTimeout("timeout of the test", request=request)


@pytest.mark.parametrize(
    ("data_api_client", "status", "state"),
    [
        (
            httpx2.Client(
                base_url="http://localhost",
                transport=httpx2.MockTransport(raise_timeout),
            ),
            504,
            "data_api_timeout",
        ),
        (
            httpx2.Client(
                base_url="http://localhost",
                transport=httpx2.MockTransport(lambda request: httpx2.Response(401)),
            ),
            502,
            "data_api_unauthorized",
        ),
    ],
    ids=["timeout", "key-refused"],
)
def test_a_reading_that_fails_fails_the_request_without_a_call_to_the_model(
    make_ai_api, capsys, caplog, data_api_client, status, state
):
    client, transport = make_ai_api(VALID_STEPS, data_api_client=data_api_client)

    with caplog.at_level(logging.ERROR, logger="k_b.ai_api"):
        response = post_digests(client)

    assert response.status_code == status
    assert response.json() == {"error": state}
    assert transport.requests == []
    assert capsys.readouterr().out == ""
    request_id = response.headers["X-Request-ID"]
    [failure] = [
        r.getMessage() for r in caplog.records if r.name.startswith("k_b.ai_api")
    ]
    assert failure.startswith(f"reading of the watch failed: {state}")
    assert failure.endswith(f", request_id={request_id}")


# The fields of a metric line, in the order of the spec (docs/fiches-veille.md, "Métriques").
METRIC_FIELDS = [
    "ts",
    "event",
    "request_id",
    "stage",
    "subject",
    "provider",
    "model",
    "prompt_version",
    "items_in",
    "items_truncated",
    "items_dropped_before_writing_stage",
    "items_capped",
    "input_chars",
    "tokens_in",
    "tokens_out",
    "reasoning_tokens",
    "data_fetch_ms",
    "llm_ms",
    "check_ms",
    "attempts",
    "outcome",
    "finish_reason",
    "http_status",
    "schema_valid",
    "failed_check",
    "first_failed_check",
    "facts_checked",
    "facts_found",
    "fidelity_rate",
    "claims_removed",
    "items_removed_by_checks",
    "items_with_subject",
    "items_no_suitable_subject",
    "items_unanswered",
    "cost_estimated",
]


def test_each_call_writes_one_json_line_on_stdout_without_a_text(make_ai_api, capsys):
    client, _ = make_ai_api(VALID_STEPS)

    response = post_digests(client)

    output = capsys.readouterr().out
    lines = [json.loads(line) for line in output.splitlines()]
    assert [(line["stage"], line["subject"]) for line in lines] == [
        ("classify", None),
        ("write", "security_alerts"),
        ("write", "agent_tools"),
        ("write", "research"),
    ]
    first, *others = lines
    assert list(first) == METRIC_FIELDS
    assert first["event"] == "llm_call"
    assert first["request_id"] == response.json()["request_id"]
    assert (first["provider"], first["model"]) == ("replay", "replay")
    assert isinstance(first["data_fetch_ms"], int)
    assert (
        first["items_in"],
        first["items_dropped_before_writing_stage"],
        first["items_capped"],
    ) == (
        10,
        0,
        False,
    )
    assert (first["claims_removed"], first["items_removed_by_checks"]) == (None, None)
    # The three cases of the classification: 5 items given a subject, 3 that no subject
    # suits, none left without answer.
    assert (
        first["items_with_subject"],
        first["items_no_suitable_subject"],
        first["items_unanswered"],
    ) == (7, 3, 0)
    # Each item of the call is in one case, and in one only.
    assert (
        first["items_with_subject"]
        + first["items_no_suitable_subject"]
        + first["items_unanswered"]
    ) == first["items_in"]
    for line in others:
        assert (
            line["data_fetch_ms"],
            line["items_dropped_before_writing_stage"],
            line["items_capped"],
        ) == (
            None,
            None,
            None,
        )
    assert (others[0]["claims_removed"], others[0]["items_removed_by_checks"]) == (0, 0)
    for line in others:
        assert (
            line["items_with_subject"],
            line["items_no_suitable_subject"],
            line["items_unanswered"],
        ) == (None, None, None)
    # The counters of the fidelity: null at the classification, null rate without fact.
    assert [
        (line["facts_checked"], line["facts_found"], line["fidelity_rate"])
        for line in lines
    ] == [(None, None, None), (1, 1, 1.0), (1, 1, 1.0), (0, 0, None)]
    for line in lines:
        assert (line["outcome"], line["http_status"], line["attempts"]) == (
            "ok",
            200,
            1,
        )
        assert line["ts"].endswith("Z")
        assert line["ts"] == datetime.fromisoformat(line["ts"]).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
    # Neither the text of a claim nor a title.
    assert "CVE-2026-86950" not in output
    assert "Apple" not in output


def test_the_cost_is_the_tokens_at_the_prices_of_the_config(make_ai_api, capsys):
    client, _ = make_ai_api(VALID_STEPS)

    post_digests(client)

    expected = round(
        (
            TOKENS_IN * LLM_INPUT_USD_PER_MILLION_TOKENS
            + TOKENS_OUT * LLM_OUTPUT_USD_PER_MILLION_TOKENS
        )
        / 1_000_000,
        6,
    )
    assert {line["cost_estimated"] for line in read_metric_lines(capsys)} == {expected}
    assert expected == 0.00024


def test_the_fidelity_rate_is_the_facts_found_over_the_facts_checked(
    make_ai_api, capsys
):
    # v2.1.287 is the tag of release 6; v2.1.299 is in no item.
    two_versions = {
        "text": "Claude Code publie v2.1.287 puis v2.1.299.",
        "item_ids": ["i6"],
        "facts": [
            {"value": "v2.1.287", "item_id": "i6"},
            {"value": "v2.1.299", "item_id": "i6"},
        ],
    }
    client, _ = make_ai_api(
        [
            classified(),
            written(SECURITY_CLAIM),
            written(AGENT_CLAIM, two_versions),
            written(),
        ]
    )

    post_digests(client)

    agent = read_metric_lines(capsys)[2]
    assert (agent["subject"], agent["facts_checked"], agent["facts_found"]) == (
        "agent_tools",
        3,
        2,
    )
    assert agent["fidelity_rate"] == 0.6667
    assert agent["claims_removed"] == 1


# The attributes a measure may carry: closed values, never a text, a subject nor an id of request
# (.claude/rules/poc.md, rule 5).
METRIC_ATTRIBUTES = {
    "gen_ai.operation.name",
    "gen_ai.provider.name",
    "gen_ai.request.model",
    "gen_ai.token.modality",
    "error.type",
    "k_b.stage",
    "k_b.outcome",
    "k_b.schema_valid",
}


def read_metric_points(metric_reader):
    """The points of each instrument by name: (attributes, value), the sum of a counter or the
    (count, sum) of a histogram."""
    metrics_data = metric_reader.get_metrics_data()
    points = {}
    for resource_metrics in metrics_data.resource_metrics:
        for scope_metrics in resource_metrics.scope_metrics:
            for metric in scope_metrics.metrics:
                points[metric.name] = [
                    (
                        dict(point.attributes),
                        (point.count, point.sum)
                        if hasattr(point, "count")
                        else point.value,
                    )
                    for point in metric.data.data_points
                ]
    return points


def find_point(points, name, **attributes):
    """The value of the point of the instrument whose attributes hold these ones."""
    [value] = [
        value
        for point_attributes, value in points[name]
        if attributes.items() <= point_attributes.items()
    ]
    return value


def test_each_call_is_measured_in_opentelemetry_as_on_its_line(
    make_ai_api, capsys, metric_reader
):
    client, _ = make_ai_api(VALID_STEPS)

    post_digests(client)

    lines = read_metric_lines(capsys)
    points = read_metric_points(metric_reader)
    classify, *writes = lines
    common = {
        "gen_ai.operation.name": "chat",
        "gen_ai.provider.name": "replay",
        "gen_ai.request.model": "replay",
    }
    assert find_point(
        points,
        "gen_ai.client.inference.duration",
        **common,
        **{"k_b.stage": "classify"},
    ) == (1, classify["llm_ms"] / 1000)
    assert find_point(
        points, "gen_ai.client.inference.duration", **{"k_b.stage": "write"}
    ) == (3, sum(line["llm_ms"] for line in writes) / 1000)
    assert find_point(points, "k_b.llm.check.duration", **{"k_b.stage": "write"}) == (
        3,
        sum(line["check_ms"] for line in writes) / 1000,
    )
    assert find_point(
        points,
        "gen_ai.client.inference.usage.input_tokens",
        **{"k_b.stage": "write", "gen_ai.token.modality": "text"},
    ) == sum(line["tokens_in"] for line in writes)
    assert find_point(
        points, "gen_ai.client.inference.usage.output_tokens", **{"k_b.stage": "write"}
    ) == sum(line["tokens_out"] for line in writes)
    assert (
        find_point(
            points,
            "k_b.llm.calls",
            **{"k_b.stage": "write", "k_b.outcome": "ok", "k_b.schema_valid": True},
        )
        == 3
    )
    assert find_point(points, "k_b.llm.cost", **{"k_b.stage": "write"}) == (
        pytest.approx(sum(line["cost_estimated"] for line in writes))
    )
    assert find_point(points, "k_b.digest.claims_removed") == sum(
        line["claims_removed"] for line in writes
    )
    assert find_point(points, "k_b.digest.items_removed_by_checks") == sum(
        line["items_removed_by_checks"] for line in writes
    )
    assert (
        find_point(points, "k_b.digest.items_with_subject")
        == classify["items_with_subject"]
    )
    assert (
        find_point(points, "k_b.digest.items_no_suitable_subject")
        == classify["items_no_suitable_subject"]
    )
    assert (
        find_point(points, "k_b.digest.items_unanswered")
        == classify["items_unanswered"]
    )
    assert find_point(points, "k_b.digest.facts_checked") == sum(
        line["facts_checked"] for line in writes
    )
    assert find_point(points, "k_b.digest.facts_found") == sum(
        line["facts_found"] for line in writes
    )
    assert find_point(points, "k_b.digest.data_fetch.duration") == (
        1,
        classify["data_fetch_ms"] / 1000,
    )


def test_a_measure_carries_closed_attributes_never_a_text(make_ai_api, metric_reader):
    client, _ = make_ai_api(VALID_STEPS)

    response = post_digests(client)

    points = read_metric_points(metric_reader)
    values = {
        str(value)
        for name in points
        for attributes, _ in points[name]
        for value in attributes.values()
    }
    keys = {
        key for name in points for attributes, _ in points[name] for key in attributes
    }
    assert keys <= METRIC_ATTRIBUTES
    assert values <= {"chat", "replay", "classify", "write", "ok", "True", "text"}
    assert response.json()["request_id"] not in values


def test_a_call_checked_then_retried_without_answer_measures_its_check(
    make_ai_api, metric_reader
):
    client, _ = make_ai_api(
        [
            classified(),
            completion("not JSON"),
            {"kind": "timeout"},
            written(AGENT_CLAIM),
            written(),
        ]
    )

    post_digests(client)

    points = read_metric_points(metric_reader)
    assert (
        find_point(points, "k_b.llm.check.duration", **{"k_b.stage": "write"})[0] == 3
    )


def test_a_call_without_answer_measures_no_check(make_ai_api, metric_reader):
    client, _ = make_ai_api(
        [classified(), {"kind": "timeout"}, written(AGENT_CLAIM), written()]
    )

    post_digests(client)

    points = read_metric_points(metric_reader)
    assert (
        find_point(points, "k_b.llm.check.duration", **{"k_b.stage": "write"})[0] == 2
    )
    assert (
        find_point(
            points, "gen_ai.client.inference.duration", **{"error.type": "llm_timeout"}
        )[0]
        == 1
    )


def test_a_call_that_fails_carries_its_outcome_as_error_type(
    make_ai_api, metric_reader
):
    client, _ = make_ai_api([INVALID_OUTPUT, INVALID_OUTPUT])

    post_digests(client)

    points = read_metric_points(metric_reader)
    [(attributes, _)] = points["gen_ai.client.inference.duration"]
    assert attributes["error.type"] == "llm_invalid_output"
    assert (
        find_point(
            points,
            "k_b.llm.calls",
            **{"k_b.outcome": "llm_invalid_output", "k_b.schema_valid": False},
        )
        == 1
    )


def test_an_empty_window_measures_its_reading_without_a_call(
    make_ai_api, metric_reader
):
    client, _ = make_ai_api([])
    client.app.dependency_overrides[read_request_time] = lambda: datetime(
        2030, 1, 1, tzinfo=UTC
    )

    post_digests(client)

    points = read_metric_points(metric_reader)
    assert set(points) == {"k_b.digest.data_fetch.duration"}
    [(attributes, (count, _))] = points["k_b.digest.data_fetch.duration"]
    assert (attributes, count) == ({}, 1)


class StoppedTraceExport:
    """An export of the traces that sends nothing, and says when each signal is stopped."""

    def __init__(self, stop):
        self.stop = stop

    def build_tracing(self):
        return TRACING_OFF

    def stop_traces(self):
        self.stop("traces")

    def stop_logs(self):
        self.stop("logs")


def test_the_measures_the_spans_and_the_logs_are_sent_at_once_when_the_app_stops():
    # Each stop waits for the two others: run one after the other, the first would wait alone
    # and break the barrier.
    barrier = threading.Barrier(3, timeout=5)
    stopped = []

    def stop(signal):
        barrier.wait()
        stopped.append(signal)

    model_metrics = ModelMetrics(
        MeterProvider(metric_readers=[InMemoryMetricReader()], shutdown_on_exit=False)
    )
    model_metrics.stop = lambda: stop("metrics")
    app = create_app(
        {"TERMINAL": SecretStr(CLIENT_KEY)},
        REPLAY_SETTINGS,
        DataApiSettings(
            base_url="http://localhost", api_key=SecretStr(secrets.token_urlsafe(32))
        ),
        TEST_SUBJECTS,
        WINDOW_DAYS,
        model_metrics,
        StoppedTraceExport(stop),
    )

    with TestClient(app):
        assert stopped == []

    assert sorted(stopped) == ["logs", "metrics", "traces"]


class RecordingOutput:
    """A stdout that keeps what is written and when it is flushed."""

    def __init__(self):
        self.events = []

    def write(self, text):
        self.events.append(("write", text))
        return len(text)

    def flush(self):
        self.events.append(("flush", None))


def test_each_metric_line_is_flushed_at_once(make_ai_api, monkeypatch):
    """On SIGTERM, uvicorn sends the signal again: a line left in the buffer of a pipe is lost."""
    client, _ = make_ai_api(VALID_STEPS)
    output = RecordingOutput()
    monkeypatch.setattr(sys, "stdout", output)

    post_digests(client)

    kinds = [kind for kind, _ in output.events]
    assert kinds == ["write", "flush"] * 4
    assert all(text.endswith("\n") for kind, text in output.events if kind == "write")


def test_health_answers_without_key(make_ai_api):
    client, _ = make_ai_api([])

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_only_the_public_routes_answer_without_a_key(make_ai_api):
    client, _ = make_ai_api([])
    routes = {
        (route.path, method)
        for route in iter_route_contexts(client.app.routes)
        for method in route.methods or ()
        if method != "HEAD"
    }

    answered_without_key = {
        path
        for path, method in routes
        if client.request(method, path).status_code != 401
    }

    assert routes == {
        ("/docs", "GET"),
        ("/openapi.json", "GET"),
        ("/health", "GET"),
        ("/digests", "POST"),
    }
    assert answered_without_key == {"/docs", "/openapi.json", "/health"}


def test_a_final_slash_is_an_unknown_route(make_ai_api):
    client, _ = make_ai_api([])

    response = client.post("/digests/", json={}, headers=AUTHORIZATION)

    assert response.status_code == 404
    assert response.json() == {"error": "route_not_found"}


def test_each_state_that_fails_a_request_has_its_status():
    """A state without status would answer 500 in place of a named 502, 503 or 504."""
    failure_states = {
        *get_args(LlmFailure.__value__),
        *get_args(DataApiState.__value__),
    }

    assert set(STATUS_BY_STATE) == failure_states


def describe_tree(recorded_traces):
    """Each span by its name, kind, parent name and state: the shape of the trace."""
    spans = recorded_traces.spans.get_finished_spans()
    name_by_id = {span.context.span_id: span.name for span in spans}
    return [
        (
            span.name,
            span.kind,
            None if span.parent is None else name_by_id[span.parent.span_id],
            span.attributes.get("error.type"),
        )
        for span in sorted(spans, key=lambda span: span.start_time)
    ]


def test_a_request_is_a_trace_of_its_steps(make_ai_api, recorded_traces):
    client, _ = make_ai_api(VALID_STEPS)

    response = post_digests(client)

    assert describe_tree(recorded_traces) == [
        ("POST /digests", SpanKind.SERVER, None, None),
        ("read_watch", SpanKind.INTERNAL, "POST /digests", None),
        ("classify_batch", SpanKind.INTERNAL, "POST /digests", None),
        ("chat replay", SpanKind.CLIENT, "classify_batch", None),
        ("write_subject", SpanKind.INTERNAL, "POST /digests", None),
        ("chat replay", SpanKind.CLIENT, "write_subject", None),
        ("write_subject", SpanKind.INTERNAL, "POST /digests", None),
        ("chat replay", SpanKind.CLIENT, "write_subject", None),
        ("write_subject", SpanKind.INTERNAL, "POST /digests", None),
        ("chat replay", SpanKind.CLIENT, "write_subject", None),
    ]
    [root] = recorded_traces.find_spans("POST /digests")
    assert dict(root.attributes) == {
        "http.request.method": "POST",
        "http.route": "/digests",
        "k_b.request_id": response.headers["X-Request-ID"],
        "http.response.status_code": 200,
    }
    [reading] = recorded_traces.find_spans("read_watch")
    assert dict(reading.attributes) == {
        "k_b.items.read": 10,
        "k_b.items.dropped": 0,
        "k_b.items.capped": False,
    }
    [batch] = recorded_traces.find_spans("classify_batch")
    assert batch.attributes["k_b.batch.index"] == 0
    assert batch.attributes["k_b.outcome"] == "ok"
    assert list(batch.attributes["k_b.items.no_suitable_subject.ids"]) == [12, 11, 13]
    assert list(batch.attributes["k_b.items.unanswered.ids"]) == []
    security = recorded_traces.find_spans("write_subject")[0]
    assert security.attributes["k_b.subject"] == "security_alerts"
    assert list(security.attributes["k_b.items.sent.ids"]) == SECURITY_ITEMS
    assert list(security.attributes["k_b.items.not_retained.ids"]) == [4]
    assert security.attributes["k_b.claims.removed"] == 0


def test_a_subject_that_fails_or_is_skipped_is_a_span_in_error(
    make_ai_api, recorded_traces
):
    # A Retry-After beyond the maximum: no retry, and nothing more is sent.
    client, _ = make_ai_api(
        [classified(), {"kind": "response", "status": 429, "retry_after": "30"}]
    )

    post_digests(client)

    writings = recorded_traces.find_spans("write_subject")
    assert [span.attributes["error.type"] for span in writings] == [
        "llm_rate_limited",
        "llm_rate_limited",
        "llm_rate_limited",
    ]
    assert all(span.status.status_code is StatusCode.ERROR for span in writings)
    # The first sent its request; the next two, skipped, sent none.
    tree = describe_tree(recorded_traces)
    assert [parent for name, _, parent, _ in tree if name == "chat replay"] == [
        "classify_batch",
        "write_subject",
    ]


def test_a_request_that_fails_names_its_state_and_its_status_on_the_root(
    make_ai_api, recorded_traces
):
    client, _ = make_ai_api([INVALID_OUTPUT, INVALID_OUTPUT])

    response = post_digests(client)

    [root] = recorded_traces.find_spans("POST /digests")
    assert root.status.status_code is StatusCode.ERROR
    assert root.status.description is None
    assert root.attributes["error.type"] == "llm_invalid_output"
    assert root.attributes["http.response.status_code"] == response.status_code == 502
    [batch] = recorded_traces.find_spans("classify_batch")
    assert batch.attributes["error.type"] == "llm_invalid_output"


class TrappedError(Exception):
    """An unexpected error whose text quotes an item."""


def test_an_unexpected_error_names_its_type_never_its_text_on_the_root(
    make_ai_api, recorded_traces, monkeypatch
):
    def fail(*args, **kwargs):
        raise TrappedError("Mistral Large 4 (9 minute read)")

    monkeypatch.setattr(digest_router, "build_digests", fail)
    client, _ = make_ai_api(VALID_STEPS)

    response = post_digests(client)

    assert response.status_code == 500
    [root] = recorded_traces.find_spans("POST /digests")
    assert root.attributes["error.type"] == "TrappedError"
    assert root.attributes["http.response.status_code"] == 500
    assert root.status.description is None
    assert all(span.events == () for span in recorded_traces.spans.get_finished_spans())
    assert not any("Mistral" in str(value) for value in list_values(recorded_traces))


CONTENT_EVENT = "gen_ai.client.inference.operation.details"


def list_values(recorded_traces):
    """Every value of every attribute of the spans and log records, and every body."""
    values = []
    for span in recorded_traces.spans.get_finished_spans():
        values.extend(span.attributes.values())
    for record in recorded_traces.log_records.get_finished_logs():
        values.extend(record.log_record.attributes.values())
        values.append(record.log_record.body)
    return values


def test_no_value_holds_a_text_of_the_items_or_of_the_output(
    make_ai_api, recorded_traces
):
    client, _ = make_ai_api(VALID_STEPS)

    body = post_digests(client).json()

    texts = [
        text
        for digest in body["digests"]
        for text in [
            *(claim["text"] for claim in digest["claims"]),
            *(source["title"] for source in digest["sources"]),
        ]
    ]
    assert texts
    words = {word for text in texts for word in text.split() if len(word) > 6}
    for value in list_values(recorded_traces):
        written = " ".join(map(str, value)) if isinstance(value, tuple) else str(value)
        assert not any(word in written for word in words), written
        assert len(written.encode()) <= 2048


def test_with_content_all_only_the_body_of_a_content_record_holds_a_text(
    make_ai_api, recorded_traces, capsys, caplog
):
    # Rule 5 of poc.md and its exception: the texts go in the body of the content records, never
    # in an attribute, another record, a log line nor a metric line.
    client, _ = make_ai_api(VALID_STEPS, trace_content="all")

    with caplog.at_level(logging.DEBUG):
        body = post_digests(client).json()

    words = {
        word
        for digest in body["digests"]
        for text in [
            *(claim["text"] for claim in digest["claims"]),
            *(source["title"] for source in digest["sources"]),
        ]
        for word in text.split()
        if len(word) > 6
    }
    assert words
    content_records = recorded_traces.find_log_records(CONTENT_EVENT)
    assert len(content_records) == 4
    assert all(any(word in record.body for word in words) for record in content_records)
    out, err = capsys.readouterr()
    values = [
        *(
            v
            for s in recorded_traces.spans.get_finished_spans()
            for v in s.attributes.values()
        ),
        *(
            v
            for r in recorded_traces.log_records.get_finished_logs()
            for v in r.log_record.attributes.values()
        ),
        *(
            r.log_record.body
            for r in recorded_traces.log_records.get_finished_logs()
            if r.log_record.event_name != CONTENT_EVENT
        ),
        *(record.getMessage() for record in caplog.records),
        # The values of the metric lines: a key, as prompt_version, is a name of K-B.
        *(v for line in out.splitlines() for v in json.loads(line).values()),
        err,
    ]
    for value in values:
        written = " ".join(map(str, value)) if isinstance(value, tuple) else str(value)
        assert not any(word in written for word in words), written


def test_each_claim_removed_is_an_evaluation_tied_to_its_last_request(
    make_ai_api, recorded_traces
):
    two_versions = {
        "text": "Claude Code publie v2.1.287 puis v2.1.299.",
        "item_ids": ["i6"],
        "facts": [
            {"value": "v2.1.287", "item_id": "i6"},
            {"value": "v2.1.299", "item_id": "i6"},
        ],
    }
    client, _ = make_ai_api(
        [
            classified(),
            written(SECURITY_CLAIM),
            written(AGENT_CLAIM, two_versions),
            written(),
        ]
    )

    post_digests(client)

    [record] = recorded_traces.log_records.get_finished_logs()
    log_record = record.log_record
    agent_chat = [
        span
        for span in recorded_traces.find_spans("chat replay")
        if span.attributes["gen_ai.prompt.name"] == "write"
    ][1]
    assert log_record.span_id == agent_chat.context.span_id
    assert log_record.event_name == "gen_ai.evaluation.result"
    assert log_record.attributes["gen_ai.evaluation.explanation"] == (
        "fact_not_found:1:6"
    )
    assert log_record.attributes["k_b.claim.index"] == 1
    assert not any(name.startswith("exception.") for name in log_record.attributes)
    agent = recorded_traces.find_spans("write_subject")[1]
    assert agent.attributes["k_b.claims.removed"] == 1
