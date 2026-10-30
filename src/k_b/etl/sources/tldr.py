"""The TLDR source: download the edition page of each day (E), then read its articles into items
(T)."""

import logging
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from time import sleep
from typing import NamedTuple

from bs4 import BeautifulSoup, Tag
from bs4.exceptions import ParserRejectedMarkup

from k_b.core.config import (
    TLDR_FIRST_COLLECTION_DAYS,
    TLDR_PAUSE_BETWEEN_PAGES_SECONDS,
)
from k_b.core.quoting import quote_value
from k_b.etl.http_download import RedirectedOutsideError, download
from k_b.etl.raw_store import AUTOMATED_WATCH_DIR_NAME, find_stream
from k_b.etl.source_contract import EntryReading, PageRefusedError, SourceReader
from k_b.items import EDITION_SLUG, Item, SourceCode, at_midnight_utc

SOURCE_CODE: SourceCode = "tldr"

logger = logging.getLogger(__name__)


# The name of its raw folders, written once: its instances and its kind use it.
FOLDER_PREFIX = "tldr"


# === EXTRACT ===

SITE_URL = "https://tldr.tech/"
# An edition page weighs about 80 KB in 2026.
MAX_PAGE_BYTES = 1024 * 1024
# The failure of a page received that is not the edition of its day (conventions.md, rule 1).
NOT_THE_EDITION_OF_THE_DAY = "not_the_edition_of_the_day"


class PageRefusal(NamedTuple):
    page_url: str
    # What the page is instead: the refusal of the reader, or the date of its edition.
    observed: str


class TldrSource:
    extension = "html"
    acquisition_dir_name = AUTOMATED_WATCH_DIR_NAME

    def __init__(self, edition: str):
        if not EDITION_SLUG.fullmatch(edition):
            raise ValueError(f"not a TLDR edition slug: {edition!r}")
        self.edition = edition
        self.name = f"{FOLDER_PREFIX}_{edition}"

    def fetch(self, last_collected_at: datetime | None) -> dict[str, bytes] | None:
        """Return the edition page of each day to collect, by day; None if there is none.

        A page that is not the edition of its day is never returned: refused after the last
        edition read, it fails the source; before, it is given up; today, skipped
        (docs/specs-collecte.md)."""
        today = current_day()
        days = list_days_to_collect(last_collected_at, today)
        read_pages = self.read_pages(days)
        pages = {
            day: page for day, page in read_pages.items() if isinstance(page, bytes)
        }
        refusals = {
            day: page
            for day, page in read_pages.items()
            if isinstance(page, PageRefusal)
        }
        today_refusal = refusals.pop(today, None)
        if today_refusal is not None:
            logger.info(
                "%s %s: %s, read again at the next collection; %s, from %s",
                self.name,
                today,
                NOT_THE_EDITION_OF_THE_DAY,
                today_refusal.observed,
                today_refusal.page_url,
            )
        last_edition_day = max(pages, default=None)
        if any(last_edition_day is None or day > last_edition_day for day in refusals):
            raise self.refuse(refusals)
        for day, refusal in refusals.items():
            logger.warning(
                "%s %s: %s, given up: edition of %s read; %s, from %s",
                self.name,
                day,
                NOT_THE_EDITION_OF_THE_DAY,
                last_edition_day,
                refusal.observed,
                refusal.page_url,
            )
        if not pages:
            logger.info(
                "%s: no edition page from %s to %s", self.name, days[0], days[-1]
            )
        return {day.isoformat(): page for day, page in pages.items()} or None

    def read_pages(self, days: list[date]) -> dict[date, bytes | PageRefusal]:
        """The page of each day, or why it is not the edition of the day; a day redirected to the
        landing page is left out."""
        read_pages: dict[date, bytes | PageRefusal] = {}
        for day in days:
            # Before every page, the first too: the editions are sources collected one after
            # the other, and the last page of one would else follow the first of the next.
            sleep(TLDR_PAUSE_BETWEEN_PAGES_SECONDS)
            page_url = f"{SITE_URL}{self.edition}/{day.isoformat()}"
            try:
                # The page url itself is the trusted prefix: any redirection is noticed.
                page = download(page_url, page_url, MAX_PAGE_BYTES)
                observed = find_refusal(page, day)
            except Exception as error:
                if (
                    isinstance(error, RedirectedOutsideError)
                    and error.redirect_url.rstrip("/") == f"{SITE_URL}{self.edition}"
                ):
                    logger.info(
                        "%s %s: no edition page, redirected to %s",
                        self.name,
                        day,
                        error.redirect_url,
                    )
                    continue
                # The error alone does not always say which day and url it is about. Logged
                # where the collection handles it (etl/extract.py), with this note.
                error.add_note(f"{self.name} {day}, from {page_url}")
                raise
            if observed is None:
                logger.info("%s %s: edition page, %d bytes", self.name, day, len(page))
                read_pages[day] = page
            else:
                read_pages[day] = PageRefusal(page_url, observed)
        return read_pages

    def refuse(self, refusals: dict[date, PageRefusal]) -> PageRefusedError:
        """The failure of the source, one note per day refused, with its day, url and
        observation."""
        error = PageRefusedError(NOT_THE_EDITION_OF_THE_DAY)
        for day, refusal in refusals.items():
            error.add_note(f"{self.name} {day}, from {refusal.page_url}")
            error.add_note(refusal.observed)
        return error


