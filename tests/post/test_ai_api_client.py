"""The client of the AI API of kb digest: its settings, its request, and the response it accepts,
each 200 made by the schema of the AI API, which ties the client to the contract."""

import json
import secrets
from datetime import UTC, datetime

import httpx2
import pytest
from pydantic import SecretStr

from k_b.ai_api.schemas.digest_schema import (
    ClaimSchema,
    DigestSchema,
    DigestsSchema,
    FailedSubjectSchema,
    SourceSchema,
    WindowSchema,
)
from k_b.core.config import AI_API_READ_TIMEOUT_SECONDS, DIGEST_REQUEST_TIMEOUT_SECONDS
from k_b.post.ai_api_client import (
    AiApiClientSettings,
    AiApiError,
    build_ai_api_client,
    read_ai_api_client_settings_or_refusal,
    request_digests,
)

# Made for the test: never a real key.
TEST_API_KEY = secrets.token_urlsafe(32)
SETTINGS = AiApiClientSettings(
    base_url="http://127.0.0.1:8001", api_key=SecretStr(TEST_API_KEY)
)
# The subjects of the tests, never the real ones, as in tests/ai_api/test_digest_router.py.
SUBJECT_NAMES = ("security_alerts", "agent_tools", "research")
REQUEST_ID = "1b4e28ba-2fa1-4d2e-9b5c-6c8e1f0a7d3e"
OTHER_REQUEST_ID = "0f8fad5b-d9cb-469f-a165-70867728950e"
UNTIL = datetime(2026, 10, 21, 16, 30, tzinfo=UTC)
# Paper 1 and release 6 of the test base (tests/fixtures/items_database.sql), as the data API
# gives them.
PAPER_SOURCE = SourceSchema(
    item_id=1,
    kind="paper",
    title="Resource-Optimized and Energy-Aware Agentic AI Framework Anchored on Blockchain"
    " for Secure Software Supply Chains",
    url="https://arxiv.org/abs/2609.31282",
)
RELEASE_SOURCE = SourceSchema(
    item_id=6,
    kind="release",
    title="Claude Code 2.1.287",
    url="https://github.com/anthropics/claude-code/releases/tag/v2.1.287",
)
AGENT_DIGEST = DigestSchema(
    subject="agent_tools",
    claims=[
        ClaimSchema(text="Claude Code publie la version v2.1.287.", item_ids=[6]),
        ClaimSchema(
            text="Un cadre d'agents ancré sur une blockchain protège la chaîne logicielle.",
            item_ids=[1, 6],
        ),
    ],
    sources=[RELEASE_SOURCE, PAPER_SOURCE],
    items_not_retained_in_output=[7],
    claims_removed=0,
    items_removed_by_checks=[],
)


def make_response(digests=(AGENT_DIGEST,), failed_subjects=(), **changes):
    """A response of POST /digests, made by the schema of the AI API."""
    fields = {
        "request_id": REQUEST_ID,
        "model": "gpt-6-luna",
        "window": WindowSchema(
            days=3,
            since=datetime(2026, 10, 18, 16, 30, tzinfo=UTC),
            until=UNTIL,
            capped_kind=None,
            covered_after=None,
        ),
        "digests": list(digests),
        "failed_subjects": list(failed_subjects),
        "no_suitable_subject": [12],
        "unanswered": [],
        "items_in": 4,
        "items_truncated": 0,
        "items_dropped_before_writing_stage": 0,
        "items_capped": False,
    } | changes
    return DigestsSchema(**fields)


def to_body(response):
    return response.model_dump_json().encode()


def simulate(handle):
    """The client of the settings, on a transport that keeps each request."""
    requests = []

    def keep_and_handle(request):
        requests.append(request)
        return handle(request)

    transport = httpx2.MockTransport(keep_and_handle)
    return build_ai_api_client(SETTINGS, transport=transport), requests


def answer(status=200, content=b"", headers=None):
    def handle(request):
        return httpx2.Response(status, content=content, headers=headers)

    return handle


def ask(handle):
    http_client, requests = simulate(handle)
    with http_client:
        return request_digests(http_client, SUBJECT_NAMES), requests


