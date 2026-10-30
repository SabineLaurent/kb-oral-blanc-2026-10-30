"""The data API: the items of the base, read only, for the clients of Sabine (docs/specs-api.md)."""

from collections.abc import Mapping, Sequence
from pathlib import Path

from fastapi import Depends, FastAPI
from pydantic import SecretStr
from starlette.middleware.trustedhost import TrustedHostMiddleware

from k_b.core.api_access import check_api_key
from k_b.core.api_errors import respond_to_unexpected_error
from k_b.core.api_health import HEALTH_PATH, answer_health
from k_b.core.api_request_log import log_request
from k_b.core.api_telemetry import NATIVE_TELEMETRY_OFF
from k_b.core.config import DATA_API_ALLOWED_HOSTS
from k_b.data_api.api_router import api_router
from k_b.data_api.errors import ERROR_HANDLERS

# The version of docs/specs-api.md: a change a client can see changes both.
API_VERSION = "0.5.0"
# Asked of every user of the arXiv API, "on your product" (arXiv API Access, 2026-10-25): the
# data API serves its papers.
ARXIV_ACKNOWLEDGEMENT = (
    "Thank you to arXiv for use of its open access interoperability."
)


def create_app(
    database_path: Path,
    api_keys: Mapping[str, SecretStr],
    allowed_hosts: Sequence[str] = DATA_API_ALLOWED_HOSTS,
) -> FastAPI:
    """The app, its keys, its base and the hosts it answers given; without any key, every data
    route answers 401."""
    app = FastAPI(
        title="K-B data API",
        version=API_VERSION,
        description=ARXIV_ACKNOWLEDGEMENT,
        # On the app, not on a router: no data route can leave it out. /docs, /openapi.json and
        # /health are routes of Starlette, outside: they hold no data. ReDoc and the OAuth2
        # redirect of Swagger UI, which K-B does not use, are not served.
        dependencies=[Depends(check_api_key)],
        redoc_url=None,
        swagger_ui_oauth2_redirect_url=None,
        # /items/ is an unknown route (404), not a redirection to /items.
        redirect_slashes=False,
        exception_handlers=ERROR_HANDLERS,
        # Its traces would carry the raw query of a request (core/api_telemetry.py).
        telemetry=NATIVE_TELEMETRY_OFF,
    )
    app.state.database_path = database_path
    app.state.api_keys = dict(api_keys)
    # The last added is the outermost.
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(allowed_hosts))
    app.middleware("http")(respond_to_unexpected_error)
    # The outermost: the 400 of TrustedHostMiddleware and the 500 of an unexpected error have
    # their line too.
    app.middleware("http")(log_request)
    app.include_router(api_router)
    # add_route, as FastAPI adds /docs: a route of Starlette, which the key of the app does not
    # reach (fastapi/applications.py, 0.143).
    app.add_route(HEALTH_PATH, answer_health, methods=["GET"], include_in_schema=False)
    return app
