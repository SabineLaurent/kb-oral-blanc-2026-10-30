"""The reading of the items and the highlights on the test base
(tests/fixtures/items_database.sql), whose ids the tests name."""

import typing
from datetime import UTC, date, datetime, timedelta, timezone, tzinfo

import pytest

from k_b.database.connection import connect_database, connect_database_read_only
from k_b.database.creation import create_database
from k_b.database.reading import (
    AdvisoryDetail,
    ClipDetail,
    ItemDetail,
    KindDetail,
    ListedItem,
    StoredHighlight,
    StoredTrend,
    TrendWindow,
    read_highlights,
    read_item_detail,
    read_items,
    read_trends,
)
from k_b.items import MAX_INT64, MIN_INT64, ItemKind

# Every item of the test base, in the order of the reading: source_date, then id, descending.
ALL_IDS = [12, 11, 13, 15, 6, 14, 7, 8, 3, 2, 4, 1, 16, 18, 9, 10, 17, 5]
PLUS_TWO_HOURS = timezone(timedelta(hours=2))


@pytest.fixture
def connection(items_database):
    connection = connect_database_read_only(items_database)
    yield connection
    connection.close()


def read_ids(connection, **filters):
    return [item.id for item in read_items(connection, limit=100, **filters).items]


def read_item(connection, item_id):
    return next(
        item for item in read_items(connection, limit=100).items if item.id == item_id
    )


@pytest.mark.parametrize(
    "listed_item",
    [
        ListedItem(
            1,
            "paper",
            "Resource-Optimized and Energy-Aware Agentic AI Framework Anchored on Blockchain"
            " for Secure Software Supply Chains",
            "2026-09-25T13:53:50Z",
            "https://arxiv.org/abs/2609.31282",
            None,
        ),
        ListedItem(
            3,
            "vulnerability",
            "Apple Multiple Products Out-of-Bounds Write Vulnerability",
            "2026-09-29T00:00:00Z",
            "https://nvd.nist.gov/vuln/detail/CVE-2026-86950",
            None,
        ),
        ListedItem(
            6,
            "release",
            "anthropics/claude-code Claude Code 2.1.287",
            "2026-10-01T18:00:22Z",
            "https://github.com/anthropics/claude-code/releases/tag/v2.1.287",
            None,
        ),
        ListedItem(
            9,
            "advisory",
            "LangGraph Checkpoint: Unsafe JSON deserialization in checkpoint loading",
            "2026-06-25T18:25:42Z",
            "https://github.com/advisories/GHSA-fjqc-hq36-qh5p",
            None,
        ),
        ListedItem(
            11,
            "newsletter_article",
            "Mistral Large 4 (9 minute read)",
            "2026-10-07T00:00:00Z",
            "https://mistral.ai/news/mistral-large-4/",
            None,
        ),
        ListedItem(
            14,
            "zotero_entry",
            "Prompt injection through tool results",
            "2026-10-01T08:15:00Z",
            "http://arxiv.org/abs/2609.00001",
            None,
        ),
        ListedItem(
            16,
            "zotero_entry",
            "A web page in the trash",
            "2026-09-20T10:00:00Z",
            None,
            None,
        ),
    ],
    ids=lambda listed_item: f"{listed_item.kind}-{listed_item.id}",
)
def test_an_item_of_each_kind_has_its_title_url_and_date(connection, listed_item):
    assert read_item(connection, listed_item.id) == listed_item


@pytest.mark.parametrize(
    ("item_id", "title"),
    [(7, "anthropics/claude-code v2.1.286"), (8, "anthropics/claude-code v2.1.285")],
    ids=["no-name", "empty-name"],
)
def test_a_release_without_a_name_is_titled_by_its_tag(connection, item_id, title):
    assert read_item(connection, item_id).title == title


@pytest.mark.parametrize("kind", typing.get_args(ItemKind))
def test_each_kind_has_its_branch(connection, kind):
    kinds_read = {
        item.kind for item in read_items(connection, limit=100, kinds=[kind]).items
    }

    assert kinds_read == {kind}