def ask_and_fail(handle):
    with pytest.raises(AiApiError) as raised:
        ask(handle)
    return raised.value


def test_the_settings_take_the_local_ai_api_by_default():
    settings = read_ai_api_client_settings_or_refusal(
        {"KB_AI_API_KEY_KB_DIGEST": TEST_API_KEY}
    )

    assert settings == SETTINGS


def test_the_settings_take_the_url_of_kb_ai_api_url():
    settings = read_ai_api_client_settings_or_refusal(
        {
            "KB_AI_API_KEY_KB_DIGEST": TEST_API_KEY,
            "KB_AI_API_URL": "http://api-ia:8001",
        }
    )

    assert settings.base_url == "http://api-ia:8001"


@pytest.mark.parametrize(
    ("environ", "refusal"),
    [
        ({}, "KB_AI_API_KEY_KB_DIGEST: not set"),
        ({"KB_AI_API_KEY_KB_DIGEST": ""}, "KB_AI_API_KEY_KB_DIGEST: not set"),
        (
            {"KB_AI_API_KEY_KB_DIGEST": "short"},
            "'KB_AI_API_KEY_KB_DIGEST': key of 5 characters, minimum 32",
        ),
        (
            {"KB_AI_API_KEY_KB_DIGEST": TEST_API_KEY, "KB_AI_API_URL": "ftp://api-ia"},
            "KB_AI_API_URL: scheme not http nor https",
        ),
        (
            {
                "KB_AI_API_KEY_KB_DIGEST": TEST_API_KEY,
                "KB_AI_API_URL": f"http://kb:{TEST_API_KEY}@api-ia:8001",
            },
            "KB_AI_API_URL: with user information",
        ),
    ],
)
def test_a_setting_refused_names_its_variable_never_its_value(environ, refusal):
    assert read_ai_api_client_settings_or_refusal(environ) == refusal


def test_a_key_of_a_character_out_of_the_rule_is_refused_without_being_quoted():
    key = TEST_API_KEY[:-1] + "!"

    refusal = read_ai_api_client_settings_or_refusal({"KB_AI_API_KEY_KB_DIGEST": key})

    assert refusal == (
        "'KB_AI_API_KEY_KB_DIGEST': key with a character outside [A-Za-z0-9_-]"
    )


def test_the_request_asks_the_default_window_with_the_key_and_its_timeouts():
    response, requests = ask(answer(content=to_body(make_response())))

    [request] = requests
    assert (request.method, str(request.url)) == (
        "POST",
        "http://127.0.0.1:8001/digests",
    )
    assert json.loads(request.content) == {}
    assert request.headers["Authorization"] == f"Bearer {TEST_API_KEY}"
    assert request.extensions["timeout"] == {
        "connect": 5,
        "read": AI_API_READ_TIMEOUT_SECONDS,
        "write": 10,
        "pool": 5,
    }
    assert response.request_id == REQUEST_ID


def test_the_read_timeout_waits_beyond_the_bound_of_a_request_of_the_ai_api():
    """A request lasts up to DIGEST_REQUEST_TIMEOUT_SECONDS, its last call a little more, and the
    answer comes in one block at the end."""
    assert AI_API_READ_TIMEOUT_SECONDS > DIGEST_REQUEST_TIMEOUT_SECONDS + 20


def test_a_response_gives_its_digests_its_window_and_its_model():
    response, _ = ask(answer(content=to_body(make_response())))

    assert response.model == "gpt-6-luna"
    assert (response.window.days, response.window.until) == (3, UNTIL)
    [digest] = response.digests
    assert digest.subject == "agent_tools"
    assert [claim.item_ids for claim in digest.claims] == [[6], [1, 6]]
    assert [source.url for source in digest.sources] == [
        RELEASE_SOURCE.url,
        PAPER_SOURCE.url,
    ]


