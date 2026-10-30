"""The errors of both APIs: a named state, never a stack, a path nor a value received
(docs/specs-api.md, "Erreurs")."""

import logging
from collections.abc import Awaitable, Callable, Coroutine, Mapping
from typing import Any

from fastapi import Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

from k_b.core.api_request_log import read_request_id

logger = logging.getLogger(__name__)

# The states of the errors Starlette raises when it routes a request.
STATE_BY_ROUTING_STATUS = {404: "route_not_found", 405: "method_not_allowed"}
# FastAPI raises a 400 for a body it cannot read as JSON, one not in UTF-8 among them
# (fastapi/routing.py:485, 0.143): a malformed request, refused as any other. The only other
# HTTPException(400) of FastAPI 0.143 and Starlette 1.8 is a form that cannot be read
# (starlette/requests.py:297 and :311), and no route of K-B reads a form; the 400 of
# TrustedHostMiddleware answers before the app, without exception.
BODY_NOT_READ_STATUS = 400
# The name of a body refused as a whole: absent, not an object, not JSON, or holding a field that
# is not declared.
BODY = "body"
# The type of the error of a field that is not declared: its name comes from the client.
EXTRA_FIELD_ERROR = "extra_forbidden"


class ErrorSchema(BaseModel):
    error: str


class InvalidParameterSchema(ErrorSchema):
    # The names of the parameters refused, never their values.
    parameters: list[str]


# The error bodies every route of both APIs may give, declared in the OpenAPI in place of
# FastAPI's own 422.
API_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorSchema, "description": "invalid_api_key"},
    422: {"model": InvalidParameterSchema, "description": "invalid_parameter"},
}


class ApiError(Exception):
    """An error of an API, by its status, its named state and the headers it answers with."""

    def __init__(
        self,
        status_code: int,
        state: str,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(state)
        self.status_code = status_code
        self.state = state
        self.headers = headers


def respond_with_error(
    status_code: int, state: str, headers: Mapping[str, str] | None = None
) -> JSONResponse:
    return JSONResponse({"error": state}, status_code=status_code, headers=headers)


def respond_with_invalid_parameters(parameters: list[str]) -> JSONResponse:
    return JSONResponse(
        {"error": "invalid_parameter", "parameters": parameters}, status_code=422
    )


async def handle_api_error(request: Request, error: ApiError) -> Response:
    return respond_with_error(error.status_code, error.state, error.headers)


async def handle_invalid_parameter(
    request: Request, error: RequestValidationError
) -> Response:
    parameters = sorted({name_parameter(detail) for detail in error.errors()})
    return respond_with_invalid_parameters(parameters)


def name_parameter(detail: Mapping[str, Any]) -> str:
    """The name of the parameter refused, never a value received; a refusal of the whole body or
    of an undeclared field (named by the client) is the body."""
    location = detail["loc"]
    if (
        len(location) < 2
        or not isinstance(location[1], str)
        or detail["type"] == EXTRA_FIELD_ERROR
    ):
        return BODY
    return location[1]


async def handle_http_error(
    request: Request, error: StarletteHTTPException
) -> Response:
    """The errors Starlette raises when it routes a request, and the 400 of an unreadable body,
    each by its named state."""
    if error.status_code == BODY_NOT_READ_STATUS:
        return respond_with_invalid_parameters([BODY])
    state = STATE_BY_ROUTING_STATUS.get(error.status_code)
    if state is None:
        return await http_exception_handler(request, error)
    return respond_with_error(error.status_code, state, error.headers)


async def respond_to_unexpected_error(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """An exception no handler takes: a 500 with a named state, its type logged once, never its
    message nor its stack, which may quote a value received.

    A middleware, not a handler of Exception: Starlette raises again after such a handler, and
    uvicorn would log the stack."""
    try:
        return await call_next(request)
    except Exception as error:  # noqa: BLE001 — every exception no handler takes
        logger.error(
            "internal_error: %s request_id=%s",
            type(error).__name__,
            read_request_id(request),
        )
        return respond_with_error(500, "internal_error")


# Given to FastAPI(exception_handlers=...), whose type takes a handler of one exception class.
type ErrorHandlers = dict[
    int | type[Exception], Callable[[Request, Any], Coroutine[Any, Any, Response]]
]
API_ERROR_HANDLERS: ErrorHandlers = {
    ApiError: handle_api_error,
    RequestValidationError: handle_invalid_parameter,
    StarletteHTTPException: handle_http_error,
}
