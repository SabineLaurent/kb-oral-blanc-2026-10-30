"""The common item every source is read into, before the import, and the row of a GH Archive
ranking; which source field goes where: docs/stockage.md."""

import re
from datetime import UTC, date, datetime, time
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from k_b.core.quoting import quote_value

# The sources of seed_sources.sql: a test keeps both lists equal.
SourceCode = Literal[
    "github", "arxiv", "cisa_kev", "clipper", "tldr", "zotero", "gh_archive"
]

# The kinds of item of schema.sql: a test keeps both lists equal.
ItemKind = Literal[
    "paper",
    "vulnerability",
    "newsletter_article",
    "release",
    "advisory",
    "zotero_entry",
    "clip",
]

# The acquisition modes a reader filters on (database/reading.py, kb search --in): those of
# schema.sql but wiki, which no source has yet (decision of 2026-10-14).
SearchableMode = Literal["watch", "library"]

# knownRansomwareCampaignUse of CISA KEV.
RansomwareUse = Literal["Known", "Unknown"]

# CVE_ID, GHSA_ID and REPOSITORY_NAME are written once, as text: each type anchors its own,
# and the readers apply it with re.fullmatch. [0-9], not \d: \d matches any Unicode digit, which the GLOB of
# schema.sql refuses.
CVE_ID = r"CVE-[0-9]{4}-[0-9]{4,}"
# The alphabet of the GitHub Advisory Database.
GHSA_ID = r"GHSA(?:-[23456789cfghjmpqrvwx]{4}){3}"
# owner/name, in the letters, digits and signs GitHub uses; a control character is refused too.
REPOSITORY_NAME = r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+"
# The slug of a newsletter edition, as in https://tldr.tech/<slug>/<YYYY-MM-DD>.
EDITION_SLUG = re.compile(r"[a-z]+")
# An arXiv identifier without its version, as text: the form of since 2007 (2609.31282), or the
# one of before (hep-th/9901001, math.GT/0309136). etl/sources/arxiv.py reads it in the identifier
# of a paper, etl/sources/zotero.py in the reference of a document. [0-9], not \d: \d matches any
# Unicode digit, and a fullwidth one would make another publication.
ARXIV_ID = r"(?:[0-9]{4}\.[0-9]{4,5}|[a-z]+(?:-[a-z]+)?(?:\.[A-Z]{2})?/[0-9]{7})"
# The page of a paper and of a CVE, followed by its identifier: the base keeps the identifier,
# arxiv.py and cisa_kev.py write the URL of the item, and database/reading.py the URL it lists.
ARXIV_ABSTRACT_PAGE_URL = "https://arxiv.org/abs/"
NVD_PAGE_URL = "https://nvd.nist.gov/vuln/detail/"
# The key Zotero gives a document or an annotation; the GLOB of schema.sql. Zotero's own
# alphabet is narrower: this form refuses no key it gives.
ZOTERO_KEY = r"[0-9A-Z]{8}"
# The integers SQLite holds, and BigQuery gives (INT64).
MIN_INT64 = -(2**63)
MAX_INT64 = 2**63 - 1
# The longest name a ranking may write in trend.repository_name, held by a CHECK too. A wide
# bound, not the one of GitHub: the longest of the collection of 2026-10-11 has 38 characters.
TREND_REPOSITORY_NAME_MAX_LENGTH = 255
# The longest tag, normalized, held by the CHECK of tag.name too; a test keeps both equal. A wide
# bound, not measured: the vault is not read.
TAG_MAX_LENGTH = 100
# The most tags of an item: a bound on the statements one item writes (code.md, defense in
# depth), not a rule of filing (decision of Sabine of 2026-10-19), not held by the base.
TAG_MAX_COUNT = 30


def is_repository_name(text: str) -> bool:
    """owner/name, where neither part is `.` nor contains `..`: the name goes into paths."""
    return (
        re.fullmatch(REPOSITORY_NAME, text) is not None
        and ".." not in text
        and "." not in text.split("/")
    )


def check_url_can_be_normalized(url: str) -> str:
    """Refuse a URL urlsplit refuses, and one without a host once its utm_* parameters are
    removed ("https://?utm_source=x"): the import would refuse it and stop."""
    try:
        host = urlsplit(url).hostname
    except ValueError:
        # The error of urlsplit quotes the host as written: only the state is kept.
        raise ValueError(f"url cannot be split: {len(url)} characters") from None
    if not host:
        raise ValueError(f"url without a host: {len(url)} characters")
    return url


