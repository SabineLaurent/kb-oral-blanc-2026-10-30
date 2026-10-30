"""Read the items database: the list of the items is one query for the data API, kb search and
"what's new", one branch per kind, every filter a bound parameter."""

import json
import sqlite3
from collections.abc import Callable, Collection
from datetime import date, datetime, timedelta
from typing import Any, NamedTuple

from k_b.core.dates import format_day
from k_b.items import (
    ARXIV_ABSTRACT_PAGE_URL,
    MAX_INT64,
    MIN_INT64,
    NVD_PAGE_URL,
    ItemKind,
    RansomwareUse,
    SearchableMode,
    check_text_can_be_stored,
    convert_to_utc,
    parse_aware_datetime,
)

SELECT_RAW_FILE_PATHS = "SELECT raw_file_path FROM raw_data"

# One branch per ItemKind; its date is the one the source gives (docs/specs-api.md, "Date,
# titre et résumé par sorte"). A kind without summary gives NULL: its title alone is searched.
# Shared by the list and the detail: the title, date and URL of a kind are written once.
# publication and vulnerability have no kind, unlike the five subtypes: their branch keeps the
# items of its kind only, so that an item is read by the one branch of its kind.
LISTED_ITEMS = """
    SELECT item.id, item.kind, item.raw_data_id, publication.title AS title,
        publication.summary AS summary, publication.submitted_at AS source_date,
        :arxiv_page_url || publication.arxiv_id AS url, NULL AS withdrawn_at
    FROM item JOIN publication ON publication.item_id = item.id AND item.kind = 'paper'
    UNION ALL
    SELECT item.id, item.kind, item.raw_data_id, vulnerability.name, vulnerability.description,
        vulnerability.date_added, :nvd_page_url || vulnerability.cve_id,
        vulnerability.withdrawn_at
    FROM item JOIN vulnerability
        ON vulnerability.item_id = item.id AND item.kind = 'vulnerability'
    UNION ALL
    SELECT item.id, item.kind, item.raw_data_id,
        tracked_repository.repository_name || ' '
            || coalesce(nullif(release.name, ''), release.tag_name),
        release.body, release.published_at, release.url, NULL
    FROM item JOIN release ON release.item_id = item.id
        JOIN tracked_repository ON tracked_repository.id = release.tracked_repository_id
    UNION ALL
    SELECT item.id, item.kind, item.raw_data_id, advisory.summary, NULL, advisory.published_at,
        advisory.url, NULL
    FROM item JOIN advisory ON advisory.item_id = item.id
    UNION ALL
    SELECT item.id, item.kind, item.raw_data_id, newsletter_article.title,
        newsletter_article.summary, newsletter_article.edition_date, newsletter_article.url, NULL
    FROM item JOIN newsletter_article ON newsletter_article.item_id = item.id
    UNION ALL
    SELECT item.id, item.kind, item.raw_data_id, zotero_entry.title, NULL, zotero_entry.saved_at,
        zotero_entry.url, zotero_entry.withdrawn_at
    FROM item JOIN zotero_entry ON zotero_entry.item_id = item.id
    UNION ALL
    SELECT item.id, item.kind, item.raw_data_id, clip.title, clip.body, clip.saved_at, clip.url,
        NULL
    FROM item JOIN clip ON clip.item_id = item.id
"""

LISTED_FIELDS = "listed.id, listed.kind, listed.title, listed.source_date, listed.url, listed.withdrawn_at"

# The filters of the list, every one a bound parameter. Raw string: SQLite must receive
# ESCAPE '\'.
LIST_FILTERS = r"""
JOIN raw_data ON raw_data.id = listed.raw_data_id
JOIN source ON source.id = raw_data.source_id
WHERE (:kinds IS NULL OR listed.kind IN (SELECT value FROM json_each(:kinds)))
    AND (:since IS NULL OR listed.source_date >= :since)
    AND (:pattern IS NULL
        OR listed.title LIKE :pattern ESCAPE '\' OR listed.summary LIKE :pattern ESCAPE '\')
    AND (:mode IS NULL OR source.acquisition_mode = :mode)
ORDER BY listed.source_date DESC, listed.id DESC
LIMIT :limit OFFSET :offset
"""

