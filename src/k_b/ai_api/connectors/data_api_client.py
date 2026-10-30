"""The reading of the watch by the data API, with the key of the client AI_DIGEST
(docs/fiches-veille.md): a failure is a named state, raised as a DataApiError, without retry."""

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

import httpx2
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from k_b.ai_api.connectors.data_api_settings import DataApiSettings
from k_b.ai_api.deadline import Deadline
from k_b.ai_api.watch_items import (
    WATCH_KINDS,
    WatchItem,
    WatchKind,
    WatchReading,
    build_fields,
)
from k_b.core.config import (
    DATA_API_CONNECT_TIMEOUT_SECONDS,
    DATA_API_POOL_TIMEOUT_SECONDS,
    DATA_API_READ_TIMEOUT_SECONDS,
    DATA_API_WRITE_TIMEOUT_SECONDS,
    MAX_DATA_API_BODY_BYTES,
    MAX_ITEMS_PER_DIGEST_REQUEST,
)
from k_b.core.outbound import open_http_client
from k_b.items import Int64, RansomwareUse, UtcDatetime

ITEMS_PATH = "items"
# The largest page of GET /items (docs/specs-api.md, "Paramètres").
LIST_PAGE_SIZE = 100

type DataApiState = Literal[
    "data_api_unavailable",
    "data_api_timeout",
    "data_api_unauthorized",
    "data_api_invalid_response",
]


class DataApiError(Exception):
    """A reading stopped by the data API: its state and the HTTP status; never the body nor a
    header, which may quote what was sent."""

    def __init__(self, state: DataApiState, http_status: int | None = None) -> None:
        super().__init__(
            state if http_status is None else f"{state}, HTTP {http_status}"
        )
        self.state: DataApiState = state
        self.http_status = http_status


class DataApiModel(BaseModel):
    """An answer of the data API: the fields read are typed, the others ignored, the authors
    among them."""

    model_config = ConfigDict(extra="ignore", strict=True, frozen=True)


class ListedItem(DataApiModel):
    id: Int64
    kind: WatchKind
    source_date: UtcDatetime
    withdrawn_at: str | None


class ItemPage(DataApiModel):
    items: list[ListedItem]
    next_offset: Annotated[int, Field(ge=1)] | None


class ItemDetail(DataApiModel):
    id: Int64
    title: str
    source_date: UtcDatetime
    url: str | None
    withdrawn_at: str | None


class PaperDetail(ItemDetail):
    kind: Literal["paper"]
    summary: str


class VulnerabilityDetail(ItemDetail):
    kind: Literal["vulnerability"]
    cve_id: str
    description: str
    vendor: str
    product: str
    ransomware_use: RansomwareUse


class ReleaseDetail(ItemDetail):
    kind: Literal["release"]
    repository_name: str
    tag_name: str
    body: str | None


class AdvisoryDetail(ItemDetail):
    kind: Literal["advisory"]
    ghsa_id: str
    cve_id: str | None
    cve_listed_in_cisa_kev: bool | None = Field(validation_alias="exploited")
    packages: list[str]


class NewsletterArticleDetail(ItemDetail):
    kind: Literal["newsletter_article"]
    edition: str
    summary: str


KindDetail = (
    PaperDetail
    | VulnerabilityDetail
    | ReleaseDetail
    | AdvisoryDetail
    | NewsletterArticleDetail
)
# A detail of a kind out of the whitelist (zotero_entry) is refused here.
DETAIL: TypeAdapter[KindDetail] = TypeAdapter(
    Annotated[KindDetail, Field(discriminator="kind")]
)
PAGE = TypeAdapter(ItemPage)


class ErrorBody(DataApiModel):
    error: str


@dataclass(frozen=True)
class Answer:
    """An answer of the data API, its body read whole within MAX_DATA_API_BODY_BYTES."""

    status_code: int
    content: bytes


@dataclass(frozen=True)
class ItemList:
    """The items listed and kept under the cap, in the order of the reading."""

    items: list[ListedItem]
    withdrawn: int
    capped_kind: WatchKind | None
    covered_after: datetime | None


def build_data_api_client(
    settings: DataApiSettings, transport: httpx2.BaseTransport | None = None
) -> httpx2.Client:
    return open_http_client(
        settings.base_url,
        {"Authorization": f"Bearer {settings.api_key.get_secret_value()}"},
        httpx2.Timeout(
            connect=DATA_API_CONNECT_TIMEOUT_SECONDS,
            read=DATA_API_READ_TIMEOUT_SECONDS,
            write=DATA_API_WRITE_TIMEOUT_SECONDS,
            pool=DATA_API_POOL_TIMEOUT_SECONDS,
        ),
        transport,
    )


def read_watch(
    http_client: httpx2.Client, since: datetime, *, deadline: Deadline
) -> WatchReading:
    """The items of the whitelist dated since `since` or later, MAX_ITEMS_PER_DIGEST_REQUEST at
    most, each read in its detail and masked; the deadline bounds the reading, as the timeouts
    bound each wait only."""
    item_list = list_items(http_client, format_since(since), deadline)
    items = []
    for listed_item in item_list.items:
        detail = read_detail(http_client, listed_item, deadline)
        # Gone, or withdrawn, since the list.
        if detail is None or detail.withdrawn_at is not None:
            continue
        items.append(to_watch_item(detail))
    return WatchReading(
        items=tuple(items),
        items_dropped=item_list.withdrawn + len(item_list.items) - len(items),
        capped_kind=item_list.capped_kind,
        covered_after=item_list.covered_after,
    )