# A NUL, or a surrogate code point: sqlite3 cannot encode a lone one in UTF-8.
UNSTORABLE_CHARACTER = re.compile("[\0\ud800-\udfff]")


def check_text_can_be_stored(text: str) -> str:
    """Refuse a NUL and a lone surrogate: SQLite stops a GLOB and a length() at a NUL, and
    sqlite3 fails to write a lone surrogate. A ValueError, as core/quoting.py rebuilds errors by
    their type."""
    if UNSTORABLE_CHARACTER.search(text):
        raise ValueError(f"text with a NUL or a lone surrogate: {len(text)} characters")
    return text


def normalize_tag(tag: str) -> str:
    """A tag as the base keeps it: stripped, without the leading # Obsidian writes, in lower
    case."""
    return tag.strip().removeprefix("#").strip().lower()


def check_tag_length(tag: str) -> str:
    """Refuse a tag longer than the base keeps, saying its length and the maximum."""
    if len(tag) > TAG_MAX_LENGTH:
        raise ValueError(
            f"tag too long: {len(tag)} characters, maximum {TAG_MAX_LENGTH}"
        )
    return tag


def check_tag_is_normalized(tag: str) -> str:
    """Refuse a tag normalize_tag would change: two forms of one tag would be two rows of tag."""
    if normalize_tag(tag) != tag:
        raise ValueError(f"tag not normalized: {len(tag)} characters")
    return tag


def convert_to_utc(moment: datetime) -> datetime:
    """The same moment in UTC; a moment out of the range of datetime once in UTC is refused, not
    raised as an OverflowError."""
    try:
        return moment.astimezone(UTC)
    except OverflowError:
        raise ValueError("date out of range once in UTC") from None


def at_midnight_utc(day: date) -> datetime:
    """A day stored at midnight UTC, the convention for a source that gives a day only
    (docs/stockage.md)."""
    return datetime.combine(day, time(), UTC)


def parse_aware_datetime(stored_date: object, label: str) -> datetime:
    """Read an ISO 8601 date, refusing one without a time zone (naive dates compare wrongly);
    label names the date in error messages."""
    if not isinstance(stored_date, str):
        raise ValueError(f"{label} is not a date")
    try:
        parsed_date = datetime.fromisoformat(stored_date)
    except ValueError as error:
        raise ValueError(f"{label} is invalid: {quote_value(stored_date)}") from error
    if parsed_date.tzinfo is None:
        raise ValueError(f"{label} has no time zone")
    return parsed_date


# Each text type ends with this check: after its length or its pattern, so that pydantic
# checks them on the string, and names them as it does for a string (string_too_long).
CAN_BE_STORED = AfterValidator(check_text_can_be_stored)
Text = Annotated[str, CAN_BE_STORED]
NonEmptyText = Annotated[str, StringConstraints(min_length=1), CAN_BE_STORED]
# Kept as given, not as pydantic's HttpUrl, which rewrites it (adds a final slash): the base
# keeps the URL the source gave, without its utm_* parameters (etl/normalize.py). http is
# accepted for a Zotero entry and a clip only (check_url_scheme): Zotero and Web Clipper keep the
# URL a page was saved from.
WebUrl = Annotated[
    str,
    StringConstraints(pattern=r"^https?://\S+$"),
    AfterValidator(check_url_can_be_normalized),
    CAN_BE_STORED,
]
# The ASCII patterns below refuse a NUL and a surrogate already.
CveId = Annotated[str, StringConstraints(pattern=f"^{CVE_ID}$")]
# Narrower than the GLOB '?*/?*' of tracked_repository and trend in schema.sql.
RepositoryName = Annotated[str, StringConstraints(pattern=f"^{REPOSITORY_NAME}$")]
# The CHECK of newsletter_article.edition in schema.sql. Anchored, and given as text: pydantic
# searches an unanchored pattern, and a compiled one would accept a final line break.
NewsletterEdition = Annotated[
    str, StringConstraints(pattern=f"^{EDITION_SLUG.pattern}$")
]
ArxivId = Annotated[str, StringConstraints(pattern=f"^{ARXIV_ID}$")]
ZoteroKey = Annotated[str, StringConstraints(pattern=f"^{ZOTERO_KEY}$")]
# A tag of an imported .md, in its normalized form.
Tag = Annotated[
    str,
    StringConstraints(min_length=1),
    AfterValidator(check_tag_length),
    AfterValidator(check_tag_is_normalized),
    CAN_BE_STORED,
]
# A date with its time zone, held in UTC.
UtcDatetime = Annotated[AwareDatetime, AfterValidator(convert_to_utc)]
# An integer SQLite holds: a larger one fails to be written.
Int64 = Annotated[int, Field(ge=MIN_INT64, le=MAX_INT64)]