# S608: the queries below join constants of this module only; every value is bound.
SELECT_ITEMS = f"SELECT {LISTED_FIELDS} FROM ({LISTED_ITEMS}) AS listed {LIST_FILTERS}"  # noqa: S608
# Its own query, not a filter of the list: each branch then reads its item by its index.
SELECT_LISTED_ITEM = (
    f"SELECT {LISTED_FIELDS} FROM ({LISTED_ITEMS}) AS listed WHERE listed.id = :item_id"  # noqa: S608
)

SELECT_HIGHLIGHTS = """
SELECT zotero_key, text, comment, highlighted_at, revised_at, withdrawn_at
FROM highlight
WHERE item_id = ?
ORDER BY highlighted_at, highlight_id
"""

# What the detail serves beyond the list, one query per kind, each column named as the response
# names it (docs/specs-api.md, "GET /items/{item_id}").
SELECT_PAPER = """
SELECT arxiv_id, authors, summary, revised_at AS arxiv_updated_at
FROM publication
WHERE item_id = ?
"""

SELECT_VULNERABILITY = """
SELECT cve_id, description, vendor, product, ransomware_use, revised_at
FROM vulnerability
WHERE item_id = ?
"""

SELECT_RELEASE = """
SELECT release.release_id, tracked_repository.repository_name, release.tag_name, release.body
FROM release JOIN tracked_repository ON tracked_repository.id = release.tracked_repository_id
WHERE release.item_id = ?
"""

# Exploited: the CVE is in the KEV catalog, withdrawn since or not; null without CVE.
SELECT_ADVISORY = """
SELECT advisory.ghsa_id, vulnerability.cve_id,
    CASE WHEN vulnerability.id IS NULL THEN NULL ELSE vulnerability.item_id IS NOT NULL END,
    advisory.packages
FROM advisory LEFT JOIN vulnerability ON vulnerability.id = advisory.vulnerability_id
WHERE advisory.item_id = ?
"""

SELECT_NEWSLETTER_ARTICLE = """
SELECT edition, edition_date, rank, summary
FROM newsletter_article
WHERE item_id = ?
"""

SELECT_ZOTERO_ENTRY = """
SELECT zotero_entry.zotero_key, zotero_entry.item_type, publication.arxiv_id,
    zotero_entry.revised_at
FROM zotero_entry LEFT JOIN publication ON publication.id = zotero_entry.publication_id
WHERE zotero_entry.item_id = ?
"""

SELECT_CLIP = """
SELECT published_at, body
FROM clip
WHERE item_id = ?
"""

# The latest window when none is given; a window exists in the base by its rows only.
SELECT_TRENDS = """
SELECT window_end, repository_id, repository_name, stars_gained, forks_gained
FROM trend
WHERE window_end = coalesce(:window_end, (SELECT max(window_end) FROM trend))
ORDER BY stars_gained DESC, forks_gained DESC, repository_id
"""


class ListedItem(NamedTuple):
    id: int
    kind: ItemKind
    title: str
    # The date of since, as the base writes it: YYYY-MM-DDTHH:MM:SSZ.
    source_date: str
    # None for a Zotero entry without URL.
    url: str | None
    # The date K-B saw a catalog row withdrawn: a withdrawn row is listed, not left out.
    withdrawn_at: str | None


class ItemPage(NamedTuple):
    items: list[ListedItem]
    # The offset of the next page; None on the last one.
    next_offset: int | None


class StoredHighlight(NamedTuple):
    zotero_key: str
    text: str | None
    comment: str | None
    highlighted_at: str
    revised_at: str | None
    withdrawn_at: str | None


class PaperDetail(NamedTuple):
    arxiv_id: str
    # The names, in arXiv's order.
    authors: list[str]
    summary: str
    # publication.revised_at: a date of arXiv, named apart from the dates of K-B.
    arxiv_updated_at: str


class VulnerabilityDetail(NamedTuple):
    cve_id: str
    description: str
    vendor: str
    product: str
    ransomware_use: RansomwareUse
    # A date of K-B: the collection that saw the row change.
    revised_at: str | None


class ReleaseDetail(NamedTuple):
    release_id: int
    repository_name: str
    tag_name: str
    body: str | None


class AdvisoryDetail(NamedTuple):
    ghsa_id: str
    cve_id: str | None
    # In the KEV catalog; None for an advisory without CVE.
    exploited: bool | None
    packages: list[str]


class NewsletterArticleDetail(NamedTuple):
    edition: str
    edition_date: str
    rank: int
    summary: str


class ZoteroEntryDetail(NamedTuple):
    zotero_key: str
    item_type: str
    # None for a document without arXiv reference.
    arxiv_id: str | None
    # A date of K-B: the collection that saw the entry change.
    revised_at: str | None
    highlights: list[StoredHighlight]


