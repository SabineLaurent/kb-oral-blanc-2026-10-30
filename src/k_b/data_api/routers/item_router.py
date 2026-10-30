"""GET /items, the list of the items, and GET /items/{item_id}, the detail of an item
(docs/specs-api.md)."""

import re
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Path, Query
from pydantic import AfterValidator, BeforeValidator, TypeAdapter

from k_b.core.api_errors import ApiError, ErrorSchema
from k_b.data_api.dependencies import Database
from k_b.data_api.errors import ERROR_RESPONSES
from k_b.data_api.schemas.item_schema import (
    ItemDetailSchema,
    ItemPageSchema,
    ListedItemSchema,
)
from k_b.database.reading import format_since, read_item_detail, read_items
from k_b.items import (
    Int64,
    ItemKind,
    NonEmptyText,
    UtcDatetime,
    parse_aware_datetime,
)

# The bounds of the parameters (docs/specs-api.md, "Paramètres").
DEFAULT_LIMIT = 20
MAX_LIMIT = 100
MAX_OFFSET = 10_000
MAX_SEARCH_LENGTH = 200


def read_since_text(since: object) -> datetime:
    """since read as an ISO 8601 date with its zone: left to pydantic, a number would be a Unix
    time (since=20261007, the 23rd of August 1970)."""
    return parse_aware_datetime(since, "since")


def check_since_can_be_read(since: datetime) -> datetime:
    """since as the reading compares it, rounded up to its second, must remain a date
    (9999-12-31T23:59:59.5Z does not); a ValueError becomes a 422."""
    format_since(since)
    return since


# Still a date-time to pydantic: the OpenAPI declares it so.
Since = Annotated[
    UtcDatetime,
    BeforeValidator(read_since_text),
    AfterValidator(check_since_can_be_read),
]

# An integer written one way only: no sign +, no leading zero, no space, no decimal point.
ITEM_ID_FORM = re.compile(r"0|-?[1-9][0-9]*")


def check_item_id_form(item_id: object) -> object:
    """item_id as written in the path: left to pydantic, 1.0, " 1" and 01 would each be a URL of
    item 1."""
    if not isinstance(item_id, str) or not ITEM_ID_FORM.fullmatch(item_id):
        raise ValueError("item_id is not an integer written in digits")
    return item_id


ItemId = Annotated[Int64, BeforeValidator(check_item_id_form)]

DETAIL_ADAPTER: TypeAdapter[ItemDetailSchema] = TypeAdapter(ItemDetailSchema)

router = APIRouter(prefix="/items", tags=["items"])

DETAIL_RESPONSES = {
    **ERROR_RESPONSES,
    404: {"model": ErrorSchema, "description": "item_not_found"},
}


@router.get("", responses=ERROR_RESPONSES)
def list_items(
    connection: Database,
    kind: Annotated[list[ItemKind] | None, Query()] = None,
    since: Annotated[Since | None, Query()] = None,
    q: Annotated[NonEmptyText | None, Query(max_length=MAX_SEARCH_LENGTH)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    offset: Annotated[int, Query(ge=0, le=MAX_OFFSET)] = 0,
) -> ItemPageSchema:
    """A page of the items, the newest first by the date the source gives."""
    page = read_items(
        connection,
        limit=limit,
        offset=offset,
        kinds=kind or (),
        since=since,
        search_text=q,
    )
    return ItemPageSchema(
        items=[ListedItemSchema(**item._asdict()) for item in page.items],
        next_offset=bound_next_offset(page.next_offset),
    )


def bound_next_offset(next_offset: int | None) -> int | None:
    """None beyond the bound of offset, as the client could not ask for that page."""
    if next_offset is None or next_offset > MAX_OFFSET:
        return None
    return next_offset


@router.get("/{item_id}", responses=DETAIL_RESPONSES)
def read_item(
    connection: Database, item_id: Annotated[ItemId, Path()]
) -> ItemDetailSchema:
    """An item, with what its kind serves beyond the list."""
    detail = read_item_detail(connection, item_id)
    if detail is None:
        raise ApiError(404, "item_not_found")
    # The discriminator picks the schema of the kind; a field it does not name is not served.
    fields: dict[str, Any] = {**detail.listed._asdict(), **detail.details._asdict()}
    return DETAIL_ADAPTER.validate_python(fields)