# The fields each kind must fill, so that an entry the base would refuse is refused when read:
# one refused row would otherwise fail the whole import.
REQUIRED_FIELDS_BY_KIND = {
    "paper": ("url", "summary", "published_at", "revised_at"),
    "vulnerability": (
        "url",
        "cve",
        "summary",
        "published_at",
        "vendor",
        "product",
        "ransomware_use",
    ),
    "release": ("url", "published_at", "repository_name", "tag_name"),
    "advisory": ("url", "published_at"),
    "newsletter_article": ("url", "summary", "published_at", "edition", "rank"),
    # A Zotero entry may have no URL (ADR of 2026-10-13).
    "zotero_entry": ("item_type", "saved_at"),
    # Its URL is its key; its text, empty or not, is never absent.
    "clip": ("url", "saved_at", "summary"),
}
# The source of each kind. Written to a reference table as the item of its source of truth, an
# item of another source would mark a CVE as exploited, or overwrite a paper; a release or an
# advisory is a GitHub subtype, a newsletter article a TLDR one, an entry a Zotero one, a clip a
# Web Clipper one.
SOURCE_BY_KIND = {
    "paper": "arxiv",
    "vulnerability": "cisa_kev",
    "release": "github",
    "advisory": "github",
    "newsletter_article": "tldr",
    "zotero_entry": "zotero",
    "clip": "clipper",
}
# The fields only one kind may fill.
FIELDS_OF_ONE_KIND = {
    "vulnerability": ("vendor", "product", "ransomware_use"),
    "release": ("repository_name", "tag_name", "release_name"),
    "advisory": ("package_names",),
    "newsletter_article": ("edition", "rank"),
    "zotero_entry": ("item_type", "arxiv_id", "highlights"),
    "clip": ("tags",),
}
KINDS_WITH_CVE = ("vulnerability", "advisory")
# Zotero and Web Clipper keep the URL a page was saved from.
KINDS_WITH_HTTP_URL = ("zotero_entry", "clip")
# The form of source_item_id, for the kinds whose base column holds it: the GLOB of zotero_key,
# the GLOB of ghsa_id, the integer release_id. A release identifier is positive, written
# without a leading zero ("0007" and "7" would be one release in the base), in 19 digits at
# most, as MAX_INT64; its range is checked apart.
SOURCE_ITEM_ID_BY_KIND = {
    "zotero_entry": ZOTERO_KEY,
    "advisory": GHSA_ID,
    "release": "[1-9][0-9]{0,18}",
}

# The largest clip copied from the vault, in bytes: a UTF-8 text of this size holds as many
# characters at most, so that the text of a clip copied is never too long for the base. A wide
# bound: 183 441 bytes for the largest clip of the vault of 2026-10-17.
CLIP_MAX_BYTES = 1_000_000

# The longest text a source may write in each column of schema.sql, in characters, by kind and
# by the field that fills the column. Held twice (ADR of 2026-10-08): here when the raw is
# read, and by a CHECK of the column; a test keeps both equal.
CVE_MAX_LENGTH = 20
MAX_LENGTH_BY_KIND: dict[ItemKind, dict[str, int]] = {
    "paper": {"source_item_id": 20, "title": 500, "summary": 5_000},
    "vulnerability": {
        "cve": CVE_MAX_LENGTH,
        "title": 500,
        "summary": 5_000,
        "vendor": 200,
        "product": 500,
    },
    "release": {
        "tag_name": 255,
        "release_name": 255,
        "summary": 125_000,
        "url": 2_000,
    },
    "advisory": {
        "source_item_id": 19,
        "cve": CVE_MAX_LENGTH,
        "title": 1_024,
        "url": 2_000,
    },
    "newsletter_article": {
        "edition": 20,
        "title": 500,
        "summary": 5_000,
        "url": 2_000,
    },
    # Wide bounds: 91 characters at most for a title of the extract of 2026-10-13.
    "zotero_entry": {"item_type": 100, "title": 1_000, "url": 2_000, "arxiv_id": 20},
    "clip": {"title": 1_000, "url": 2_000, "summary": CLIP_MAX_BYTES},
}
# The lists written as JSON: the most elements, held by a CHECK too, and the longest element,
# held here only (a CHECK of SQLite cannot go through the elements of a JSON list).
MAX_COUNT_BY_KIND: dict[ItemKind, dict[str, int]] = {
    "paper": {"authors": 1_000},
    "advisory": {"package_names": 100},
}
MAX_ELEMENT_LENGTH_BY_KIND: dict[ItemKind, dict[str, int]] = {
    "paper": {"authors": 200},
    "advisory": {"package_names": 255},
}


