"""The reading of the watch by the data API: against the real data API on the test base, and a
simulated transport for the answers the base cannot give."""

import json
import logging
import secrets
from datetime import UTC, datetime, tzinfo

import httpx2
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from k_b.ai_api.connectors import data_api_client
from k_b.ai_api.connectors.data_api_client import (
    DataApiError,
    build_data_api_client,
    read_watch,
)
from k_b.ai_api.connectors.data_api_settings import DataApiSettings
from k_b.ai_api.deadline import Deadline
from k_b.ai_api.watch_items import WATCH_KINDS, NewsletterArticleFields, PaperFields
from k_b.core.config import (
    DATA_API_CONNECT_TIMEOUT_SECONDS,
    DATA_API_POOL_TIMEOUT_SECONDS,
    DATA_API_READ_TIMEOUT_SECONDS,
    DATA_API_WRITE_TIMEOUT_SECONDS,
)
from k_b.data_api.app import create_app

# Made for the test: never a real key.
TEST_API_KEY = secrets.token_urlsafe(32)
# Before every date of the test base, the withdrawn vulnerability of 2021 included.
SINCE_ALL = datetime(2020, 1, 1, tzinfo=UTC)
# The items of the whitelist, in the order of the reading: the rare kinds first, each from the
# most recent, the same day by id (12 before 11); item 5, a withdrawn vulnerability, is left
# out; the Zotero entries 14 to 16 are never read.
ITEMS_READ = [3, 4, 9, 10, 17, 2, 1, 6, 7, 8, 12, 11, 13]


def far_deadline():
    """A deadline no test reaches: the timeouts of the client stay those of the settings."""
    return Deadline.after(3600)


@pytest.fixture
def data_api(items_database):
    """The data API on the test base, its client AI_DIGEST holding the test key; on localhost, as
    TrustedHostMiddleware refuses the testserver host of TestClient."""
    client = TestClient(
        create_app(items_database, {"AI_DIGEST": SecretStr(TEST_API_KEY)}),
        base_url="http://localhost",
        headers={"Authorization": f"Bearer {TEST_API_KEY}"},
        follow_redirects=False,
        raise_server_exceptions=False,
    )
    paths = []
    client.event_hooks = {"request": [lambda request: paths.append(request.url.path)]}
    client.paths = paths
    return client


def test_the_reading_gives_the_items_of_the_whitelist_in_the_order_of_the_spec(
    data_api,
):
    reading = read_watch(data_api, SINCE_ALL, deadline=far_deadline())

    assert [item.item_id for item in reading.items] == ITEMS_READ
    assert {item.kind for item in reading.items} == {
        "vulnerability",
        "advisory",
        "paper",
        "release",
        "newsletter_article",
    }
    # Withdrawn vulnerability 5: dropped, counted, its detail never asked.
    assert reading.items_dropped == 1
    assert "/items/5" not in data_api.paths
    assert reading.items_capped is False
    assert reading.capped_kind is None
    assert reading.covered_after is None


def test_a_clip_is_of_the_library_and_never_read_for_the_watch(data_api):
    """A clip is of the library (item 18 of the test base): the reading asks neither its kind nor
    its detail."""
    queries = []
    data_api.event_hooks["request"].append(
        lambda request: queries.append(dict(request.url.params))
    )

    reading = read_watch(data_api, SINCE_ALL, deadline=far_deadline())

    assert "clip" not in WATCH_KINDS
    assert {query.get("kind") for query in queries} <= set(WATCH_KINDS) | {None}
    assert "/items/18" not in data_api.paths
    assert 18 not in [item.item_id for item in reading.items]


def test_an_item_read_keeps_its_source_and_sends_its_whitelist_only(data_api):
    reading = read_watch(data_api, SINCE_ALL, deadline=far_deadline())

    paper = next(item for item in reading.items if item.item_id == 2)
    assert paper.kind == "paper"
    assert paper.source_date == datetime(2026, 9, 27, 9, 30, 13, tzinfo=UTC)
    assert paper.url == "https://arxiv.org/abs/2609.33401"
    assert paper.source_title.startswith("Evaluating System One Models")
    # Neither its authors nor its url: the model never receives them.
    assert isinstance(paper.fields, PaperFields)
    assert set(paper.fields.model_dump()) == {"title", "summary"}
    assert "Yixuan Liu" not in repr(reading)


