import logging
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError

import pytest
from bs4.exceptions import ParserRejectedMarkup
from pydantic import ValidationError

from k_b.core.config import (
    GITHUB_TRACKED_REPOSITORIES,
    TLDR_PAUSE_BETWEEN_PAGES_SECONDS,
)
from k_b.etl.http_download import RedirectedOutsideError
from k_b.etl.normalize import normalize_item
from k_b.etl.source_contract import PageRefusedError, find_source_kind, list_sources
from k_b.etl.source_kinds import SOURCE_KINDS
from k_b.etl.sources import tldr
from k_b.etl.sources.tldr import (
    TldrArticle,
    TldrSource,
    convert_article,
    read_articles,
)
from k_b.etl.transform import read_raw_files

# Sunday: the window of a collection on this day holds a weekend.
TODAY = date(2026, 10, 4)
# The reader. Both pages are reduced extracts of collected pages: some entries, as written,
# their summaries cut to their first sentence.
FIXTURES_DIR = Path(__file__).parent.parent.parent / "fixtures"
AI_PAGE = (FIXTURES_DIR / "tldr_ai_extract.html").read_bytes()
DEV_PAGE = (FIXTURES_DIR / "tldr_dev_extract.html").read_bytes()
AI_HEADING = b"TLDR AI 2026-10-07</h1>"
# The edition of 2026-10-02, as the collection reads it: a collected page under another date.
EDITION_PAGE = AI_PAGE.replace(AI_HEADING, b"TLDR AI 2026-10-02</h1>")
# The landing page of the edition, https://tldr.tech/ai, received on 2026-10-20 and reduced to
# its structure and its h1, without any text of its own. It is the page a day without edition
# is redirected to; served with a 200 at the url of a day, as here, is not observed.
LANDING_PAGE = (FIXTURES_DIR / "tldr_ai_landing_extract.html").read_bytes()
LANDING_REFUSAL = (
    "first h1 is not an edition title with its date: 'Keep up with AI in 5 minutes'"
)


class FakeDownload:
    """Stands for download: answers each url as given, records the urls asked."""

    def __init__(self, answers):
        self.answers = answers
        self.urls = []

    def __call__(self, url, trusted_url_prefix, max_bytes):
        self.urls.append(url)
        answer = self.answers.get(
            url, RedirectedOutsideError(url, "https://tldr.tech/ai")
        )
        if isinstance(answer, Exception):
            raise answer
        return answer


@pytest.fixture
def fake_download(monkeypatch):
    fake = FakeDownload({})
    monkeypatch.setattr(tldr, "download", fake)
    monkeypatch.setattr(tldr, "current_day", lambda: TODAY)
    return fake


def test_each_tracked_edition_is_a_source_of_kb_collect():
    names = [source.name for source in list_sources(SOURCE_KINDS)]
    assert {"tldr_ai", "tldr_infosec", "tldr_dev"} <= set(names)


def test_the_page_of_a_day_is_asked_at_its_edition_and_date_url(fake_download):
    TldrSource("ai").fetch(datetime(2026, 10, 4, 8, 0, tzinfo=UTC))

    assert fake_download.urls == ["https://tldr.tech/ai/2026-10-04"]


def test_every_day_from_the_last_collection_day_to_today_is_asked(fake_download):
    TldrSource("ai").fetch(datetime(2026, 10, 2, 18, 0, tzinfo=UTC))

    assert fake_download.urls == [
        "https://tldr.tech/ai/2026-10-02",
        "https://tldr.tech/ai/2026-10-03",
        "https://tldr.tech/ai/2026-10-04",
    ]


def test_a_first_collection_asks_the_seven_days_before_today_and_today(fake_download):
    TldrSource("ai").fetch(last_collected_at=None)

    assert fake_download.urls[0] == "https://tldr.tech/ai/2026-09-27"
    assert len(fake_download.urls) == 8


def test_an_edition_page_is_returned_unchanged_under_its_day_and_logged(
    fake_download, caplog
):
    fake_download.answers["https://tldr.tech/ai/2026-10-02"] = EDITION_PAGE
    caplog.set_level(logging.INFO, logger="k_b.etl.sources.tldr")

    pages = TldrSource("ai").fetch(datetime(2026, 10, 2, 6, 0, tzinfo=UTC))

    assert pages == {"2026-10-02": EDITION_PAGE}
    assert f"tldr_ai 2026-10-02: edition page, {len(EDITION_PAGE)} bytes" in caplog.text