# The longest text of a highlight, in characters, held by a CHECK of highlight too; a test keeps
# both equal. A wide bound: 825 characters at most in the extract of 2026-10-13.
HIGHLIGHT_MAX_LENGTH_BY_FIELD = {"text": 10_000, "comment": 10_000}


class Highlight(BaseModel):
    """A highlight of a PDF of a Zotero entry, written with its entry."""

    model_config = ConfigDict(
        frozen=True, extra="forbid", strict=True, hide_input_in_errors=True
    )

    zotero_key: ZoteroKey
    # Absent when Zotero gives none: the highlight is kept for its comment (decision of
    # 2026-10-13). A highlight without either is not one (check_text_or_comment).
    text: (
        Annotated[
            str,
            Field(max_length=HIGHLIGHT_MAX_LENGTH_BY_FIELD["text"]),
            CAN_BE_STORED,
        ]
        | None
    )
    comment: (
        Annotated[
            str,
            Field(max_length=HIGHLIGHT_MAX_LENGTH_BY_FIELD["comment"]),
            CAN_BE_STORED,
        ]
        | None
    )
    # dateAdded of Zotero.
    highlighted_at: UtcDatetime

    @model_validator(mode="after")
    def check_text_or_comment(self) -> "Highlight":
        if self.text is None and self.comment is None:
            raise ValueError("a highlight without text or comment")
        return self


class Item(BaseModel):
    # strict: a date given as text is refused, not parsed.
    model_config = ConfigDict(
        frozen=True, extra="forbid", strict=True, hide_input_in_errors=True
    )

    source_code: SourceCode
    source_item_id: NonEmptyText
    kind: ItemKind
    title: NonEmptyText
    # None for a Zotero entry only (REQUIRED_FIELDS_BY_KIND).
    url: WebUrl | None = None
    authors: tuple[NonEmptyText, ...] = ()
    published_at: UtcDatetime | None = None
    # arXiv only: the date of the version read.
    revised_at: UtcDatetime | None = None
    # Clipper and Zotero only: the day the document was saved by hand (a Zotero date; for a
    # clip, its day at midnight UTC).
    saved_at: UtcDatetime | None = None
    summary: Text | None = None
    collected_at: UtcDatetime
    # Relative to the raw folder, so the base can be rebuilt from a copy of it elsewhere.
    raw_file_path: NonEmptyText
    # CISA KEV only (vulnerability): vendorProject, product, knownRansomwareCampaignUse.
    vendor: NonEmptyText | None = None
    product: NonEmptyText | None = None
    ransomware_use: RansomwareUse | None = None
    # A vulnerability (CISA KEV) or an advisory (GitHub).
    cve: CveId | None = None
    # GitHub release only: its repository, tag_name, and name (None when GitHub gives none).
    repository_name: RepositoryName | None = None
    tag_name: NonEmptyText | None = None
    release_name: Text | None = None
    # GitHub advisory only: the affected packages, each once.
    package_names: tuple[NonEmptyText, ...] = ()
    # Newsletter article only: the slug of its edition, and its position in the edition page,
    # from 1. published_at is the day of the edition, at midnight UTC.
    edition: NewsletterEdition | None = None
    rank: Annotated[Int64, Field(ge=1)] | None = None
    # Zotero entry only: source_item_id is its Zotero key; its type of document, its arXiv
    # reference without version, and the highlights of its PDF, in the order of the extract.
    item_type: NonEmptyText | None = None
    arxiv_id: ArxivId | None = None
    highlights: tuple[Highlight, ...] = ()
    # Clip only: its tags, normalized, each once.
    tags: tuple[Tag, ...] = ()

    @model_validator(mode="after")
    def check_the_fields_of_its_kind(self) -> "Item":
        check_source(self)
        check_required_fields(self)
        check_fields_of_other_kinds(self)
        check_cve(self)
        check_lengths(self)
        check_url_scheme(self)
        check_source_item_id(self)
        check_tag_count(self)
        check_tags_once(self)
        return self