def test_the_advisory_says_whether_its_cve_was_in_the_kev_catalog(data_api):
    reading = read_watch(data_api, SINCE_ALL, deadline=far_deadline())

    fields = {item.item_id: item.fields.model_dump() for item in reading.items}
    assert fields[9] == {
        "ghsa_id": "GHSA-fjqc-hq36-qh5p",
        "cve_id": "CVE-2020-29583",
        "title": "LangGraph Checkpoint: Unsafe JSON deserialization in checkpoint loading",
        "cve_listed_in_cisa_kev": True,
        "packages": ["langgraph-checkpoint"],
    }
    assert fields[10]["cve_listed_in_cisa_kev"] is None
    assert fields[17]["cve_listed_in_cisa_kev"] is False


def test_the_url_of_a_text_sent_is_masked_but_kept_as_the_source(data_api):
    reading = read_watch(data_api, SINCE_ALL, deadline=far_deadline())

    vulnerability = next(item for item in reading.items if item.item_id == 3)
    assert vulnerability.fields.model_dump() == {
        "cve_id": "CVE-2026-86950",
        "title": "Apple Multiple Products Out-of-Bounds Write Vulnerability",
        "description": (
            "Apple iOS, macOS, and iPadOS contain an out-of-bounds write vulnerability in"
            " CoreGraphics that may lead to arbitrary code execution."
        ),
        "vendor": "Apple",
        "product": "Multiple Products",
        "ransomware_use": "Unknown",
    }


def test_the_reading_follows_the_pages(data_api, monkeypatch):
    monkeypatch.setattr(data_api_client, "LIST_PAGE_SIZE", 2)

    reading = read_watch(data_api, SINCE_ALL, deadline=far_deadline())

    assert [item.item_id for item in reading.items] == ITEMS_READ
    assert data_api.paths.count("/items") == 9


def test_the_reading_is_capped_inside_a_kind(data_api, monkeypatch):
    monkeypatch.setattr(data_api_client, "MAX_ITEMS_PER_DIGEST_REQUEST", 3)

    reading = read_watch(data_api, SINCE_ALL, deadline=far_deadline())

    # Vulnerabilities 3 and 4, then advisory 9; advisory 10 is the first left out.
    assert [item.item_id for item in reading.items] == [3, 4, 9]
    assert reading.items_capped is True
    assert reading.capped_kind == "advisory"
    assert reading.covered_after == datetime(2026, 6, 16, 15, 3, 14, tzinfo=UTC)
    # The kinds after the one capped are not read.
    assert data_api.paths.count("/items") == 2


def test_the_reading_capped_between_two_kinds_names_the_next_one(data_api, monkeypatch):
    monkeypatch.setattr(data_api_client, "MAX_ITEMS_PER_DIGEST_REQUEST", 2)

    reading = read_watch(data_api, SINCE_ALL, deadline=far_deadline())

    assert [item.item_id for item in reading.items] == [3, 4]
    assert reading.capped_kind == "advisory"
    assert reading.covered_after == datetime(2026, 6, 25, 18, 25, 42, tzinfo=UTC)


def test_a_withdrawn_entry_takes_no_place_under_the_cap(data_api, monkeypatch):
    # Vulnerabilities 3, 4 and the withdrawn 5, then 3 advisories: a cap of 5 is reached
    # within the advisories, not by the withdrawn entry.
    monkeypatch.setattr(data_api_client, "MAX_ITEMS_PER_DIGEST_REQUEST", 5)

    reading = read_watch(data_api, SINCE_ALL, deadline=far_deadline())

    assert [item.item_id for item in reading.items] == [3, 4, 9, 10, 17]
    assert reading.items_dropped == 1
    assert reading.capped_kind == "paper"


def test_an_empty_window_asks_no_detail(data_api):
    reading = read_watch(
        data_api, datetime(2030, 1, 1, tzinfo=UTC), deadline=far_deadline()
    )

    assert reading.items == ()
    assert reading.items_dropped == 0
    assert data_api.paths == ["/items"] * 5


def read_since(data_api, since):
    queries = []
    data_api.event_hooks["request"].append(
        lambda request: queries.append(request.url.params)
    )
    read_watch(data_api, since, deadline=far_deadline())
    return queries


