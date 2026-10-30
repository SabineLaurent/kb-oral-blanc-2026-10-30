"""GET /items on the test base (tests/fixtures/items_database.sql), whose ids the tests name."""

import pytest

from k_b.data_api.routers import item_router
from k_b.database.reading import ItemPage
from k_b.items import MAX_INT64, MIN_INT64

# The fields of an item of the list (docs/specs-api.md, "Réponse"): nothing else is served.
LISTED_FIELDS = {"id", "kind", "title", "source_date", "url", "withdrawn_at"}


def test_each_kind_is_listed_with_the_fields_of_the_spec(client, authorization):
    response = client.get("/items", params={"limit": 100}, headers=authorization)

    assert response.status_code == 200
    items = response.json()["items"]
    assert {item["kind"] for item in items} == {
        "paper",
        "vulnerability",
        "release",
        "advisory",
        "newsletter_article",
        "zotero_entry",
        "clip",
    }
    paper = next(item for item in items if item["id"] == 1)
    assert paper == {
        "id": 1,
        "kind": "paper",
        "title": "Resource-Optimized and Energy-Aware Agentic AI Framework Anchored on Blockchain for Secure Software Supply Chains",
        "source_date": "2026-09-25T13:53:50Z",
        "url": "https://arxiv.org/abs/2609.31282",
        "withdrawn_at": None,
    }


@pytest.mark.parametrize(
    ("params", "expected_ids"),
    [
        ({"kind": ["paper", "advisory"]}, [2, 1, 9, 10, 17]),
        ({"since": "2026-10-07T00:00:00+02:00"}, [12, 11]),
        ({"q": "blockchain"}, [1]),
        ({"limit": 2}, [12, 11]),
        ({"limit": 2, "offset": 2}, [13, 15]),
    ],
)
def test_kind_since_q_limit_offset_filter_as_the_reading(
    client, authorization, params, expected_ids
):
    response = client.get("/items", params=params, headers=authorization)

    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == expected_ids


@pytest.mark.parametrize(
    ("query", "parameter"),
    [
        ("limit=0", "limit"),
        ("limit=101", "limit"),
        ("offset=-1", "offset"),
        ("offset=10001", "offset"),
        ("q=", "q"),
        ("q=" + "a" * 201, "q"),
        ("q=a%00b", "q"),
        ("kind=vuln", "kind"),
        ("since=2026-10-07T00:00:00", "since"),
        ("since=0001-01-01T00:00:00%2B01:00", "since"),
        ("since=9999-12-31T23:59:59.5Z", "since"),
        # A number is not read as a Unix time: since is an ISO 8601 date with its zone.
        ("since=20261007", "since"),
        ("since=0", "since"),
        ("since=1700000000", "since"),
    ],
)
def test_a_parameter_out_of_bounds_gives_422_with_its_name_only(
    client, authorization, query, parameter
):
    response = client.get(f"/items?{query}", headers=authorization)

    assert response.status_code == 422
    assert response.json() == {"error": "invalid_parameter", "parameters": [parameter]}


@pytest.mark.parametrize(
    ("offset", "limit", "read_next_offset", "expected_next_offset"),
    [(9980, 20, 10_000, 10_000), (10_000, 100, 10_100, None), (0, 20, None, None)],
)
def test_next_offset_stops_at_the_offset_bound(
    client,
    authorization,
    monkeypatch,
    offset,
    limit,
    read_next_offset,
    expected_next_offset,
):
    # A base of 10 000 items is not written for it: the reading gives the next offset.
    monkeypatch.setattr(
        item_router, "read_items", lambda *_, **__: ItemPage([], read_next_offset)
    )

    response = client.get(
        "/items", params={"offset": offset, "limit": limit}, headers=authorization
    )

    assert response.status_code == 200
    assert response.json() == {"items": [], "next_offset": expected_next_offset}


def test_the_list_serves_only_the_six_fields(client, authorization):
    """API3: a whitelist of six fields; highlights, an author, a summary, a raw file path or a
    collection date are never served."""
    response = client.get("/items", params={"limit": 100}, headers=authorization)

    items = response.json()["items"]
    assert 14 in {item["id"] for item in items}
    assert all(set(item) == LISTED_FIELDS for item in items)
    for absent in (
        "A highlighted sentence on the first page.",
        "A comment on it.",
        "Toqeer Ali Syed",
        "This paper proposes",
        "raw_file_path",
        "selected_library/zotero/",
        "automated_watch/",
        "collected_at",
    ):
        assert absent not in response.text