@pytest.mark.parametrize(
    ("error", "state"),
    [
        (httpx2.ConnectError("refused"), "ai_api_unavailable"),
        (httpx2.ReadError("reset"), "ai_api_unavailable"),
        (httpx2.ConnectTimeout("connect"), "ai_api_timeout"),
        (httpx2.ReadTimeout("read"), "ai_api_timeout"),
        (httpx2.DecodingError("gzip"), "ai_api_invalid_response"),
    ],
)
def test_a_failed_request_gives_its_state_without_the_url(error, state):
    def raise_error(request):
        raise error

    failure = ask_and_fail(raise_error)

    assert (failure.state, failure.http_status) == (state, None)
    assert str(failure) == state


def test_a_refused_key_is_ai_api_unauthorized_and_never_quoted():
    failure = ask_and_fail(
        answer(
            401,
            b'{"error": "invalid_api_key"}',
            {"X-Request-ID": REQUEST_ID, "WWW-Authenticate": "Bearer"},
        )
    )

    assert failure.state == "ai_api_unauthorized"
    assert str(failure) == (
        f"ai_api_unauthorized, HTTP 401, error invalid_api_key, X-Request-ID {REQUEST_ID}"
    )
    assert TEST_API_KEY not in str(failure)


@pytest.mark.parametrize(
    ("status", "error", "headers", "described"),
    [
        (
            503,
            "llm_rate_limited",
            {"Retry-After": "20", "X-Request-ID": REQUEST_ID},
            f"error llm_rate_limited, X-Request-ID {REQUEST_ID}, Retry-After 20",
        ),
        (
            504,
            "llm_timeout",
            {"X-Request-ID": REQUEST_ID},
            f"error llm_timeout, X-Request-ID {REQUEST_ID}",
        ),
        (
            502,
            "data_api_unavailable",
            {"X-Request-ID": REQUEST_ID},
            f"error data_api_unavailable, X-Request-ID {REQUEST_ID}",
        ),
    ],
)
def test_a_failure_of_the_ai_api_gives_its_state_its_id_and_its_retry_after(
    status, error, headers, described
):
    failure = ask_and_fail(
        answer(status, json.dumps({"error": error}).encode(), headers)
    )

    assert failure.state == "ai_api_failed"
    assert str(failure) == f"ai_api_failed, HTTP {status}, {described}"


def test_a_value_received_out_of_its_rule_is_quoted_cut_and_escaped():
    failure = ask_and_fail(
        answer(
            503,
            json.dumps({"error": "Llm Down\nERROR forged line"}).encode(),
            {
                "X-Request-ID": "not an id",
                "Retry-After": "Fri, 31 Dec 1999 23:59:59 GMT",
            },
        )
    )

    assert str(failure) == (
        "ai_api_failed, HTTP 503, error 'Llm Down\\nERROR forged line',"
        " X-Request-ID 'not an id', Retry-After 'Fri, 31 Dec 1999 23:59:59 GMT'"
    )
    assert "\n" not in str(failure)


@pytest.mark.parametrize(
    ("status", "headers"),
    [
        (307, {"Location": "http://elsewhere.example.invalid/digests"}),
        (404, None),
        (422, None),
    ],
)
def test_another_answer_is_ai_api_invalid_response_and_no_redirection_is_followed(
    status, headers
):
    failure = ask_and_fail(answer(status, b"{}", headers))

    assert (failure.state, failure.http_status) == ("ai_api_invalid_response", status)
    assert "elsewhere" not in str(failure)


def make_body_with(**changes):
    body = json.loads(to_body(make_response()))
    body.update(changes)
    return json.dumps(body).encode()