@pytest.mark.parametrize(
    "landing_url", ["https://tldr.tech/ai", "https://tldr.tech/ai/"]
)
def test_a_day_redirected_to_the_edition_landing_page_is_logged_and_skipped(
    fake_download, caplog, landing_url
):
    fake_download.answers["https://tldr.tech/ai/2026-10-02"] = EDITION_PAGE
    fake_download.answers["https://tldr.tech/ai/2026-10-03"] = RedirectedOutsideError(
        "https://tldr.tech/ai/2026-10-03", landing_url
    )
    caplog.set_level(logging.INFO, logger="k_b.etl.sources.tldr")

    pages = TldrSource("ai").fetch(datetime(2026, 10, 2, 6, 0, tzinfo=UTC))

    assert list(pages) == ["2026-10-02"]
    assert (
        f"tldr_ai 2026-10-03: no edition page, redirected to {landing_url}"
        in caplog.text
    )


def test_a_redirection_elsewhere_than_the_edition_landing_page_is_raised_with_its_day_and_url(
    fake_download, caplog
):
    fake_download.answers["https://tldr.tech/ai/2026-10-04"] = RedirectedOutsideError(
        "https://tldr.tech/ai/2026-10-04", "https://tldr.tech/login"
    )

    with pytest.raises(
        RedirectedOutsideError, match="https://tldr.tech/login"
    ) as raised:
        TldrSource("ai").fetch(datetime(2026, 10, 4, 6, 0, tzinfo=UTC))
    assert raised.value.__notes__ == [
        "tldr_ai 2026-10-04, from https://tldr.tech/ai/2026-10-04"
    ]
    # Logged once, where the collection handles the failure (etl/extract.py).
    assert not [record for record in caplog.records if record.levelno >= logging.ERROR]


def test_no_edition_page_in_the_window_returns_none_and_is_logged(
    fake_download, caplog
):
    caplog.set_level(logging.INFO, logger="k_b.etl.sources.tldr")

    assert TldrSource("ai").fetch(datetime(2026, 10, 2, 6, 0, tzinfo=UTC)) is None
    assert "tldr_ai: no edition page from 2026-10-02 to 2026-10-04" in caplog.text


@pytest.mark.parametrize(
    ("error", "observed"),
    [
        (
            HTTPError(
                "https://tldr.tech/ai/2026-10-04",
                500,
                "Internal Server Error",
                {},
                None,
            ),
            "HTTP Error 500: Internal Server Error",
        ),
        (URLError("timed out"), "<urlopen error timed out>"),
        (
            PageRefusedError("response larger than 1048576 bytes, refused"),
            "response larger than",
        ),
    ],
)
def test_an_error_is_raised_with_its_day_and_url_and_not_logged_here(
    fake_download, caplog, error, observed
):
    fake_download.answers["https://tldr.tech/ai/2026-10-04"] = error

    with pytest.raises(type(error), match=observed) as raised:
        TldrSource("ai").fetch(datetime(2026, 10, 4, 6, 0, tzinfo=UTC))
    assert raised.value.__notes__ == [
        "tldr_ai 2026-10-04, from https://tldr.tech/ai/2026-10-04"
    ]
    assert not [record for record in caplog.records if record.levelno >= logging.ERROR]


@pytest.mark.parametrize("edition", ["", "AI", "ai/../x", "ai?x=1"])
def test_an_edition_that_is_not_a_slug_is_refused(edition):
    with pytest.raises(ValueError, match="not a TLDR edition slug"):
        TldrSource(edition)


def test_a_last_collection_after_today_is_refused_with_both_days(fake_download):
    with pytest.raises(
        ValueError, match="last collection day 2026-10-05 is after today 2026-10-04"
    ):
        TldrSource("ai").fetch(datetime(2026, 10, 5, 6, 0, tzinfo=UTC))
    assert fake_download.urls == []


def test_a_page_that_is_not_an_edition_fails_the_source_with_its_day_and_url(
    fake_download, caplog
):
    fake_download.answers["https://tldr.tech/ai/2026-10-03"] = LANDING_PAGE

    with pytest.raises(PageRefusedError, match="^not_the_edition_of_the_day") as raised:
        TldrSource("ai").fetch(datetime(2026, 10, 3, 6, 0, tzinfo=UTC))
    assert raised.value.__notes__ == [
        "tldr_ai 2026-10-03, from https://tldr.tech/ai/2026-10-03",
        LANDING_REFUSAL,
    ]
    # Logged once, where the collection handles the failure (etl/extract.py).
    assert not [record for record in caplog.records if record.levelno >= logging.ERROR]


