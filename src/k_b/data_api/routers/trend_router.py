"""GET /trends: the repositories that gained the most stars and forks over a window of 3 days
(docs/specs-api.md)."""

import re
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BeforeValidator

from k_b.core.api_errors import ApiError, ErrorSchema
from k_b.data_api.dependencies import Database
from k_b.data_api.errors import ERROR_RESPONSES
from k_b.data_api.schemas.trend_schema import TrendSchema, TrendWindowSchema
from k_b.database.reading import read_trends

# YYYY-MM-DD, as the spec writes a day: no time, no number of seconds.
WINDOW_END_FORM = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


def check_window_end_form(window_end: object) -> object:
    """window_end as written in the URL: left to pydantic, 1760227200 would be a Unix time and
    2026-10-12T00:00:00Z a day."""
    if not isinstance(window_end, str) or not WINDOW_END_FORM.fullmatch(window_end):
        raise ValueError("window_end is not a day written YYYY-MM-DD")
    return window_end


# Still a date to pydantic: the OpenAPI declares it so.
WindowEnd = Annotated[date, BeforeValidator(check_window_end_form)]

router = APIRouter(prefix="/trends", tags=["trends"])

TRENDS_RESPONSES = {
    **ERROR_RESPONSES,
    404: {"model": ErrorSchema, "description": "trend_window_not_found"},
}


@router.get("", responses=TRENDS_RESPONSES)
def list_trends(
    connection: Database,
    window_end: Annotated[WindowEnd | None, Query()] = None,
) -> TrendWindowSchema:
    """The trends of a window, the latest in the base when none is given."""
    window = read_trends(connection, window_end)
    if window is None:
        raise ApiError(404, "trend_window_not_found")
    return TrendWindowSchema(
        window_end=window.window_end,
        trends=[TrendSchema(**trend._asdict()) for trend in window.trends],
    )