@pytest.mark.parametrize(
    ("body", "detail"),
    [
        (b"not json", "input (json_invalid)"),
        (make_body_with(model="gpt 6\n---"), "model (value_error)"),
        (make_body_with(request_id="1"), "request_id (value_error)"),
        (make_body_with(items_capped="false"), "items_capped (bool_type)"),
        (
            make_body_with(
                failed_subjects=[
                    {"subject": "research", "stage": "Write", "error": "llm_timeout"}
                ]
            ),
            "failed_subjects.0.stage (value_error)",
        ),
        (
            make_body_with(
                window={"days": 3, "since": "2026-10-18T16:30:00", "until": "x"}
            ),
            "window.since (timezone_aware), 4 errors",
        ),
        (
            make_body_with(
                window={
                    "days": 3,
                    "since": "2026-10-18T16:30:00Z",
                    "until": "2026-10-21T16:30:00Z",
                    "capped_kind": "Vulnerability\n---",
                    "covered_after": "2026-10-19T08:00:00Z",
                }
            ),
            "window.capped_kind (literal_error)",
        ),
        *(
            (
                make_body_with(
                    window={
                        "days": 3,
                        "since": "2026-10-18T16:30:00Z",
                        "until": "2026-10-21T16:30:00Z",
                        "capped_kind": word,
                        "covered_after": "2026-10-19T08:00:00Z",
                    }
                ),
                "window.capped_kind (literal_error)",
            )
            # Words YAML reads as null or a boolean: a sheet cut would look uncut (second code
            # review, finding 6).
            for word in ("null", "true", "yes", "on")
        ),
        (
            make_body_with(
                digests=[
                    AGENT_DIGEST.model_dump(mode="json")
                    | {"claims": [{"text": "Sans source.", "item_ids": []}]}
                ]
            ),
            "digests.0.claims.0.item_ids (too_short)",
        ),
    ],
)
def test_a_200_out_of_the_schema_is_refused_by_its_place_never_its_value(body, detail):
    failure = ask_and_fail(answer(content=body, headers={"X-Request-ID": REQUEST_ID}))

    assert failure.state == "ai_api_invalid_response"
    assert str(failure).startswith(f"ai_api_invalid_response, HTTP 200, {detail}")
    assert str(failure).endswith(f", X-Request-ID {REQUEST_ID}")
    assert "gpt 6" not in str(failure)


def digest_with(**changes):
    return AGENT_DIGEST.model_copy(update=changes)


@pytest.mark.parametrize(
    ("response", "detail"),
    [
        (
            make_response([digest_with(subject="tops")]),
            "digests.0.subject (subject_not_listed)",
        ),
        (
            make_response([AGENT_DIGEST, AGENT_DIGEST]),
            "digests.1.subject (subject_twice)",
        ),
        (
            make_response(
                [AGENT_DIGEST],
                [
                    FailedSubjectSchema(
                        subject="agent_tools", stage="write", error="llm_timeout"
                    )
                ],
            ),
            "failed_subjects.0.subject (subject_twice)",
        ),
        (
            make_response(
                [],
                [
                    FailedSubjectSchema(
                        subject="../tops", stage="write", error="llm_timeout"
                    )
                ],
            ),
            "failed_subjects.0.subject (subject_not_listed)",
        ),
        (
            make_response(
                [digest_with(claims=[ClaimSchema(text="Inventée.", item_ids=[99])])]
            ),
            "digests.0.claims.0.item_ids (item_not_in_sources)",
        ),
        (
            make_response(
                [digest_with(claims=[ClaimSchema(text="Deux fois.", item_ids=[6, 6])])]
            ),
            "digests.0.claims.0.item_ids (item_twice)",
        ),
        (
            make_response(
                [digest_with(sources=[RELEASE_SOURCE, PAPER_SOURCE, RELEASE_SOURCE])]
            ),
            "digests.0.sources (item_twice)",
        ),
    ],
)
def test_a_response_that_breaks_the_closed_lists_is_refused_before_any_sheet(
    response, detail
):
    failure = ask_and_fail(answer(content=to_body(response)))

    assert failure.state == "ai_api_invalid_response"
    assert str(failure) == (
        f"ai_api_invalid_response, HTTP 200, {detail}, request_id {REQUEST_ID}"
    )


def test_a_response_with_failed_subjects_is_accepted():
    response, _ = ask(
        answer(
            content=to_body(
                make_response(
                    [AGENT_DIGEST],
                    [
                        FailedSubjectSchema(
                            subject="research", stage="write", error="llm_timeout"
                        )
                    ],
                    request_id=OTHER_REQUEST_ID,
                )
            )
        )
    )

    assert [
        (failed.subject, failed.stage, failed.error)
        for failed in response.failed_subjects
    ] == [("research", "write", "llm_timeout")]
    assert response.request_id == OTHER_REQUEST_ID