def check_source(item: Item) -> None:
    source_code = SOURCE_BY_KIND.get(item.kind, item.source_code)
    if item.source_code != source_code:
        raise ValueError(f"a {item.kind} item from {item.source_code}")


def check_required_fields(item: Item) -> None:
    missing = [
        field
        for field in REQUIRED_FIELDS_BY_KIND.get(item.kind, ())
        if getattr(item, field) is None
    ]
    if missing:
        raise ValueError(f"a {item.kind} item without {', '.join(missing)}")
    if item.kind == "paper" and not item.authors:
        raise ValueError("a paper item without authors")
    if item.kind == "advisory" and not item.package_names:
        raise ValueError("an advisory item without packages")


def check_fields_of_other_kinds(item: Item) -> None:
    filled = [
        field
        for kind, fields in FIELDS_OF_ONE_KIND.items()
        if kind != item.kind
        for field in fields
        if getattr(item, field) not in (None, ())
    ]
    if filled:
        raise ValueError(f"a {item.kind} item with {', '.join(filled)}")


def check_cve(item: Item) -> None:
    if item.cve is not None and item.kind not in KINDS_WITH_CVE:
        raise ValueError(f"a {item.kind} item with a CVE")


def check_url_scheme(item: Item) -> None:
    if (
        item.url is not None
        and not item.url.startswith("https://")
        and item.kind not in KINDS_WITH_HTTP_URL
    ):
        raise ValueError(
            f"url not https for a {item.kind} item: {len(item.url)} characters"
        )


def check_source_item_id(item: Item) -> None:
    form = SOURCE_ITEM_ID_BY_KIND.get(item.kind)
    if form is not None and not re.fullmatch(form, item.source_item_id):
        raise ValueError(
            f"source_item_id not of the form of a {item.kind}:"
            f" {len(item.source_item_id)} characters"
        )
    if item.kind == "release" and int(item.source_item_id) > MAX_INT64:
        raise ValueError(
            f"source_item_id of a release over 64 bits: {len(item.source_item_id)} digits"
        )


def check_tag_count(item: Item) -> None:
    if len(item.tags) > TAG_MAX_COUNT:
        raise ValueError(
            f"tags too long: {len(item.tags)} elements, maximum {TAG_MAX_COUNT}"
        )


def check_tags_once(item: Item) -> None:
    if len(set(item.tags)) != len(item.tags):
        raise ValueError(
            f"tags not each once: {len(item.tags)} tags, {len(set(item.tags))} distinct"
        )


def check_lengths(item: Item) -> None:
    """Refuse a text or a list longer than its maximum, saying which field, its length and the
    maximum."""
    too_long = list_texts_too_long(item) + list_lists_too_long(item)
    if too_long:
        raise ValueError("; ".join(too_long))


def list_texts_too_long(item: Item) -> list[str]:
    too_long = []
    for field, maximum in MAX_LENGTH_BY_KIND.get(item.kind, {}).items():
        text = getattr(item, field)
        if text is not None and len(text) > maximum:
            too_long.append(
                f"{field} too long: {len(text)} characters, maximum {maximum}"
            )
    return too_long


def list_lists_too_long(item: Item) -> list[str]:
    too_long = []
    for field, maximum in MAX_COUNT_BY_KIND.get(item.kind, {}).items():
        count = len(getattr(item, field))
        if count > maximum:
            too_long.append(f"{field} too long: {count} elements, maximum {maximum}")
    for field, maximum in MAX_ELEMENT_LENGTH_BY_KIND.get(item.kind, {}).items():
        longest = max(map(len, getattr(item, field)), default=0)
        if longest > maximum:
            too_long.append(
                f"an element of {field} too long: {longest} characters,"
                f" maximum {maximum}"
            )
    return too_long


class TrendRow(BaseModel):
    """A repository of a GH Archive ranking in a window of 3 days, not an item; a test keeps it
    in step with each CHECK of trend."""

    model_config = ConfigDict(strict=True, extra="forbid")

    # The last day of the window.
    window_end: date
    # repo.id of GitHub, and the last name seen in the window.
    repository_id: Annotated[Int64, Field(gt=0)]
    repository_name: Annotated[
        RepositoryName, Field(max_length=TREND_REPOSITORY_NAME_MAX_LENGTH)
    ]
    stars_gained: Annotated[Int64, Field(ge=0)]
    forks_gained: Annotated[Int64, Field(ge=0)]