class ClipDetail(NamedTuple):
    # published of Web Clipper, a day at midnight UTC; None when the page gives none.
    published_at: str | None
    # The text kept, as written.
    body: str


KindDetail = (
    PaperDetail
    | VulnerabilityDetail
    | ReleaseDetail
    | AdvisoryDetail
    | NewsletterArticleDetail
    | ZoteroEntryDetail
    | ClipDetail
)


class ItemDetail(NamedTuple):
    listed: ListedItem
    details: KindDetail


class StoredTrend(NamedTuple):
    repository_id: int
    repository_name: str
    stars_gained: int
    forks_gained: int


class TrendWindow(NamedTuple):
    # The last day of the window.
    window_end: date
    # The most stars gained first, then the most forks, then by repository_id.
    trends: list[StoredTrend]


def read_raw_file_paths(connection: sqlite3.Connection) -> frozenset[str]:
    """The raw files already imported, one row of raw_data per file."""
    return frozenset(
        str(raw_file_path)
        for (raw_file_path,) in connection.execute(SELECT_RAW_FILE_PATHS)
    )


def read_items(
    connection: sqlite3.Connection,
    *,
    limit: int,
    offset: int = 0,
    kinds: Collection[ItemKind] = (),
    since: datetime | None = None,
    search_text: str | None = None,
    mode: SearchableMode | None = None,
) -> ItemPage:
    """A page of the items, the newest first; no kind given reads every kind.

    Refuses only what SQLite would misread or cannot bind (a negative LIMIT reads every row); the
    bounds of the data API are its own."""
    # limit + 1 is bound as a 64-bit integer: one more would raise an OverflowError.
    check_within_bounds("limit", limit, 1, MAX_INT64 - 1)
    check_within_bounds("offset", offset, 0, MAX_INT64)
    # One row more than the page tells whether a next page exists, in the same query.
    rows = connection.execute(
        SELECT_ITEMS,
        {
            "arxiv_page_url": ARXIV_ABSTRACT_PAGE_URL,
            "nvd_page_url": NVD_PAGE_URL,
            "kinds": json.dumps(list(kinds)) if kinds else None,
            "since": None if since is None else format_since(since),
            "pattern": None if search_text is None else to_like_pattern(search_text),
            "mode": mode,
            "limit": limit + 1,
            "offset": offset,
        },
    ).fetchall()
    return ItemPage(
        [ListedItem._make(row) for row in rows[:limit]],
        offset + limit if len(rows) > limit else None,
    )


def check_within_bounds(name: str, value: int, minimum: int, maximum: int) -> None:
    """Refuse a value out of its bounds, naming the bound crossed, never the value: an integer of
    thousands of digits cannot even be written."""
    if value < minimum:
        raise ValueError(f"{name} below minimum {minimum}")
    if value > maximum:
        raise ValueError(f"{name} above maximum {maximum}")


def format_since(since: datetime) -> str:
    """since in UTC, in the format of the base, rounded up to its second, as the dates of the
    base are compared as text; isoformat, as strftime leaves the width of the year 1 to the C
    library."""
    # utcoffset, not tzinfo: a tzinfo without offset leaves the date naive, read as local.
    if since.utcoffset() is None:
        raise ValueError("since has no time zone")
    try:
        since_utc = convert_to_utc(since)
        if since_utc.microsecond:
            since_utc = since_utc.replace(microsecond=0) + timedelta(seconds=1)
    except (ValueError, OverflowError):
        raise ValueError("since out of range once in UTC") from None
    return since_utc.replace(tzinfo=None).isoformat(timespec="seconds") + "Z"


def to_like_pattern(search_text: str) -> str:
    """A LIKE pattern that finds search_text anywhere, % and _ as written (ESCAPE '\\')."""
    if not search_text:
        raise ValueError("search_text is empty")
    # SQLite stops a LIKE pattern at a NUL, which would find every item; sqlite3 cannot bind a
    # lone surrogate.
    check_text_can_be_stored(search_text)
    escaped = search_text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def read_highlights(
    connection: sqlite3.Connection, item_id: int
) -> list[StoredHighlight]:
    """The highlights of a Zotero entry, the withdrawn ones included, by date of creation;
    item_id must hold in 64 bits, or sqlite3 raises an OverflowError."""
    check_within_bounds("item_id", item_id, MIN_INT64, MAX_INT64)
    return [
        StoredHighlight._make(row)
        for row in connection.execute(SELECT_HIGHLIGHTS, (item_id,))
    ]