@pytest.mark.parametrize(
    ("since", "sent"),
    [
        (datetime(2026, 10, 7, 2, 30, 15, tzinfo=UTC), "2026-10-07T02:30:15Z"),
        (datetime.fromisoformat("2026-10-07T04:30:15+02:00"), "2026-10-07T02:30:15Z"),
        # Rounded up, as the data API does (database/reading.py, format_since): an item dated
        # 02:30:15 is before since, and is not read.
        (datetime(2026, 10, 7, 2, 30, 15, 1, tzinfo=UTC), "2026-10-07T02:30:16Z"),
        # The year 1, written on four digits whatever the C library.
        (datetime(1, 1, 1, tzinfo=UTC), "0001-01-01T00:00:00Z"),
    ],
)
def test_since_is_sent_in_utc_to_the_second_rounded_up(data_api, since, sent):
    queries = read_since(data_api, since)

    assert queries[0]["since"] == sent
    assert queries[0]["kind"] == "vulnerability"
    assert queries[0]["limit"] == "100"


class NoOffset(tzinfo):
    """A time zone without offset: the date stays naive."""

    def utcoffset(self, moment):
        return None


@pytest.mark.parametrize(
    "since", [datetime(2026, 10, 7), datetime(2026, 10, 7, tzinfo=NoOffset())]
)
def test_a_since_without_time_zone_is_refused(data_api, since):
    with pytest.raises(ValueError, match="since without time zone"):
        read_watch(data_api, since, deadline=far_deadline())


def test_a_key_refused_gives_data_api_unauthorized(items_database):
    client = TestClient(
        create_app(items_database, {"AI_DIGEST": SecretStr(TEST_API_KEY)}),
        base_url="http://localhost",
        headers={"Authorization": f"Bearer {secrets.token_urlsafe(32)}"},
    )

    with pytest.raises(DataApiError) as raised:
        read_watch(client, SINCE_ALL, deadline=far_deadline())

    assert raised.value.state == "data_api_unauthorized"
    assert raised.value.http_status == 401


# The answers the test base cannot give: a simulated data API.

LISTED_PAPER = {
    "id": 2,
    "kind": "paper",
    "title": "Evaluating System One Models",
    "source_date": "2026-09-27T09:30:13Z",
    "url": "https://arxiv.org/abs/2609.33401",
    "withdrawn_at": None,
}
PAPER_DETAIL = LISTED_PAPER | {
    "arxiv_id": "2609.33401",
    "authors": ["Yixuan Liu"],
    "summary": "Model-based judges support agent security.",
    "arxiv_updated_at": "2026-09-29T16:41:24Z",
}
EMPTY_PAGE = {"items": [], "next_offset": None}


def answer(status=200, body=None, headers=None):
    content = json.dumps(body).encode() if body is not None else b""
    return httpx2.Response(status, content=content, headers=headers)


def simulate(answer_detail=None, answer_list=None):
    """A data API that lists paper 2 alone and answers its detail with answer_detail, keeping
    every request."""
    requests = []

    def handle(request):
        requests.append(request)
        if request.url.path != "/items":
            if answer_detail is None:
                return answer(body=PAPER_DETAIL)
            return answer_detail(request)
        if answer_list is not None:
            return answer_list(request)
        if request.url.params["kind"] == "paper":
            return answer(body={"items": [LISTED_PAPER], "next_offset": None})
        return answer(body=EMPTY_PAGE)

    transport = httpx2.MockTransport(handle)
    settings = DataApiSettings(
        base_url="http://api-donnees:8000", api_key=SecretStr(TEST_API_KEY)
    )
    return build_data_api_client(settings, transport=transport), requests


def raise_error(error):
    def handle(request):
        raise error

    return handle


def test_the_client_of_the_settings_sends_the_key_with_its_timeouts():
    http_client, requests = simulate()

    with http_client:
        reading = read_watch(http_client, SINCE_ALL, deadline=far_deadline())

    assert [item.item_id for item in reading.items] == [2]
    assert str(requests[0].url).startswith("http://api-donnees:8000/items?")
    assert requests[0].headers["Authorization"] == f"Bearer {TEST_API_KEY}"
    assert requests[-1].url.path == "/items/2"
    timeout = requests[0].extensions["timeout"]
    assert (timeout["connect"], timeout["read"], timeout["write"], timeout["pool"]) == (
        DATA_API_CONNECT_TIMEOUT_SECONDS,
        DATA_API_READ_TIMEOUT_SECONDS,
        DATA_API_WRITE_TIMEOUT_SECONDS,
        DATA_API_POOL_TIMEOUT_SECONDS,
    )