def test_without_a_kind_every_kind_is_read(connection):
    kinds_read = {item.kind for item in read_items(connection, limit=100).items}

    assert kinds_read == set(typing.get_args(ItemKind))


def test_a_kind_is_matched_exactly_and_may_be_repeated(connection):
    vulnerabilities = read_items(connection, limit=100, kinds=["vulnerability"]).items
    papers_and_releases = read_items(
        connection, limit=100, kinds=["paper", "release"]
    ).items

    assert {item.kind for item in vulnerabilities} == {"vulnerability"}
    assert {item.kind for item in papers_and_releases} == {"paper", "release"}


def test_since_is_converted_to_utc_before_the_comparison(connection):
    # 2026-10-01T20:00:22+02:00 is the moment of the release 6, 2026-10-01T18:00:22Z: read as
    # written, without its time zone, it would come after it.
    since = datetime(2026, 10, 1, 20, 0, 22, tzinfo=PLUS_TWO_HOURS)

    assert 6 in read_ids(connection, kinds=["release"], since=since)


def test_since_takes_the_day_of_its_time_zone(connection):
    # The example of docs/specs-api.md: 2026-10-07T00:00:00+02:00 takes the edition of
    # 2026-10-07, written at midnight UTC, and not an earlier one.
    since = datetime(2026, 10, 7, tzinfo=PLUS_TWO_HOURS)

    assert read_ids(connection, kinds=["newsletter_article"], since=since) == [12, 11]


def test_since_within_a_second_leaves_out_the_item_of_that_second(connection):
    item_second = datetime(2026, 10, 1, 18, 0, 22, tzinfo=UTC)

    assert 6 in read_ids(connection, since=item_second)
    assert 6 not in read_ids(
        connection, since=item_second + timedelta(milliseconds=500)
    )


class OffsetUnknown(tzinfo):
    """A time zone that gives no offset: Python takes the date as naive."""

    def utcoffset(self, moment):
        return None


@pytest.mark.parametrize(
    "since",
    [
        datetime(2026, 10, 7),
        datetime(2026, 10, 7, tzinfo=OffsetUnknown()),
        datetime(9999, 12, 31, 23, 59, 59, 500_000, tzinfo=UTC),
        datetime(1, 1, 1, tzinfo=PLUS_TWO_HOURS),
    ],
    ids=[
        "naive",
        "offset-unknown",
        "beyond-the-last-second",
        "before-the-first-day-in-utc",
    ],
)
def test_a_since_without_time_zone_or_out_of_range_is_refused(connection, since):
    with pytest.raises(ValueError, match="since"):
        read_items(connection, limit=100, since=since)


@pytest.mark.parametrize("search_text", ["v2%286", "v2_1"])
def test_a_percent_or_an_underscore_is_searched_as_written(connection, search_text):
    # As wildcards, they would find the releases titled v2.1.286 and v2.1.285.
    assert read_ids(connection, search_text=search_text) == []


def test_an_underscore_finds_the_text_that_holds_it(connection):
    assert read_ids(connection, search_text="CODE_DISABLE") == [8]


def test_a_backslash_is_searched_as_written(connection):
    assert read_ids(connection, search_text="\\") == []


def test_an_empty_search_is_refused(connection):
    with pytest.raises(ValueError, match="search_text"):
        read_items(connection, limit=100, search_text="")


@pytest.mark.parametrize(
    "search_text",
    ["\x00", "Vulnerability\x00xyz", "a\udcff"],
    ids=["nul", "nul-inside", "surrogate"],
)
def test_a_search_with_a_nul_or_a_lone_surrogate_is_refused(connection, search_text):
    # SQLite stops a LIKE pattern at a NUL: "\x00" would find every item. sqlite3 cannot encode
    # a lone surrogate, which a terminal argument that is not UTF-8 becomes.
    with pytest.raises(ValueError, match="NUL or a lone surrogate"):
        read_items(connection, limit=100, search_text=search_text)