def test_the_edition_of_another_day_fails_the_source_with_its_date(fake_download):
    fake_download.answers["https://tldr.tech/ai/2026-10-03"] = EDITION_PAGE

    with pytest.raises(PageRefusedError, match="^not_the_edition_of_the_day") as raised:
        TldrSource("ai").fetch(datetime(2026, 10, 3, 6, 0, tzinfo=UTC))
    assert raised.value.__notes__ == [
        "tldr_ai 2026-10-03, from https://tldr.tech/ai/2026-10-03",
        "edition of 2026-10-02",
    ]


def test_each_day_refused_after_the_last_edition_read_is_noted(
    fake_download, monkeypatch
):
    fake_download.answers["https://tldr.tech/ai/2026-10-02"] = EDITION_PAGE
    fake_download.answers["https://tldr.tech/ai/2026-10-03"] = LANDING_PAGE
    fake_download.answers["https://tldr.tech/ai/2026-10-04"] = EDITION_PAGE
    # Monday: 2026-10-04 is a past day, not today.
    monkeypatch.setattr(tldr, "current_day", lambda: date(2026, 10, 5))

    with pytest.raises(PageRefusedError, match="^not_the_edition_of_the_day") as raised:
        TldrSource("ai").fetch(datetime(2026, 10, 2, 6, 0, tzinfo=UTC))
    # The edition of 2026-10-02 read is not returned: the source fails whole.
    assert raised.value.__notes__ == [
        "tldr_ai 2026-10-03, from https://tldr.tech/ai/2026-10-03",
        LANDING_REFUSAL,
        "tldr_ai 2026-10-04, from https://tldr.tech/ai/2026-10-04",
        "edition of 2026-10-02",
    ]


def test_a_day_refused_before_an_edition_read_is_noted_too_when_the_source_fails(
    fake_download, monkeypatch
):
    fake_download.answers["https://tldr.tech/ai/2026-10-02"] = LANDING_PAGE
    fake_download.answers["https://tldr.tech/ai/2026-10-03"] = EDITION_PAGE.replace(
        b"2026-10-02</h1>", b"2026-10-03</h1>"
    )
    fake_download.answers["https://tldr.tech/ai/2026-10-04"] = LANDING_PAGE
    monkeypatch.setattr(tldr, "current_day", lambda: date(2026, 10, 5))

    with pytest.raises(PageRefusedError, match="^not_the_edition_of_the_day") as raised:
        TldrSource("ai").fetch(datetime(2026, 10, 2, 6, 0, tzinfo=UTC))
    # Every day refused in the window, in its order: 2026-10-02 is given up only by a
    # collection that succeeds.
    assert raised.value.__notes__ == [
        "tldr_ai 2026-10-02, from https://tldr.tech/ai/2026-10-02",
        LANDING_REFUSAL,
        "tldr_ai 2026-10-04, from https://tldr.tech/ai/2026-10-04",
        LANDING_REFUSAL,
    ]


def test_a_past_day_refused_before_an_edition_read_is_given_up_and_logged(
    fake_download, caplog
):
    fake_download.answers["https://tldr.tech/ai/2026-10-02"] = LANDING_PAGE
    fake_download.answers["https://tldr.tech/ai/2026-10-04"] = EDITION_PAGE.replace(
        b"2026-10-02</h1>", b"2026-10-04</h1>"
    )
    caplog.set_level(logging.INFO, logger="k_b.etl.sources.tldr")

    pages = TldrSource("ai").fetch(datetime(2026, 10, 2, 6, 0, tzinfo=UTC))

    assert list(pages) == ["2026-10-04"]
    [warning] = [
        record for record in caplog.records if record.levelno == logging.WARNING
    ]
    assert warning.getMessage() == (
        "tldr_ai 2026-10-02: not_the_edition_of_the_day, given up: edition of "
        f"2026-10-04 read; {LANDING_REFUSAL}, from https://tldr.tech/ai/2026-10-02"
    )