def test_an_item_gone_between_the_list_and_the_detail_is_skipped_and_counted():
    http_client, _ = simulate(
        answer_detail=lambda request: answer(404, {"error": "item_not_found"})
    )

    reading = read_watch(http_client, SINCE_ALL, deadline=far_deadline())

    assert reading.items == ()
    assert reading.items_dropped == 1


def test_an_item_withdrawn_between_the_list_and_the_detail_is_skipped_and_counted():
    withdrawn = PAPER_DETAIL | {"withdrawn_at": "2026-10-06T09:30:00Z"}
    http_client, _ = simulate(answer_detail=lambda request: answer(body=withdrawn))

    reading = read_watch(http_client, SINCE_ALL, deadline=far_deadline())

    assert reading.items == ()
    assert reading.items_dropped == 1


def test_an_item_listed_twice_is_read_once():
    def answer_list(request):
        if request.url.params["kind"] != "paper":
            return answer(body=EMPTY_PAGE)
        if request.url.params["offset"] == "0":
            return answer(body={"items": [LISTED_PAPER], "next_offset": 1})
        # An import wrote between the two pages: the same item comes again.
        return answer(body={"items": [LISTED_PAPER], "next_offset": None})

    http_client, requests = simulate(answer_list=answer_list)

    reading = read_watch(http_client, SINCE_ALL, deadline=far_deadline())

    assert [item.item_id for item in reading.items] == [2]
    assert [r.url.path for r in requests].count("/items/2") == 1


@pytest.mark.parametrize(
    ("error", "state"),
    [
        (httpx2.ConnectError("refused"), "data_api_unavailable"),
        (httpx2.ReadError("reset"), "data_api_unavailable"),
        (httpx2.ConnectTimeout("connect"), "data_api_timeout"),
        (httpx2.ReadTimeout("read"), "data_api_timeout"),
        (httpx2.DecodingError("gzip"), "data_api_invalid_response"),
    ],
)
def test_a_failed_request_gives_its_state(error, state):
    http_client, _ = simulate(answer_list=raise_error(error))

    with pytest.raises(DataApiError) as raised:
        read_watch(http_client, SINCE_ALL, deadline=far_deadline())

    assert raised.value.state == state
    assert raised.value.http_status is None


@pytest.mark.parametrize(
    ("status", "body", "headers", "state"),
    [
        (500, {"error": "database_error"}, None, "data_api_unavailable"),
        (503, {"error": "database_missing"}, None, "data_api_unavailable"),
        (401, {"error": "invalid_api_key"}, None, "data_api_unauthorized"),
        (422, {"error": "invalid_parameter"}, None, "data_api_invalid_response"),
        (404, {"error": "route_not_found"}, None, "data_api_invalid_response"),
        (
            307,
            None,
            {"Location": "http://elsewhere.example/items"},
            "data_api_invalid_response",
        ),
    ],
)
def test_an_answer_of_the_list_other_than_200_gives_its_state(
    status, body, headers, state
):
    http_client, requests = simulate(
        answer_list=lambda request: answer(status, body, headers)
    )

    with pytest.raises(DataApiError) as raised:
        read_watch(http_client, SINCE_ALL, deadline=far_deadline())

    assert raised.value.state == state
    assert raised.value.http_status == status
    # No redirection followed.
    assert len(requests) == 1


def test_the_400_of_a_host_refused_gives_data_api_invalid_response():
    http_client, _ = simulate(
        answer_list=lambda request: httpx2.Response(400, text="Invalid host header")
    )

    with pytest.raises(DataApiError) as raised:
        read_watch(http_client, SINCE_ALL, deadline=far_deadline())

    assert raised.value.state == "data_api_invalid_response"
    assert raised.value.http_status == 400