def test_the_search_reads_the_title_and_the_summary(connection):
    # The title of a release begins with its repository; lifecycle is in a summary only.
    assert read_ids(connection, search_text="claude-code") == [6, 7, 8]
    assert read_ids(connection, search_text="LIFECYCLE") == [1]


def test_the_mode_follows_the_source_of_the_item(connection):
    assert read_ids(connection, mode="library") == [15, 14, 16, 18]
    assert {
        item.kind for item in read_items(connection, limit=100, mode="watch").items
    } == set(typing.get_args(ItemKind)) - {"zotero_entry", "clip"}


def test_the_items_are_sorted_by_date_then_by_id_descending(connection):
    assert read_ids(connection) == ALL_IDS


def test_a_page_gives_the_offset_of_the_next_one(connection):
    first_page = read_items(connection, limit=2)
    second_page = read_items(connection, limit=2, offset=2)

    assert [item.id for item in first_page.items] == ALL_IDS[:2]
    assert first_page.next_offset == 2
    assert [item.id for item in second_page.items] == ALL_IDS[2:4]
    assert second_page.next_offset == 4


def test_the_last_page_has_no_next_offset(connection):
    # Exactly limit items remain: no next page.
    last_page = read_items(connection, limit=4, offset=len(ALL_IDS) - 4)

    assert [item.id for item in last_page.items] == ALL_IDS[-4:]
    assert last_page.next_offset is None


@pytest.mark.parametrize(
    ("limit", "offset", "message"),
    [
        (0, 0, "limit below minimum 1"),
        (-1, 0, "limit below minimum 1"),
        (MAX_INT64, 0, f"limit above maximum {MAX_INT64 - 1}"),
        (10**5000, 0, f"limit above maximum {MAX_INT64 - 1}"),
        (1, -1, "offset below minimum 0"),
        (1, MAX_INT64 + 1, f"offset above maximum {MAX_INT64}"),
    ],
    ids=[
        "zero",
        "negative",
        "limit-beyond-64-bits",
        "limit-of-5001-digits",
        "negative-offset",
        "offset-beyond-64-bits",
    ],
)
def test_a_limit_or_an_offset_out_of_range_is_refused_by_its_bound(
    connection, limit, offset, message
):
    # The message names the bound crossed, never the value received.
    with pytest.raises(ValueError) as refusal:
        read_items(connection, limit=limit, offset=offset)

    assert str(refusal.value) == message


def test_the_withdrawn_rows_are_listed_with_their_date(connection):
    assert read_item(connection, 5).withdrawn_at == "2026-10-06T09:30:00Z"
    assert read_item(connection, 15).withdrawn_at == "2026-10-06T09:30:00Z"


def test_the_highlights_of_an_entry_withdrawn_ones_included(connection):
    assert read_highlights(connection, 14) == [
        StoredHighlight(
            "ANNOTAA2",
            "A highlighted sentence on the first page.",
            "A comment on it.",
            "2026-10-05T08:42:30Z",
            None,
            None,
        ),
        StoredHighlight(
            "ANNOTAA1",
            "A highlighted sentence on the third page.",
            None,
            "2026-10-05T08:50:00Z",
            "2026-10-06T09:30:00Z",
            None,
        ),
        StoredHighlight(
            "ANNOTAA4",
            "A highlight in the trash.",
            None,
            "2026-10-05T08:56:00Z",
            None,
            "2026-10-06T09:30:00Z",
        ),
    ]


def test_an_entry_without_highlight_has_none(connection):
    assert read_highlights(connection, 15) == []


@pytest.mark.parametrize(
    ("item_id", "message"),
    [
        (MAX_INT64 + 1, f"item_id above maximum {MAX_INT64}"),
        (MIN_INT64 - 1, f"item_id below minimum {MIN_INT64}"),
    ],
    ids=["beyond-64-bits", "below-64-bits"],
)
def test_an_item_id_beyond_64_bits_is_refused_by_its_bound(
    connection, item_id, message
):
    with pytest.raises(ValueError) as refusal:
        read_highlights(connection, item_id)

    assert str(refusal.value) == message


