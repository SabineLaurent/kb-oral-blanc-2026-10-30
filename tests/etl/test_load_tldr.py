"""The load of the TLDR pages: an item and its newsletter article per article."""

import pytest
from load_helpers import (
    AUTOMATED_WATCH,
    count_rows,
    fetch_rows,
    read_discards,
    read_log,
    registered_paths,
    run_import,
)
from test_transform import FIXTURES_DIR

from k_b.database.creation import create_database

# A reduced extract of the TLDR AI page of 2026-10-07, as collected on 2026-10-11: a sponsored
# entry, three articles, a job offer of TLDR.
TLDR_AI_PAGE = (FIXTURES_DIR / "tldr_ai_extract.html").read_text()


TLDR_FIRST_COLLECTION = "2026-10-11T153130Z"


TLDR_SECOND_COLLECTION = "2026-10-12T153130Z"


MISTRAL_ARTICLE_ID = "ai/2026-10-07#2"


MISTRAL_TITLE = "Mistral Large 4 (9 minute read)"


MISTRAL_SUMMARY = (
    "Mistral Large 4 is a one-trillion-parameter multimodal model positioned as a"
    " European alternative to leading closed and open AI systems."
)


NEWSLETTER_ARTICLE_QUERY = (
    "SELECT edition, edition_date, rank, title, summary, url, publication_id,"
    " item.kind, raw_file_path FROM newsletter_article"
    " JOIN item ON item.id = newsletter_article.item_id"
    " JOIN raw_data ON raw_data.id = item.raw_data_id"
    " ORDER BY edition, edition_date, rank"
)


def write_tldr_page(data_dir, folder, collection, page):
    """The page of 2026-10-07 of an edition folder, in a collection."""
    pages_dir = data_dir / "raw" / AUTOMATED_WATCH / folder / collection
    pages_dir.mkdir(parents=True)
    (pages_dir / "2026-10-07.html").write_text(page)
    return f"{AUTOMATED_WATCH}/{folder}/{collection}/2026-10-07.html"


@pytest.fixture
def tldr_data_dir(tmp_path):
    create_database(tmp_path / "k-b.sqlite")
    write_tldr_page(tmp_path, "tldr_ai", TLDR_FIRST_COLLECTION, TLDR_AI_PAGE)
    return tmp_path


def test_a_tldr_page_gives_an_item_and_its_newsletter_article_per_article(
    tldr_data_dir,
):
    first_page = f"{AUTOMATED_WATCH}/tldr_ai/{TLDR_FIRST_COLLECTION}/2026-10-07.html"

    summary = run_import(tldr_data_dir)

    assert summary == (3, 0, 0, 1, 0, 0)
    rows = fetch_rows(tldr_data_dir, NEWSLETTER_ARTICLE_QUERY)
    assert rows[0] == (
        "ai",
        "2026-10-07T00:00:00Z",
        2,
        MISTRAL_TITLE,
        MISTRAL_SUMMARY,
        "https://mistral.ai/news/mistral-large-4/",
        None,
        "newsletter_article",
        first_page,
    )
    # The ranks count the sponsored entry (1) and leave out the job offer (5); the utm_*
    # parameters are removed, the others kept; no publication is cited yet.
    assert [(row[2], row[5], row[6]) for row in rows] == [
        (2, "https://mistral.ai/news/mistral-large-4/", None),
        (3, "https://openai.com/index/sharing-ai-progress-in-mathematics/", None),
        (
            4,
            (
                "https://www.nytimes.com/2026/10/06/science/"
                "china-ai-research-recruitment.html"
                "?unlocked_article_code=1.G1E.vzNX.LAgmpYtxdpWb&smid=url-share"
            ),
            None,
        ),
    ]
    assert fetch_rows(tldr_data_dir, "SELECT stream, raw_file_path FROM raw_data") == [
        ("tldr_ai", first_page)
    ]


def test_an_article_seen_again_keeps_its_first_collection_and_is_logged_already_seen(
    tldr_data_dir,
):
    run_import(tldr_data_dir)
    rows_of_the_first_import = fetch_rows(tldr_data_dir, NEWSLETTER_ARTICLE_QUERY)
    # The day of the last collection is collected again: the same page, edited since.
    edited_page = TLDR_AI_PAGE.replace(
        MISTRAL_TITLE, "Mistral Large 4, edited"
    ).replace(MISTRAL_SUMMARY, "Edited after the first collection.")
    assert edited_page.count("dited") == 2
    again = write_tldr_page(
        tldr_data_dir, "tldr_ai", TLDR_SECOND_COLLECTION, edited_page
    )

    second = run_import(tldr_data_dir)

    assert second == (0, 3, 0, 1, 0, 0)
    assert fetch_rows(tldr_data_dir, NEWSLETTER_ARTICLE_QUERY) == (
        rows_of_the_first_import
    )
    assert rows_of_the_first_import[0][3:5] == (MISTRAL_TITLE, MISTRAL_SUMMARY)
    assert again in registered_paths(tldr_data_dir)
    assert read_discards(tldr_data_dir, MISTRAL_ARTICLE_ID) == [
        {
            "kind": "discard",
            "rule": "already_seen",
            "source_code": "tldr",
            "source_item_id": MISTRAL_ARTICLE_ID,
            "raw_file_path": again,
            "compared_with_collected_at": "2026-10-11T15:31:30Z",
        }
    ]


def test_the_same_article_summarized_by_two_editions_is_kept_twice(tldr_data_dir):
    # The same page under the folder of another edition: every URL is seen twice.
    write_tldr_page(tldr_data_dir, "tldr_dev", TLDR_FIRST_COLLECTION, TLDR_AI_PAGE)

    summary = run_import(tldr_data_dir)

    assert summary == (6, 0, 0, 2, 0, 0)
    assert fetch_rows(
        tldr_data_dir,
        "SELECT edition, count(*), count(DISTINCT url) FROM newsletter_article"
        " GROUP BY edition ORDER BY edition",
    ) == [("ai", 3, 3), ("dev", 3, 3)]
    assert (
        count_rows(tldr_data_dir, "SELECT count(DISTINCT url) FROM newsletter_article")
        == 3
    )


def test_a_tldr_page_without_edition_title_is_rejected_and_not_registered(
    tldr_data_dir,
):
    landing_page = write_tldr_page(
        tldr_data_dir,
        "tldr_ai",
        TLDR_SECOND_COLLECTION,
        TLDR_AI_PAGE.replace("TLDR AI 2026-10-07</h1>", "Keep up with AI</h1>"),
    )

    summary = run_import(tldr_data_dir)

    assert (summary.item_count, summary.rejection_count) == (3, 1)
    assert landing_page not in registered_paths(tldr_data_dir)
    assert read_log(tldr_data_dir) == [
        {
            "kind": "rejection",
            "raw_file_path": landing_page,
            "reason": "ValueError: first h1 is not an edition title with its date: "
            "'Keep up with AI'",
        }
    ]