@pytest.mark.parametrize(
    "content",
    [
        b"not json",
        json.dumps({"items": [LISTED_PAPER]}).encode(),
        json.dumps(
            {"items": [LISTED_PAPER | {"id": "2"}], "next_offset": None}
        ).encode(),
        json.dumps(
            {
                "items": [LISTED_PAPER | {"source_date": "2026-09-27"}],
                "next_offset": None,
            }
        ).encode(),
        # A page that goes back would loop forever.
        json.dumps({"items": [], "next_offset": 0}).encode(),
    ],
)
def test_a_list_out_of_its_schema_gives_data_api_invalid_response(content):
    http_client, _ = simulate(
        answer_list=lambda request: httpx2.Response(200, content=content)
    )

    with pytest.raises(DataApiError) as raised:
        read_watch(http_client, SINCE_ALL, deadline=far_deadline())

    assert raised.value.state == "data_api_invalid_response"


@pytest.mark.parametrize("kind", ["zotero_entry", "clip", "advisory"])
def test_a_kind_other_than_the_one_asked_gives_data_api_invalid_response(kind):
    http_client, _ = simulate(
        answer_list=lambda request: answer(
            body={"items": [LISTED_PAPER | {"kind": kind}], "next_offset": None}
        )
    )

    with pytest.raises(DataApiError) as raised:
        read_watch(http_client, SINCE_ALL, deadline=far_deadline())

    assert raised.value.state == "data_api_invalid_response"


@pytest.mark.parametrize(
    "detail",
    [
        PAPER_DETAIL | {"id": 3},
        PAPER_DETAIL
        | {
            "kind": "newsletter_article",
            "edition": "ai",
            "rank": 1,
            "edition_date": "2026-09-27T00:00:00Z",
        },
        {key: value for key, value in PAPER_DETAIL.items() if key != "summary"},
    ],
)
def test_a_detail_other_than_the_item_listed_gives_data_api_invalid_response(detail):
    http_client, _ = simulate(answer_detail=lambda request: answer(body=detail))

    with pytest.raises(DataApiError) as raised:
        read_watch(http_client, SINCE_ALL, deadline=far_deadline())

    assert raised.value.state == "data_api_invalid_response"


@pytest.mark.parametrize(
    ("status", "body", "state"),
    [
        (404, {"error": "route_not_found"}, "data_api_invalid_response"),
        (404, None, "data_api_invalid_response"),
        (503, {"error": "database_missing"}, "data_api_unavailable"),
    ],
)
def test_a_detail_that_fails_gives_its_state(status, body, state):
    http_client, _ = simulate(answer_detail=lambda request: answer(status, body))

    with pytest.raises(DataApiError) as raised:
        read_watch(http_client, SINCE_ALL, deadline=far_deadline())

    assert raised.value.state == state
    assert raised.value.http_status == status


def test_the_error_carries_neither_the_key_nor_what_was_received(caplog):
    caplog.set_level(logging.DEBUG)
    http_client, _ = simulate(
        answer_list=lambda request: answer(
            307, None, {"Location": f"http://elsewhere.example/?k={TEST_API_KEY}"}
        )
    )

    with pytest.raises(DataApiError) as raised:
        read_watch(http_client, SINCE_ALL, deadline=far_deadline())

    assert str(raised.value) == "data_api_invalid_response, HTTP 307"
    assert TEST_API_KEY not in repr(raised.value)
    assert raised.value.__cause__ is None
    assert TEST_API_KEY not in caplog.text


def test_the_reading_never_logs(data_api, caplog):
    caplog.set_level(logging.DEBUG, logger="k_b")

    read_watch(data_api, SINCE_ALL, deadline=far_deadline())

    assert [r for r in caplog.records if r.name.startswith("k_b.ai_api")] == []


def test_a_newsletter_article_read_is_ready_for_the_writing(data_api):
    reading = read_watch(data_api, SINCE_ALL, deadline=far_deadline())

    article = next(item for item in reading.items if item.item_id == 13)
    assert article.fields == NewsletterArticleFields(
        edition="dev",
        title="Write your Own Virtual Machine (35 minute read)",
        summary=(
            "This literate tutorial builds an LC-3 virtual machine in about 250 lines of C,"
            " covering registers, memory, opcodes, traps, loading programs, and"
            " platform-specific input."
        ),
    )


# The bound of a body (MAX_DATA_API_BODY_BYTES), reduced for the tests.

