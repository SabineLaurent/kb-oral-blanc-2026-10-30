"""One log line per request of an API, never with the key, a parameter value nor a raw path
(docs/specs-api.md, "Journal"); its id is given back in X-Request-ID."""

import logging
import time
import uuid
from collections.abc import Awaitable, Callable

from fastapi import Request, Response

from k_b.core.api_health import HEALTH_PATH
from k_b.core.config import MILLISECONDS_PER_SECOND
from k_b.core.quoting import quote_value

logger = logging.getLogger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"
# No route matched, no client named by a valid key, no response sent, or no id given.
NONE_SEEN = "-"


async def log_request(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Give the request its id, then log it once its response is known; in a finally, so that a
    request stopped before its response leaves its line, without status."""
    request.state.request_id = str(uuid.uuid4())
    started = time.perf_counter()
    status_code: int | None = None
    try:
        response = await call_next(request)
        status_code = response.status_code
        response.headers[REQUEST_ID_HEADER] = request.state.request_id
        return response
    finally:
        # A probe that answers 200 is not logged: one every few seconds would bury the requests
        # of the clients. A probe that fails keeps its line.
        if not (request.url.path == HEALTH_PATH and status_code == 200):
            logger.info(
                "%s",
                format_request_line(
                    request.method,
                    find_route_template(request),
                    status_code,
                    round((time.perf_counter() - started) * MILLISECONDS_PER_SECOND),
                    getattr(request.state, "client_name", NONE_SEEN),
                    request.state.request_id,
                ),
            )


def read_request_id(request: Request) -> str:
    return str(getattr(request.state, "request_id", NONE_SEEN))


def find_route_template(request: Request) -> str:
    """The template of the route matched (/items/{item_id}), never the path received.

    FastAPI puts only its own routes in the scope: /docs, /openapi.json and /health, without
    parameter, are named by their path."""
    route = request.scope.get("route")
    if route is not None:
        return str(route.path)
    if request.url.path in {request.app.docs_url, request.app.openapi_url, HEALTH_PATH}:
        return request.url.path
    return NONE_SEEN


def format_request_line(
    method: str,
    route: str,
    status_code: int | None,
    duration_ms: int,
    client_name: str,
    request_id: str,
) -> str:
    # The method is the one value received: quoted, it cannot write a line of its own.
    status = NONE_SEEN if status_code is None else status_code
    return (
        f"request method={quote_value(method)} route={route} status={status}"
        f" duration_ms={duration_ms} client={client_name} request_id={request_id}"
    )