def test_the_detail_of_an_item_holds_its_listed_fields(connection):
    detail = read_item_detail(connection, 2)

    assert isinstance(detail, ItemDetail)
    assert detail.listed == read_item(connection, 2)


def test_the_detail_of_a_paper_reads_its_arxiv_date_in_revised_at(connection):
    detail = read_item_detail(connection, 2)

    assert detail is not None
    assert detail.listed.source_date == "2026-09-27T09:30:13Z"
    assert detail.details._asdict() == {
        "arxiv_id": "2609.33401",
        "authors": ["Yixuan Liu"],
        "summary": "Model-based judges support agent security by detecting prompt"
        " injections, assessing interaction risks, and screening harmful requests.",
        "arxiv_updated_at": "2026-09-29T16:41:24Z",
    }


@pytest.mark.parametrize(
    ("item_id", "advisory"),
    [
        (
            9,
            AdvisoryDetail(
                "GHSA-fjqc-hq36-qh5p", "CVE-2020-29583", True, ["langgraph-checkpoint"]
            ),
        ),
        (
            17,
            AdvisoryDetail(
                "GHSA-3644-q5cj-c5c7",
                "CVE-2026-45134",
                False,
                ["langsmith", "langchain-classic", "langchain"],
            ),
        ),
        (
            10,
            AdvisoryDetail(
                "GHSA-gr75-jv2w-4656", None, None, ["langchain", "langchain-anthropic"]
            ),
        ),
    ],
    ids=["in-kev-withdrawn-since", "not-in-kev", "without-cve"],
)
def test_exploited_reads_the_kev_catalog_as_a_boolean(connection, item_id, advisory):
    detail = read_item_detail(connection, item_id)

    assert detail is not None
    assert detail.details == advisory
    assert type(detail.details.exploited) is type(advisory.exploited)


def test_the_detail_of_a_zotero_entry_holds_its_paper_and_its_highlights(connection):
    detail = read_item_detail(connection, 14)

    assert detail is not None
    assert detail.details.arxiv_id == "2609.00001"
    assert detail.details.revised_at == "2026-10-06T09:30:00Z"
    assert detail.details.highlights == read_highlights(connection, 14)


def test_a_zotero_entry_without_arxiv_reference_has_no_arxiv_id(connection):
    detail = read_item_detail(connection, 16)

    assert detail is not None
    assert detail.details.arxiv_id is None


def test_a_clip_is_listed_by_its_day_saved_and_detailed_with_its_text(connection):
    detail = read_item_detail(connection, 18)

    assert detail == ItemDetail(
        ListedItem(
            18,
            "clip",
            "7 Coding Patterns I Stole From Senior Engineers",
            "2026-09-17T00:00:00Z",
            "https://medium.com/skillstuff/"
            "7-coding-patterns-i-stole-from-senior-engineers-c95f757e52a6",
            None,
        ),
        ClipDetail(
            "2026-06-08T00:00:00Z",
            "Most junior developers think senior engineers know more tricks.",
        ),
    )


def test_the_text_of_a_clip_is_searched(connection):
    assert read_ids(connection, search_text="junior developers") == [18]


@pytest.mark.parametrize("item_id", [999, 0, -1, MAX_INT64, MIN_INT64])
def test_an_unknown_item_has_no_detail(connection, item_id):
    assert read_item_detail(connection, item_id) is None


def test_the_detail_of_an_item_id_beyond_64_bits_is_refused_by_its_bound(connection):
    with pytest.raises(ValueError) as refusal:
        read_item_detail(connection, MAX_INT64 + 1)

    assert str(refusal.value) == f"item_id above maximum {MAX_INT64}"


def test_the_detail_leaves_no_transaction_open(connection):
    read_item_detail(connection, 14)

    assert not connection.in_transaction