def test_a_page_of_today_that_is_not_an_edition_is_skipped_and_logged(
    fake_download, caplog
):
    fake_download.answers["https://tldr.tech/ai/2026-10-03"] = EDITION_PAGE.replace(
        b"2026-10-02</h1>", b"2026-10-03</h1>"
    )
    fake_download.answers["https://tldr.tech/ai/2026-10-04"] = LANDING_PAGE
    caplog.set_level(logging.INFO, logger="k_b.etl.sources.tldr")

    pages = TldrSource("ai").fetch(datetime(2026, 10, 3, 6, 0, tzinfo=UTC))

    assert list(pages) == ["2026-10-03"]
    assert (
        "tldr_ai 2026-10-04: not_the_edition_of_the_day, read again at the next "
        f"collection; {LANDING_REFUSAL}, from https://tldr.tech/ai/2026-10-04"
    ) in caplog.text
    assert not [
        record for record in caplog.records if record.levelno >= logging.WARNING
    ]


def test_an_http_error_after_a_page_refused_is_raised(fake_download):
    fake_download.answers["https://tldr.tech/ai/2026-10-03"] = LANDING_PAGE
    fake_download.answers["https://tldr.tech/ai/2026-10-04"] = HTTPError(
        "https://tldr.tech/ai/2026-10-04", 500, "Internal Server Error", {}, None
    )

    with pytest.raises(HTTPError) as raised:
        TldrSource("ai").fetch(datetime(2026, 10, 3, 6, 0, tzinfo=UTC))
    assert raised.value.__notes__ == [
        "tldr_ai 2026-10-04, from https://tldr.tech/ai/2026-10-04"
    ]


def test_an_unexpected_error_of_the_reader_is_raised_with_its_day_and_url(
    fake_download, monkeypatch
):
    def fail_reading(page):
        raise RecursionError("maximum recursion depth exceeded")

    fake_download.answers["https://tldr.tech/ai/2026-10-04"] = EDITION_PAGE
    monkeypatch.setattr(tldr, "read_articles", fail_reading)

    with pytest.raises(RecursionError) as raised:
        TldrSource("ai").fetch(datetime(2026, 10, 4, 6, 0, tzinfo=UTC))
    assert raised.value.__notes__ == [
        "tldr_ai 2026-10-04, from https://tldr.tech/ai/2026-10-04"
    ]


AI_PAGE_PATH = "automated_watch/tldr_ai/2026-10-11T153130Z/2026-10-07.html"
DEV_PAGE_PATH = "automated_watch/tldr_dev/2026-10-11T153130Z/2026-10-05.html"
COLLECTED_AT = datetime(2026, 10, 11, 15, 31, 30, tzinfo=UTC)
MISTRAL_ARTICLE = TldrArticle(
    edition_date=date(2026, 10, 7),
    rank=2,
    title="Mistral Large 4 (9 minute read)",
    link="https://mistral.ai/news/mistral-large-4/?utm_source=tldrai",
    summary=(
        "Mistral Large 4 is a one-trillion-parameter multimodal model positioned as a"
        " European alternative to leading closed and open AI systems."
    ),
)


def test_one_article_per_entry_without_the_sponsored_one_that_its_rank_counts():
    # The page holds five entries: a sponsored one, three articles, a job offer of TLDR.
    articles = read_articles(AI_PAGE).entries

    assert [(article.rank, article.title) for article in articles] == [
        (2, "Mistral Large 4 (9 minute read)"),
        (3, "Sharing AI progress in mathematics (2 minute read)"),
        (
            4,
            (
                "In Race With US, China Struggles to Recruit Foreign AI Researchers"
                " (6 minute read)"
            ),
        ),
    ]


def test_a_job_offer_of_tldr_is_left_out_by_its_link_not_by_its_title():
    # The link of the offer, given to the article before it: the article is left out too.
    nytimes_link = (
        b"https://www.nytimes.com/2026/10/06/science/china-ai-research-recruitment.html"
        b"?unlocked_article_code=1.G1E.vzNX.LAgmpYtxdpWb&amp;smid=url-share"
        b"&amp;utm_source=tldrai"
    )
    job_offer_link = b"https://jobs.ashbyhq.com/tldr.tech/897f4391?utm_source=tldr"
    assert nytimes_link in AI_PAGE

    articles = read_articles(AI_PAGE.replace(nytimes_link, job_offer_link)).entries

    assert [article.rank for article in articles] == [2, 3]


def test_an_article_about_jobs_elsewhere_than_the_job_board_of_tldr_is_kept():
    page = AI_PAGE.replace(
        b"https://jobs.ashbyhq.com/tldr.tech/", b"https://jobs.ashbyhq.com/openai/"
    )

    assert [article.rank for article in read_articles(page).entries] == [2, 3, 4, 5]


