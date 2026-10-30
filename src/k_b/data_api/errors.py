"""The errors of the data API that the base gives, without SQL, those of both APIs being in
core/api_errors.py."""

import logging
import sqlite3
from typing import Any

from fastapi import Request
from fastapi.responses import Response

from k_b.core.api_errors import (
    API_ERROR_HANDLERS,
    API_ERROR_RESPONSES,
    ErrorHandlers,
    ErrorSchema,
    respond_with_error,
)
from k_b.core.api_request_log import read_request_id
from k_b.database.connection import MissingDatabaseError

logger = logging.getLogger(__name__)

# The error bodies of a data route, declared in the OpenAPI.
ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    **API_ERROR_RESPONSES,
    500: {"model": ErrorSchema, "description": "database_error, internal_error"},
    503: {"model": ErrorSchema, "description": "database_missing"},
}


async def handle_missing_database(
    request: Request, error: MissingDatabaseError
) -> Response:
    return respond_with_error(503, "database_missing")


async def handle_database_error(request: Request, error: sqlite3.Error) -> Response:
    # The type only: the message of SQLite may hold a path or a part of the query.
    logger.error(
        "database_error: %s request_id=%s",
        type(error).__name__,
        read_request_id(request),
    )
    return respond_with_error(500, "database_error")


ERROR_HANDLERS: ErrorHandlers = {
    **API_ERROR_HANDLERS,
    MissingDatabaseError: handle_missing_database,
    sqlite3.Error: handle_database_error,
}