LATEST_TRENDS = [
    StoredTrend(1209966933, "morluto/rea", 1087, 215),
    StoredTrend(1398080271, "storytold/photocraft", 449, 64),
    StoredTrend(1322250666, "boykopovar/AnyPS5", 318, 17),
    StoredTrend(1035334306, "wwwwwwworld/solana-trading-bot-v3", 0, 70),
    StoredTrend(928981629, "rhtap-perf-test/nodejs-devfile-sample2", 0, 13),
]


def test_without_a_window_the_trends_are_those_of_the_latest(connection):
    assert read_trends(connection) == TrendWindow(date(2026, 10, 10), LATEST_TRENDS)


def test_the_trends_of_a_window_tie_on_stars_and_forks_by_repository_id(connection):
    assert read_trends(connection, date(2026, 10, 9)) == TrendWindow(
        date(2026, 10, 9),
        [
            StoredTrend(1209966933, "morluto/rea", 120, 9),
            StoredTrend(1398080271, "storytold/photocraft", 120, 9),
        ],
    )


def test_an_unknown_window_has_no_trend(connection):
    assert read_trends(connection, date(2026, 1, 1)) is None


def test_a_base_without_trend_has_no_latest_window(tmp_path):
    database_path = tmp_path / "k-b.sqlite"
    create_database(database_path)
    connection = connect_database_read_only(database_path)
    try:
        assert read_trends(connection) is None
    finally:
        connection.close()


@pytest.mark.parametrize(
    "kind_detail", typing.get_args(KindDetail), ids=lambda detail: detail.__name__
)
def test_no_field_of_a_kind_takes_the_name_of_a_field_of_the_list(kind_detail):
    """The response joins both: a field of the kind would replace the one of the list."""
    assert not set(kind_detail._fields) & set(ListedItem._fields)


@pytest.fixture
def base_with_vulnerabilities_on_papers(tmp_path):
    """Papers designated by a vulnerability row too, which the schema does not refuse; paper 1
    has its publication, paper 2 none."""
    database_path = tmp_path / "k-b.sqlite"
    create_database(database_path)
    connection = connect_database(database_path)
    try:
        connection.executescript(
            """
            INSERT INTO raw_data (id, source_id, raw_file_path, stream, collected_at)
            VALUES (1, (SELECT id FROM source WHERE source_code = 'arxiv'), 'a.xml', 'arxiv',
                '2026-10-05T09:30:00Z');
            INSERT INTO item (id, kind, raw_data_id) VALUES (1, 'paper', 1), (2, 'paper', 1);
            INSERT INTO publication (arxiv_id, item_id, title, summary, authors, submitted_at,
                revised_at)
            VALUES ('2609.31282', 1, 'A paper', 'Its summary.', '["A name"]',
                '2026-09-25T13:53:50Z', '2026-09-25T13:53:50Z');
            INSERT INTO vulnerability (cve_id, item_id, name, description, vendor, product,
                date_added, ransomware_use)
            VALUES
                ('CVE-2026-86950', 1, 'A vulnerability', 'Its description.', 'V', 'P',
                    '2026-09-29T00:00:00Z', 'Unknown'),
                ('CVE-2026-88772', 2, 'Another one', 'Its description.', 'V', 'P',
                    '2026-09-27T00:00:00Z', 'Unknown');
            """
        )
    finally:
        connection.close()
    connection = connect_database_read_only(database_path)
    yield connection
    connection.close()


def test_an_item_is_read_by_the_branch_of_its_kind_only(
    base_with_vulnerabilities_on_papers,
):
    connection = base_with_vulnerabilities_on_papers

    listed = read_items(connection, limit=100).items
    detail = read_item_detail(connection, 1)

    assert [(item.id, item.kind, item.title) for item in listed] == [
        (1, "paper", "A paper")
    ]
    assert detail is not None
    assert detail.listed.title == "A paper"
    assert read_item_detail(connection, 2) is None
