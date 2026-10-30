"""The items of the watch as the model will receive them: the fields of a whitelist per kind,
masked, then cut for the writing (masked first: a URL cut in two would no longer be found).

A field sent never carries an author nor the url of the item (.claude/rules/poc.md, rules 5 and
8)."""

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict

from k_b.core.config import MAX_FIELD_CHARS_FOR_WRITING, MAX_ITEMS_PER_DIGEST
from k_b.items import RansomwareUse

# The kinds of the watch, in the order of the reading: the rare ones first, so that a cap cuts
# the newsletter articles before a vulnerability. Neither zotero_entry nor clip: no library in
# the digests on 25/10 (Sabine, 2026-10-15).
type WatchKind = Literal[
    "vulnerability", "advisory", "paper", "release", "newsletter_article"
]
WATCH_KINDS: tuple[WatchKind, ...] = get_args(WatchKind.__value__)

# Any URL, with a scheme or from www.: replaced as a whole, its parameters included.
URL = re.compile(r"(?i)[a-z][a-z0-9+.-]*://\S+|www\.\S+")
MASKED_URL = "[url]"
# A GitHub login (letters, digits, hyphens, 39 at most) after a @ that does not follow a word,
# a path or an address: jane.doe@example.com is left. Limit: an address is not masked, the spec
# masking a @login only; an npm scope (@anthropic-ai/claude-code) is, which errs on the safe side.
LOGIN = re.compile(r"(?<![A-Za-z0-9_.@/-])@[A-Za-z0-9][A-Za-z0-9-]{0,38}")
MASKED_LOGIN = "@user"


class SentFields(BaseModel):
    """The fields of an item the model receives: built by the AI API, never by a source."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class PaperFields(SentFields):
    title: str
    summary: str


class VulnerabilityFields(SentFields):
    cve_id: str
    title: str
    description: str
    vendor: str
    product: str
    ransomware_use: RansomwareUse


class ReleaseFields(SentFields):
    repository_name: str
    tag_name: str
    title: str
    # The body holds the logins of the contributors: masked.
    body: str | None


class AdvisoryFields(SentFields):
    ghsa_id: str
    cve_id: str | None
    title: str
    # exploited of the data API, renamed: its name would invite the model to misread a false.
    cve_listed_in_cisa_kev: bool | None
    packages: list[str]


class NewsletterArticleFields(SentFields):
    edition: str
    title: str
    summary: str


type WatchFields = (
    PaperFields
    | VulnerabilityFields
    | ReleaseFields
    | AdvisoryFields
    | NewsletterArticleFields
)
FIELDS_BY_KIND: dict[WatchKind, type[WatchFields]] = {
    "vulnerability": VulnerabilityFields,
    "advisory": AdvisoryFields,
    "paper": PaperFields,
    "release": ReleaseFields,
    "newsletter_article": NewsletterArticleFields,
}
# The fields whose logins are masked (docs/registre-traitements.md: the logins of a release body).
LOGIN_FIELDS_BY_KIND: dict[WatchKind, tuple[str, ...]] = {"release": ("body",)}


@dataclass(frozen=True)
class WatchItem:
    item_id: int
    kind: WatchKind
    source_date: datetime
    # As the data API serves them, neither masked nor cut: for the sources of the answer, never
    # sent to the model, which receives fields.title, masked.
    url: str | None
    source_title: str
    fields: WatchFields


@dataclass(frozen=True)
class WatchReading:
    """The items read for a request; at the cap, capped_kind is the kind cut and covered_after
    the date of its first item left out."""

    items: tuple[WatchItem, ...]
    # Withdrawn from a catalog, or gone between the list and the detail.
    items_dropped: int
    capped_kind: WatchKind | None
    covered_after: datetime | None

    @property
    def items_capped(self) -> bool:
        return self.capped_kind is not None


@dataclass(frozen=True)
class WritingSelection:
    """The items of one digest, ready for the writing."""

    items: tuple[WatchItem, ...]
    # Items with at least one field cut.
    items_truncated: int
    # Items beyond MAX_ITEMS_PER_DIGEST.
    items_dropped: int


def to_item_ref(item_id: int) -> str:
    """The identifier of an item for the model, "i17" for item 17: no provider confirms an enum
    of integers."""
    return f"i{item_id}"


def build_fields(kind: WatchKind, values: Mapping[str, object]) -> WatchFields:
    """The fields of the whitelist of the kind, taken from values, each text masked."""
    fields_model = FIELDS_BY_KIND[kind]
    login_fields = LOGIN_FIELDS_BY_KIND.get(kind, ())
    return fields_model.model_validate(
        {
            name: mask_value(values[name], has_logins=name in login_fields)
            for name in fields_model.model_fields
        }
    )


def mask_value(value: object, has_logins: bool) -> object:
    if isinstance(value, list):
        return [mask_value(element, has_logins) for element in value]
    if not isinstance(value, str):
        return value
    masked = URL.sub(MASKED_URL, value)
    return LOGIN.sub(MASKED_LOGIN, masked) if has_logins else masked


def select_for_writing(items: Iterable[WatchItem]) -> WritingSelection:
    """The most recent items of a digest, MAX_ITEMS_PER_DIGEST at most, each text field cut to
    MAX_FIELD_CHARS_FOR_WRITING characters."""
    ordered = sorted(
        items, key=lambda item: (item.source_date, item.item_id), reverse=True
    )
    kept = ordered[:MAX_ITEMS_PER_DIGEST]
    cut_items = [cut_fields(item, MAX_FIELD_CHARS_FOR_WRITING) for item in kept]
    return WritingSelection(
        items=tuple(cut_items),
        items_truncated=sum(
            cut != item for cut, item in zip(cut_items, kept, strict=True)
        ),
        items_dropped=len(ordered) - len(kept),
    )


def cut_fields(item: WatchItem, max_chars: int) -> WatchItem:
    """The item, each text of its fields cut to max_chars characters."""
    values = item.fields.model_dump()
    cut_values = {name: cut_value(value, max_chars) for name, value in values.items()}
    if cut_values == values:
        return item
    return replace(item, fields=item.fields.model_copy(update=cut_values))


def cut_value(value: object, max_chars: int) -> object:
    if isinstance(value, list):
        return [cut_value(element, max_chars) for element in value]
    if isinstance(value, str):
        return value[:max_chars]
    return value