def format_since(since: datetime) -> str:
    """since in UTC, rounded up to the second, as the data API reads it (database/reading.py may
    not be imported); isoformat, as strftime leaves the width of the year to the C library."""
    # utcoffset, not tzinfo: a tzinfo without offset leaves the date naive, read as local.
    if since.utcoffset() is None:
        raise ValueError("since without time zone")
    try:
        since_utc = since.astimezone(UTC)
        if since_utc.microsecond:
            since_utc = since_utc.replace(microsecond=0) + timedelta(seconds=1)
    except OverflowError:
        raise ValueError("since out of range once in UTC") from None
    return since_utc.replace(tzinfo=None).isoformat(timespec="seconds") + "Z"


def list_items(http_client: httpx2.Client, since: str, deadline: Deadline) -> ItemList:
    """The items of each kind in the order of WATCH_KINDS, until the cap; a withdrawn entry takes
    no place, and an item listed again by a shifted page is read once."""
    kept: list[ListedItem] = []
    seen_ids: set[int] = set()
    withdrawn = 0
    for kind in WATCH_KINDS:
        for listed_item in list_kind(http_client, since, kind, deadline):
            if listed_item.id in seen_ids:
                continue
            seen_ids.add(listed_item.id)
            if listed_item.withdrawn_at is not None:
                withdrawn += 1
                continue
            if len(kept) == MAX_ITEMS_PER_DIGEST_REQUEST:
                return ItemList(kept, withdrawn, kind, listed_item.source_date)
            kept.append(listed_item)
    return ItemList(kept, withdrawn, None, None)


def list_kind(
    http_client: httpx2.Client, since: str, kind: WatchKind, deadline: Deadline
) -> Iterator[ListedItem]:
    """The items of a kind, the most recent first, a page asked only when the one before is read."""
    offset = 0
    while True:
        response = send(
            http_client,
            ITEMS_PATH,
            {"since": since, "kind": kind, "limit": LIST_PAGE_SIZE, "offset": offset},
            deadline,
        )
        page = read_body(response, PAGE)
        for listed_item in page.items:
            if listed_item.kind != kind:
                raise DataApiError("data_api_invalid_response", response.status_code)
            yield listed_item
        if page.next_offset is None:
            return
        # A page that does not move forward would be asked forever.
        if page.next_offset <= offset:
            raise DataApiError("data_api_invalid_response", response.status_code)
        offset = page.next_offset


def read_detail(
    http_client: httpx2.Client, listed_item: ListedItem, deadline: Deadline
) -> KindDetail | None:
    """The detail of the item listed, or None if it is gone since the list."""
    response = send(http_client, f"{ITEMS_PATH}/{listed_item.id}", None, deadline)
    if response.status_code == httpx2.codes.NOT_FOUND and is_item_not_found(response):
        return None
    detail = read_body(response, DETAIL)
    if detail.id != listed_item.id or detail.kind != listed_item.kind:
        raise DataApiError("data_api_invalid_response", response.status_code)
    return detail


def send(
    http_client: httpx2.Client,
    path: str,
    params: dict[str, str | int] | None,
    deadline: Deadline,
) -> Answer:
    """One GET, its body read within its bounds, its failure as a named state; raised from None,
    as the error of httpx2 names the URL."""
    if deadline.is_passed():
        raise DataApiError("data_api_timeout")
    try:
        with http_client.stream(
            "GET", path, params=params, timeout=deadline.cap(http_client.timeout)
        ) as response:
            return Answer(response.status_code, read_bounded_body(response, deadline))
    except httpx2.TimeoutException:
        raise DataApiError("data_api_timeout") from None
    except httpx2.TransportError:
        raise DataApiError("data_api_unavailable") from None
    except httpx2.RequestError:
        # An answer that cannot be read (a body badly encoded).
        raise DataApiError("data_api_invalid_response") from None


def read_bounded_body(response: httpx2.Response, deadline: Deadline) -> bytes:
    """The body, refused beyond MAX_DATA_API_BODY_BYTES without quoting it, and stopped as soon
    as the deadline passes between two pieces."""
    status = response.status_code
    length = response.headers.get("Content-Length")
    if (
        length is not None
        and length.isascii()
        and length.isdigit()
        and int(length) > MAX_DATA_API_BODY_BYTES
    ):
        raise DataApiError("data_api_invalid_response", status)
    body = bytearray()
    # Decoded bytes: a compressed body is bounded by what it takes once read.
    for chunk in response.iter_bytes():
        body += chunk
        if len(body) > MAX_DATA_API_BODY_BYTES:
            raise DataApiError("data_api_invalid_response", status)
        if deadline.is_passed():
            raise DataApiError("data_api_timeout", status)
    return bytes(body)


def read_body[BodyT](response: Answer, adapter: TypeAdapter[BodyT]) -> BodyT:
    """The body of a 200 in its schema; any other answer is a named state."""
    status = response.status_code
    if status == httpx2.codes.UNAUTHORIZED:
        raise DataApiError("data_api_unauthorized", status)
    if status >= httpx2.codes.INTERNAL_SERVER_ERROR:
        raise DataApiError("data_api_unavailable", status)
    if status != httpx2.codes.OK:
        # A redirection not followed, a 400 of a host refused, a 404 of a route, a 422.
        raise DataApiError("data_api_invalid_response", status)
    try:
        return adapter.validate_json(response.content)
    except ValidationError:
        raise DataApiError("data_api_invalid_response", status) from None


def is_item_not_found(response: Answer) -> bool:
    try:
        return ErrorBody.model_validate_json(response.content).error == "item_not_found"
    except ValidationError:
        return False


def to_watch_item(detail: KindDetail) -> WatchItem:
    return WatchItem(
        item_id=detail.id,
        kind=detail.kind,
        source_date=detail.source_date,
        url=detail.url,
        source_title=detail.title,
        fields=build_fields(detail.kind, detail.model_dump()),
    )