def test_an_article_is_read_with_the_date_of_its_edition_its_link_and_its_summary():
    assert read_articles(AI_PAGE).entries[0] == MISTRAL_ARTICLE


def test_a_summary_with_a_link_is_read_as_displayed():
    # The sponsored entry, read as an article: its summary holds a link.
    page = AI_PAGE.replace(b" (Sponsor)", b"")

    assert read_articles(page).entries[0].summary == "Copilot. Read the Report"


def test_the_paragraphs_of_a_summary_are_read_apart():
    # Two paragraphs written as in the sponsored entries collected: text<p></p><p>text.
    page = AI_PAGE.replace(
        b"multimodal model positioned", b"multimodal model.<p></p><p>Positioned"
    )

    assert read_articles(page).entries[0].summary == (
        "Mistral Large 4 is a one-trillion-parameter multimodal model. Positioned as a"
        " European alternative to leading closed and open AI systems."
    )


def test_an_article_gives_an_item_of_its_edition_dated_at_midnight_utc():
    item = convert_article(MISTRAL_ARTICLE, COLLECTED_AT, AI_PAGE_PATH)

    assert item.model_dump(exclude_defaults=True) == {
        "source_code": "tldr",
        "source_item_id": "ai/2026-10-07#2",
        "kind": "newsletter_article",
        "title": MISTRAL_ARTICLE.title,
        "url": MISTRAL_ARTICLE.link,
        "published_at": datetime(2026, 10, 7, tzinfo=UTC),
        "summary": MISTRAL_ARTICLE.summary,
        "collected_at": COLLECTED_AT,
        "raw_file_path": AI_PAGE_PATH,
        "edition": "ai",
        "rank": 2,
    }


def test_a_link_escaped_twice_in_the_page_loses_its_tracking_parameters():
    article = read_articles(DEV_PAGE).entries[1]
    assert article.link.endswith("&amp;utm_source=tldrdev")

    item = convert_article(article, COLLECTED_AT, DEV_PAGE_PATH)

    assert normalize_item(item).url == (
        "https://www.theatlantic.com/technology/2026/10/openai-safety-team-resignation/"
        "688881/?gift=v5U_UzUTothfWXsPxtvNVAh7esWToMRD6XnbXmc5WgA"
    )


@pytest.mark.parametrize(
    "page, refusal",
    [
        (b"\xff" + AI_PAGE, "'utf-8' codec can't decode byte 0xff in position 0"),
        (
            AI_PAGE.replace(b"<h1", b"<p").replace(b"</h1>", b"</p>"),
            "no h1 in the page",
        ),
        (
            # The landing page of an edition, as feat/tldr recorded its title.
            AI_PAGE.replace(AI_HEADING, b"Keep up with AI in 5 minutes</h1>"),
            (
                "first h1 is not an edition title with its date: "
                "'Keep up with AI in 5 minutes'"
            ),
        ),
        (
            AI_PAGE.replace(AI_HEADING, b"TLDR AI 2026-13-45</h1>"),
            "first h1 is not an edition title with its date: 'TLDR AI 2026-13-45'",
        ),
        (
            AI_PAGE.replace(AI_HEADING, b"TLDR AI 2026-10-07 and more</h1>"),
            (
                "first h1 is not an edition title with its date: "
                "'TLDR AI 2026-10-07 and more'"
            ),
        ),
        (
            AI_PAGE.replace(b'class="font-bold"', b'class="font-semibold"'),
            "no article with a title, a link and a summary among 5 article elements",
        ),
        (
            AI_PAGE.replace(b'class="newsletter-html"', b'class="newsletter"'),
            "no article with a title, a link and a summary among 5 article elements",
        ),
        (
            AI_PAGE.replace(b"<article", b"<div").replace(b"</article>", b"</div>"),
            "no article with a title, a link and a summary among 0 article elements",
        ),
        (
            # The second entry now holds the third one.
            AI_PAGE.replace(b"</article><article", b"<article", 1),
            "article element inside an article element",
        ),
    ],
    ids=[
        "not utf-8",
        "no h1",
        "landing page",
        "no such day",
        "text after the date",
        "no title link",
        "no summary",
        "no article",
        "nested articles",
    ],
)
def test_a_page_that_is_not_a_readable_edition_is_refused_whole(page, refusal):
    with pytest.raises(ValueError) as error:
        read_articles(page)

    assert refusal in str(error.value)