@pytest.mark.parametrize("path", ["/items", "/items/1", "/trends"])
@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_every_data_route_refuses_another_method_with_405(
    client, authorization, path, method
):
    response = client.request(method, path, headers=authorization)

    assert response.status_code == 405
    assert response.json() == {"error": "method_not_allowed"}


# The detail of one item of each kind, whole (docs/specs-api.md, "GET /items/{item_id}"): the
# fields of the list, then those of its kind, and nothing else (API3).
PAPER_2 = {
    "id": 2,
    "kind": "paper",
    "title": "Evaluating System One Models for Agent Security Decisions: Reliability, Calibration, and Selective Automation",
    "source_date": "2026-09-27T09:30:13Z",
    "url": "https://arxiv.org/abs/2609.33401",
    "withdrawn_at": None,
    "arxiv_id": "2609.33401",
    "authors": ["Yixuan Liu"],
    "summary": "Model-based judges support agent security by detecting prompt injections, assessing interaction risks, and screening harmful requests.",
    # publication.revised_at, a date of arXiv: not the source_date.
    "arxiv_updated_at": "2026-09-29T16:41:24Z",
}
VULNERABILITY_4 = {
    "id": 4,
    "kind": "vulnerability",
    "title": "Citrix NetScaler Improper Restriction of Operations within the Bounds of a Memory Buffer Vulnerability",
    "source_date": "2026-09-27T00:00:00Z",
    "url": "https://nvd.nist.gov/vuln/detail/CVE-2026-88772",
    "withdrawn_at": None,
    "cve_id": "CVE-2026-88772",
    "description": "Citrix NetScaler ADC and NetScaler Gateway contain an improper restriction of operations within the bounds of a memory buffer vulnerability that could allow for remote code execution or denial of service",
    "vendor": "Citrix",
    "product": "NetScaler",
    "ransomware_use": "Unknown",
    "revised_at": "2026-10-06T09:30:00Z",
}
RELEASE_6 = {
    "id": 6,
    "kind": "release",
    "title": "anthropics/claude-code Claude Code 2.1.287",
    "source_date": "2026-10-01T18:00:22Z",
    "url": "https://github.com/anthropics/claude-code/releases/tag/v2.1.287",
    "withdrawn_at": None,
    "release_id": 401174143,
    "repository_name": "anthropics/claude-code",
    "tag_name": "v2.1.287",
    "body": "## What's changed\n\n- Added Claude Mods: plugins may now modify deeper behavior\n- Added You should know, a built-in mod where a side agent watches your back and flags things you or Claude might miss. T",
}
ADVISORY_9 = {
    "id": 9,
    "kind": "advisory",
    "title": "LangGraph Checkpoint: Unsafe JSON deserialization in checkpoint loading",
    "source_date": "2026-06-25T18:25:42Z",
    "url": "https://github.com/advisories/GHSA-fjqc-hq36-qh5p",
    "withdrawn_at": None,
    "ghsa_id": "GHSA-fjqc-hq36-qh5p",
    "cve_id": "CVE-2020-29583",
    "exploited": True,
    "packages": ["langgraph-checkpoint"],
}
NEWSLETTER_ARTICLE_11 = {
    "id": 11,
    "kind": "newsletter_article",
    "title": "Mistral Large 4 (9 minute read)",
    "source_date": "2026-10-07T00:00:00Z",
    "url": "https://mistral.ai/news/mistral-large-4/",
    "withdrawn_at": None,
    "edition": "ai",
    "edition_date": "2026-10-07T00:00:00Z",
    "rank": 2,
    "summary": "Mistral Large 4 is a one-trillion-parameter multimodal model positioned as a European alternative to leading closed and open AI systems.",
}
ZOTERO_ENTRY_14 = {
    "id": 14,
    "kind": "zotero_entry",
    "title": "Prompt injection through tool results",
    "source_date": "2026-10-01T08:15:00Z",
    "url": "http://arxiv.org/abs/2609.00001",
    "withdrawn_at": None,
    "zotero_key": "AAAAAAA1",
    "item_type": "preprint",
    "arxiv_id": "2609.00001",
    "revised_at": "2026-10-06T09:30:00Z",
    # By date of creation, the withdrawn one included; the key of a highlight is not served.
    "highlights": [
        {
            "text": "A highlighted sentence on the first page.",
            "comment": "A comment on it.",
            "highlighted_at": "2026-10-05T08:42:30Z",
            "revised_at": None,
            "withdrawn_at": None,
        },
        {
            "text": "A highlighted sentence on the third page.",
            "comment": None,
            "highlighted_at": "2026-10-05T08:50:00Z",
            "revised_at": "2026-10-06T09:30:00Z",
            "withdrawn_at": None,
        },
        {
            "text": "A highlight in the trash.",
            "comment": None,
            "highlighted_at": "2026-10-05T08:56:00Z",
            "revised_at": None,
            "withdrawn_at": "2026-10-06T09:30:00Z",
        },
    ],
}