def find_refusal(page: bytes, day: date) -> str | None:
    """Why the page is not the edition of the day, as observed, read as at the import; None if it
    is."""
    try:
        reading = read_articles(page)
    except ValueError as error:
        return str(error)
    # read_articles raises for a page without any article: the first one is there.
    edition_date = reading.entries[0].edition_date
    if edition_date != day:
        return f"edition of {edition_date.isoformat()}"
    return None


def current_day() -> date:
    return datetime.now(UTC).date()


def list_days_to_collect(last_collected_at: datetime | None, today: date) -> list[date]:
    """Every day from the day of the last collection, or 7 days ago, to today; the day of the
    last collection is read again, as its edition may have come out after it."""
    if last_collected_at is None:
        first_day = today - timedelta(days=TLDR_FIRST_COLLECTION_DAYS)
    else:
        first_day = last_collected_at.astimezone(UTC).date()
    if first_day > today:
        raise ValueError(f"last collection day {first_day} is after today {today}")
    return [
        first_day + timedelta(days=offset)
        for offset in range((today - first_day).days + 1)
    ]


# === TRANSFORM ===

# The first h1 of an edition page: "TLDR AI 2026-10-08"; the landing page has no date.
EDITION_PAGE_TITLE = re.compile(r"TLDR .+ (\d{4}-\d{2}-\d{2})")
SPONSOR_MARK = "(Sponsor)"
# The job board of TLDR itself: its offers are published among the articles, without a mark.
TLDR_JOB_OFFER_URL_PREFIX = "https://jobs.ashbyhq.com/tldr.tech/"


@dataclass(frozen=True)
class TldrArticle:
    """An entry of an edition page, as read: the item refuses an invalid one."""

    edition_date: date
    # Its position among the article elements of the page, from 1, sponsored entries and job
    # offers counted: a change of what is left out renumbers nothing.
    rank: int
    title: str
    # The href as written, "" if there is none.
    link: str
    # Plain text; "" if there is none.
    summary: str


def read_articles(page: bytes) -> EntryReading[TldrArticle]:
    """One article per entry of an edition page, sponsored entries and job offers left out.

    An entry is returned as read, for the item to refuse; a page that is not UTF-8, not an
    edition, or without any complete article (a selector no longer matches) is refused whole."""
    try:
        # Decoded here: BeautifulSoup would guess the encoding of bytes.
        soup = BeautifulSoup(page.decode("utf-8"), "html.parser")
    except ParserRejectedMarkup:
        # Its message may quote the page (#212).
        raise ValueError("page rejected by html.parser") from None
    edition_date = read_edition_date(soup)
    # Refused before its entries are read: each article is searched whole, and a page of
    # articles inside one another would be searched as many times as it has articles.
    if soup.select_one("article article") is not None:
        raise ValueError("article element inside an article element")
    entries = soup.select("article")
    articles = [
        article
        for rank, entry in enumerate(entries, start=1)
        if not is_left_out(article := read_article(entry, edition_date, rank))
    ]
    if not any(
        article.title and article.link and article.summary for article in articles
    ):
        raise ValueError(
            "no article with a title, a link and a summary among "
            f"{len(entries)} article elements"
        )
    return EntryReading(articles, [])


def read_edition_date(soup: BeautifulSoup) -> date:
    heading = soup.select_one("h1")
    if heading is None:
        raise ValueError("no h1 in the page")
    title = read_text_or_empty(heading)
    refusal = ValueError(
        "first h1 is not an edition title with its date: " + quote_value(title)
    )
    match = EDITION_PAGE_TITLE.fullmatch(title)
    if match is None:
        raise refusal
    try:
        return date.fromisoformat(match.group(1))
    except ValueError:
        # The form of a date, but no such day (2026-13-45).
        raise refusal from None


def read_article(entry: Tag, edition_date: date, rank: int) -> TldrArticle:
    link = entry.select_one("a.font-bold")
    href = link.get("href") if link else None
    return TldrArticle(
        edition_date=edition_date,
        rank=rank,
        title=read_text_or_empty(link),
        # A str for one href; BeautifulSoup types every attribute as a str or a list.
        link=href if isinstance(href, str) else "",
        summary=read_text_or_empty(entry.select_one("div.newsletter-html")),
    )


def read_text_or_empty(element: Tag | None) -> str:
    """The text of an element, its pieces a space apart and white space collapsed; "" without the
    element."""
    return " ".join(element.get_text(" ").split()) if element else ""


def is_left_out(article: TldrArticle) -> bool:
    return SPONSOR_MARK in article.title or article.link.startswith(
        TLDR_JOB_OFFER_URL_PREFIX
    )


def convert_article(
    article: TldrArticle, collected_at: datetime, raw_file_path: str
) -> Item:
    # The edition is the end of the name of its raw folder (tldr_ai). find_stream cannot
    # raise here: the path of a page read holds its source folder.
    edition = find_stream(raw_file_path).removeprefix(FOLDER_PREFIX).removeprefix("_")
    return Item(
        source_code=SOURCE_CODE,
        source_item_id=f"{edition}/{article.edition_date.isoformat()}#{article.rank}",
        kind="newsletter_article",
        title=article.title,
        # Some links are escaped twice in the page: "&amp;" is still there once the HTML is
        # parsed. The utm_* parameters are removed with those of every item (etl/normalize.py).
        url=article.link.replace("&amp;", "&"),
        published_at=at_midnight_utc(article.edition_date),
        summary=article.summary or None,
        collected_at=collected_at,
        raw_file_path=raw_file_path,
        edition=edition,
        rank=article.rank,
    )


READER: SourceReader[TldrArticle] = SourceReader(
    SOURCE_CODE, read_articles, convert_article
)