def test_the_title_found_is_quoted_escaped_and_cut_in_the_refusal():
    # A line break is read as displayed, as a space; a control character is escaped.
    title = b"Ignore the rules\n2026-10-12 INFO a forged line \x1b[31m" + b"x" * 300
    page = AI_PAGE.replace(AI_HEADING, title + b"</h1>")

    with pytest.raises(ValueError) as error:
        read_articles(page)

    message = str(error.value)
    assert "'Ignore the rules 2026-10-12 INFO a forged line \\x1b[31mxxx" in message
    assert "\n" not in message
    assert "\x1b" not in message
    assert len(message) == len("first h1 is not an edition title with its date: ") + 100


def test_a_page_the_html_parser_rejects_is_refused_whole(monkeypatch):
    def reject_markup(markup, parser):
        raise ParserRejectedMarkup("expected name token at '<!RECEIVED_TEXT'")

    monkeypatch.setattr(tldr, "BeautifulSoup", reject_markup)

    with pytest.raises(ValueError) as refusal:
        read_articles(AI_PAGE)

    # The message of html.parser may quote the page (#212).
    assert str(refusal.value) == "page rejected by html.parser"
    assert refusal.value.__cause__ is None


@pytest.mark.parametrize(
    "changes",
    [
        {"title": ""},
        {"link": ""},
        {"link": "http://mistral.ai/news/mistral-large-4/"},
        {"link": "javascript:alert(1)"},
        {"link": "https://[mistral.ai/news/"},
        {"link": "https://?utm_source=tldrai"},
        {"summary": ""},
        {"title": "x" * 501},
    ],
)
def test_an_entry_without_title_https_link_or_summary_is_refused_as_an_item(changes):
    article = TldrArticle(**(vars(MISTRAL_ARTICLE) | changes))

    with pytest.raises(ValidationError):
        convert_article(article, COLLECTED_AT, AI_PAGE_PATH)


@pytest.mark.parametrize("folder", ["tldr", "tldr_ai_old", "tldr_AI"])
def test_an_article_of_a_folder_without_an_edition_slug_is_refused_as_an_item(folder):
    raw_file_path = f"automated_watch/{folder}/2026-10-11T153130Z/2026-10-07.html"

    with pytest.raises(ValidationError, match="edition"):
        convert_article(MISTRAL_ARTICLE, COLLECTED_AT, raw_file_path)


def test_the_tldr_pages_are_read_with_the_tldr_reader():
    kind = find_source_kind("tldr_ai", SOURCE_KINDS)

    assert kind is not None
    assert kind.reader is tldr.READER


def test_an_entry_refused_is_rejected_and_the_others_of_its_page_are_read(tmp_path):
    # The second article loses its summary: the class of its summary is renamed.
    before, mistral, after = AI_PAGE.partition(b"mistral-large-4")
    openai_without_summary = after.replace(
        b'class="newsletter-html"', b'class="newsletter"', 2
    ).replace(b'class="newsletter"', b'class="newsletter-html"', 1)
    page_path = tmp_path / AI_PAGE_PATH
    page_path.parent.mkdir(parents=True)
    page_path.write_bytes(before + mistral + openai_without_summary)

    reading = read_raw_files(
        tmp_path, SOURCE_KINDS, frozenset(), GITHUB_TRACKED_REPOSITORIES
    ).items

    (raw_file,) = reading.files
    assert [item.rank for item in raw_file.items] == [2, 4]
    (rejection,) = reading.rejections
    assert rejection.raw_file_path == AI_PAGE_PATH
    # The position among the entries read, sponsored ones left out, from 0: not the rank.
    assert rejection.reason.startswith("read entry 1: ValidationError")
    assert "a newsletter_article item without summary" in rejection.reason


def test_a_pause_comes_before_every_page_the_first_too(fake_download, monkeypatch):
    # The editions are collected one after the other: a pause before the first page of each
    # keeps one between the last page of an edition and the first of the next.
    pauses = []
    monkeypatch.setattr(tldr, "sleep", pauses.append)

    TldrSource("ai").fetch(None)

    # The first collection reads the 7 days before today and today.
    assert len(fake_download.urls) == 8
    assert pauses == [TLDR_PAUSE_BETWEEN_PAGES_SECONDS] * 8