CLIP_18 = {
    "id": 18,
    "kind": "clip",
    "title": "7 Coding Patterns I Stole From Senior Engineers",
    "source_date": "2026-09-17T00:00:00Z",
    "url": "https://medium.com/skillstuff/7-coding-patterns-i-stole-from-senior-engineers-c95f757e52a6",
    "withdrawn_at": None,
    "published_at": "2026-06-08T00:00:00Z",
    "body": "Most junior developers think senior engineers know more tricks.",
}


@pytest.mark.parametrize(
    "detail",
    [
        PAPER_2,
        VULNERABILITY_4,
        RELEASE_6,
        ADVISORY_9,
        NEWSLETTER_ARTICLE_11,
        ZOTERO_ENTRY_14,
        CLIP_18,
    ],
    ids=lambda detail: detail["kind"],
)
def test_each_kind_has_its_detail(client, authorization, detail):
    response = client.get(f"/items/{detail['id']}", headers=authorization)

    assert response.status_code == 200
    assert response.json() == detail


def test_a_withdrawn_highlight_is_served_with_its_withdrawn_at(client, authorization):
    highlights = client.get("/items/14", headers=authorization).json()["highlights"]

    assert {
        "text": "A highlight in the trash.",
        "comment": None,
        "highlighted_at": "2026-10-05T08:56:00Z",
        "revised_at": None,
        "withdrawn_at": "2026-10-06T09:30:00Z",
    } in highlights


def test_a_withdrawn_vulnerability_is_served_with_its_withdrawn_at(
    client, authorization
):
    detail = client.get("/items/5", headers=authorization).json()

    assert detail["kind"] == "vulnerability"
    assert detail["cve_id"] == "CVE-2020-29583"
    assert detail["withdrawn_at"] == "2026-10-06T09:30:00Z"


@pytest.mark.parametrize(
    ("item_id", "cve_id", "exploited"),
    [
        # In the KEV catalog, withdrawn since: still exploited (docs/specs-api.md).
        (9, "CVE-2020-29583", True),
        (17, "CVE-2026-45134", False),
        (10, None, None),
    ],
    ids=["in-kev", "not-in-kev", "without-cve"],
)
def test_exploited_is_true_false_or_null(
    client, authorization, item_id, cve_id, exploited
):
    detail = client.get(f"/items/{item_id}", headers=authorization).json()

    assert (detail["cve_id"], detail["exploited"]) == (cve_id, exploited)


@pytest.mark.parametrize("item_id", ["999", "0", "-1", str(MAX_INT64), str(MIN_INT64)])
def test_an_unknown_item_id_gives_404_item_not_found(client, authorization, item_id):
    response = client.get(f"/items/{item_id}", headers=authorization)

    assert response.status_code == 404
    assert response.json() == {"error": "item_not_found"}


@pytest.mark.parametrize(
    "item_id",
    [
        "abc",
        "1.5",
        "1.0",
        "%201",
        "1%0A",
        "01",
        "-0",
        "1e3",
        "+1",
        str(MAX_INT64 + 1),
        str(MIN_INT64 - 1),
    ],
)
def test_an_item_id_not_written_as_an_integer_gives_422(client, authorization, item_id):
    response = client.get(f"/items/{item_id}", headers=authorization)

    assert response.status_code == 422
    assert response.json() == {"error": "invalid_parameter", "parameters": ["item_id"]}


def test_no_detail_serves_a_raw_file_path_or_a_collection_date(client, authorization):
    """API3: neither the name nor the value of a raw file path or of a date of collection."""
    for item_id in range(1, 18):
        response = client.get(f"/items/{item_id}", headers=authorization)

        assert response.status_code == 200
        for absent in (
            "raw_file_path",
            "collected_at",
            "automated_watch/",
            "selected_library/",
            "2026-10-05T09:30:00Z",
            "2026-10-11T15:31:30Z",
        ):
            assert absent not in response.text