def read_item_detail(connection: sqlite3.Connection, item_id: int) -> ItemDetail | None:
    """An item with what its kind serves beyond the list, or None if no item has this id.

    Its queries share one read transaction, so that a write between them cannot mix two states of
    a catalog row: the connection must hold no transaction."""
    check_within_bounds("item_id", item_id, MIN_INT64, MAX_INT64)
    connection.execute("BEGIN")
    try:
        row = connection.execute(
            SELECT_LISTED_ITEM,
            {
                "arxiv_page_url": ARXIV_ABSTRACT_PAGE_URL,
                "nvd_page_url": NVD_PAGE_URL,
                "item_id": item_id,
            },
        ).fetchone()
        if row is None:
            return None
        listed = ListedItem._make(row)
        return ItemDetail(
            listed, READ_DETAILS_BY_KIND[listed.kind](connection, item_id)
        )
    finally:
        # SQLite may have ended it on an error: a ROLLBACK would then raise, in place of it.
        if connection.in_transaction:
            connection.rollback()


def read_paper_detail(connection: sqlite3.Connection, item_id: int) -> PaperDetail:
    arxiv_id, authors, summary, arxiv_updated_at = fetch_row(
        connection, SELECT_PAPER, item_id
    )
    return PaperDetail(arxiv_id, json.loads(authors), summary, arxiv_updated_at)


def read_vulnerability_detail(
    connection: sqlite3.Connection, item_id: int
) -> VulnerabilityDetail:
    return VulnerabilityDetail._make(
        fetch_row(connection, SELECT_VULNERABILITY, item_id)
    )


def read_release_detail(connection: sqlite3.Connection, item_id: int) -> ReleaseDetail:
    return ReleaseDetail._make(fetch_row(connection, SELECT_RELEASE, item_id))


def read_advisory_detail(
    connection: sqlite3.Connection, item_id: int
) -> AdvisoryDetail:
    ghsa_id, cve_id, exploited, packages = fetch_row(
        connection, SELECT_ADVISORY, item_id
    )
    # SQLite gives 0 or 1.
    return AdvisoryDetail(
        ghsa_id,
        cve_id,
        None if exploited is None else bool(exploited),
        json.loads(packages),
    )


def read_newsletter_article_detail(
    connection: sqlite3.Connection, item_id: int
) -> NewsletterArticleDetail:
    return NewsletterArticleDetail._make(
        fetch_row(connection, SELECT_NEWSLETTER_ARTICLE, item_id)
    )


def read_zotero_entry_detail(
    connection: sqlite3.Connection, item_id: int
) -> ZoteroEntryDetail:
    zotero_key, item_type, arxiv_id, revised_at = fetch_row(
        connection, SELECT_ZOTERO_ENTRY, item_id
    )
    return ZoteroEntryDetail(
        zotero_key,
        item_type,
        arxiv_id,
        revised_at,
        read_highlights(connection, item_id),
    )


def read_clip_detail(connection: sqlite3.Connection, item_id: int) -> ClipDetail:
    return ClipDetail._make(fetch_row(connection, SELECT_CLIP, item_id))


def fetch_row(connection: sqlite3.Connection, query: str, item_id: int) -> Any:
    """The row of the subtype of an item the list has read, which exists since its branch in
    LISTED_ITEMS reads the same table by the same item_id."""
    return connection.execute(query, (item_id,)).fetchone()


READ_DETAILS_BY_KIND: dict[
    ItemKind, Callable[[sqlite3.Connection, int], KindDetail]
] = {
    "paper": read_paper_detail,
    "vulnerability": read_vulnerability_detail,
    "release": read_release_detail,
    "advisory": read_advisory_detail,
    "newsletter_article": read_newsletter_article_detail,
    "zotero_entry": read_zotero_entry_detail,
    "clip": read_clip_detail,
}


def read_trends(
    connection: sqlite3.Connection, window_end: date | None = None
) -> TrendWindow | None:
    """The trends of a window, the latest when none is given; None if the base has none."""
    rows = connection.execute(
        SELECT_TRENDS,
        {"window_end": None if window_end is None else format_day(window_end)},
    ).fetchall()
    if not rows:
        return None
    window_end_read = parse_aware_datetime(rows[0][0], "window_end").date()
    return TrendWindow(window_end_read, [StoredTrend._make(row[1:]) for row in rows])
