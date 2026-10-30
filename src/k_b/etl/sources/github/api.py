"""Read the GitHub REST API: one GET, answered as published."""

import math
import os
from datetime import UTC, datetime
from time import time
from urllib.error import HTTPError

from k_b.etl.http_download import download
from k_b.etl.http_retry import find_pause_end
from k_b.etl.source_contract import ExpectedSourceError
from k_b.items import SourceCode

# The source of every GitHub module, in the source table of the base.
SOURCE_CODE: SourceCode = "github"
API_URL = "https://api.github.com/"
API_VERSION = "2026-03-10"
TOKEN_VARIABLE = "KB_GITHUB_TOKEN"  # noqa: S105 — the variable's name, not a token
# A page of 100 releases of anthropics/claude-code weighs 2.2 MB in 2026.
MAX_RESPONSE_BYTES = 5 * 1024 * 1024
# GitHub asks to wait at least a minute before retrying a rate limited request that gives no
# Retry-After (docs: "Rate limits for the REST API", 2026-10-25).
RATE_LIMITED_WAIT_SECONDS = 60
# Until when, in epoch seconds, GitHub asked for no request at all: every GitHub source shares it,
# as they share the rate limit of the token (etl/http_retry.py, find_pause_end).
paused_until = 0.0


class GithubPausedError(ExpectedSourceError):
    """GitHub asked for no request until paused_until: none is sent."""

    def __init__(self, until: float):
        when = (
            "a Retry-After too long to read"
            if math.isinf(until)
            else datetime.fromtimestamp(until, UTC).isoformat()
        )
        super().__init__(f"GitHub asked for no request until {when}; none sent")


def get_from_github_api(path_and_query: str) -> bytes:
    """Return the body of GET API_URL + path_and_query, with the token if there is one (60
    requests per hour without, 5,000 with); refused without a request while GitHub asks for a
    pause."""
    global paused_until  # the pause of every GitHub source of the run
    if time() < paused_until:
        raise GithubPausedError(paused_until)
    token = os.environ.get(TOKEN_VARIABLE)
    try:
        return download(
            API_URL + path_and_query,
            trusted_url_prefix=API_URL,
            max_bytes=MAX_RESPONSE_BYTES,
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": API_VERSION,
            },
            # Never forwarded to a redirection: the token stays with api.github.com.
            unredirected_headers={"Authorization": f"Bearer {token}"}
            if token
            else None,
            rate_limited_wait_seconds=RATE_LIMITED_WAIT_SECONDS,
        )
    except HTTPError as error:
        pause_end = find_pause_end(error, API_URL, time())
        if pause_end is not None:
            paused_until = max(paused_until, pause_end)
        raise