PAGE_OF_PAPER = json.dumps({"items": [LISTED_PAPER], "next_offset": None}).encode()


def bound_bodies(monkeypatch, max_bytes):
    monkeypatch.setattr(data_api_client, "MAX_DATA_API_BODY_BYTES", max_bytes)


def test_a_body_up_to_the_bound_is_read(monkeypatch):
    bound_bodies(monkeypatch, max(len(PAGE_OF_PAPER), len(json.dumps(PAPER_DETAIL))))
    http_client, _ = simulate()

    reading = read_watch(http_client, SINCE_ALL, deadline=far_deadline())

    assert [item.item_id for item in reading.items] == [2]


def test_a_list_above_the_bound_gives_data_api_invalid_response(monkeypatch):
    bound_bodies(monkeypatch, len(PAGE_OF_PAPER) - 1)
    http_client, _ = simulate()

    with pytest.raises(DataApiError) as raised:
        read_watch(http_client, SINCE_ALL, deadline=far_deadline())

    assert raised.value.state == "data_api_invalid_response"
    assert raised.value.http_status == 200
    assert str(raised.value) == "data_api_invalid_response, HTTP 200"


def test_a_detail_above_the_bound_gives_data_api_invalid_response(monkeypatch):
    bound_bodies(monkeypatch, len(PAGE_OF_PAPER))
    long_detail = PAPER_DETAIL | {"summary": "a" * len(PAGE_OF_PAPER)}
    http_client, _ = simulate(answer_detail=lambda request: answer(body=long_detail))

    with pytest.raises(DataApiError) as raised:
        read_watch(http_client, SINCE_ALL, deadline=far_deadline())

    assert raised.value.state == "data_api_invalid_response"
    assert raised.value.http_status == 200


def test_an_item_not_found_is_still_read_under_the_bound(monkeypatch):
    bound_bodies(monkeypatch, len(PAGE_OF_PAPER))
    http_client, _ = simulate(
        answer_detail=lambda request: answer(404, {"error": "item_not_found"})
    )

    reading = read_watch(http_client, SINCE_ALL, deadline=far_deadline())

    assert reading.items_dropped == 1


class UnreadStream(httpx2.SyncByteStream):
    """A body that fails the test if it is read."""

    def __iter__(self):
        raise AssertionError("the body was read")


def test_a_content_length_above_the_bound_is_refused_before_reading(monkeypatch):
    bound_bodies(monkeypatch, 1_000)
    http_client, _ = simulate(
        answer_list=lambda request: httpx2.Response(
            200, headers={"Content-Length": "1001"}, stream=UnreadStream()
        )
    )

    with pytest.raises(DataApiError) as raised:
        read_watch(http_client, SINCE_ALL, deadline=far_deadline())

    assert raised.value.state == "data_api_invalid_response"
    assert raised.value.http_status == 200


class Clock:
    """A clock set by the test: never the time of the machine."""

    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now


def test_a_deadline_passed_stops_the_reading_before_any_request():
    clock = Clock()
    deadline = Deadline.after(5, clock)
    clock.now += 5
    http_client, requests = simulate()

    with http_client, pytest.raises(DataApiError) as raised:
        read_watch(http_client, SINCE_ALL, deadline=deadline)

    assert raised.value.state == "data_api_timeout"
    assert requests == []


def test_the_timeouts_of_a_request_are_shortened_to_the_time_left():
    deadline = Deadline.after(4, Clock())
    http_client, requests = simulate()

    with http_client:
        read_watch(http_client, SINCE_ALL, deadline=deadline)

    timeout = requests[0].extensions["timeout"]
    assert (timeout["connect"], timeout["read"], timeout["write"], timeout["pool"]) == (
        4,
        4,
        4,
        4,
    )


def test_a_deadline_passed_while_an_answer_is_read_stops_the_reading():
    clock = Clock()
    deadline = Deadline.after(60, clock)

    def answer_late(request):
        clock.now += 60
        return answer(body=PAPER_DETAIL)

    http_client, _ = simulate(answer_detail=answer_late)

    with http_client, pytest.raises(DataApiError) as raised:
        read_watch(http_client, SINCE_ALL, deadline=deadline)

    assert raised.value.state == "data_api_timeout"
