"""When a source download gets a retry, and after which wait: the politeness of K-B towards the
services it collects (RFC 9110 Retry-After, rate limits)."""

import math
import re
from datetime import UTC, datetime
from http import HTTPStatus
from urllib.error import HTTPError, URLError

from k_b.core.retry_after import is_retry_after_too_long, read_retry_after

# The wait before the one retry after a 429, a 5xx or a failed connection, when the answer gives
# no Retry-After. A timeout gets no retry, as for the model (ai_api/connectors/llm_client.py): it
# would double a wait of HTTP_TIMEOUT_SECONDS (core/config.py).
RETRY_WAIT_SECONDS = 5
# Never less, whatever a Retry-After says: the 3 seconds arXiv asks between two requests (Terms of
# Use for arXiv APIs).
MIN_RETRY_WAIT_SECONDS = 3
# A Retry-After above it is not waited: the source fails, its last collection date stays, and the
# next collection asks again.
RETRY_AFTER_MAX_SECONDS = 60
# A rate limit with no request left (GitHub): its reset may be an hour away, so the answer gets no
# retry; the reset time, in epoch seconds, is noted on the error.
RATE_LIMIT_REMAINING_HEADER = "x-ratelimit-remaining"
RATE_LIMIT_RESET_HEADER = "x-ratelimit-reset"
EPOCH_SECONDS = re.compile(r"[0-9]{1,10}")
# A pause asked by a service lasts at least a minute: GitHub asks it when it gives no reset
# (docs: "Rate limits for the REST API", 2026-10-25).
MIN_PAUSE_SECONDS = 60


def find_retry_wait(
    error: URLError | ConnectionError,
    trusted_url_prefix: str,
    rate_limited_wait: float,
) -> float | None:
    """The wait before the retry, or None: no retry.

    rate_limited_wait follows a 429 without Retry-After; a 403 is retried only with a Retry-After
    (the secondary rate limit of GitHub), as it is first a refusal. An answer served from outside
    trusted_url_prefix, after a redirection, is not read."""
    if not isinstance(error, HTTPError):
        # A failed connection, also when it drops before the answer (ConnectionError); a
        # timeout is not retried (see RETRY_WAIT_SECONDS).
        is_timeout = isinstance(error, URLError) and isinstance(
            error.reason, TimeoutError
        )
        return None if is_timeout else RETRY_WAIT_SECONDS
    if not is_served_from(error, trusted_url_prefix) or is_rate_limit_exhausted(error):
        return None
    retry_after = error.headers.get("Retry-After")
    if not is_retried_status(error, retry_after) or is_retry_after_beyond_max(
        retry_after
    ):
        # Beyond the maximum, a shorter wait would get the same answer.
        return None
    seconds = read_retry_after(retry_after)
    if seconds is None:
        if error.code == HTTPStatus.TOO_MANY_REQUESTS:
            return rate_limited_wait
        return RETRY_WAIT_SECONDS
    return max(seconds, MIN_RETRY_WAIT_SECONDS)


def find_pause_end(
    error: HTTPError, trusted_url_prefix: str, now: float
) -> float | None:
    """Until when, in epoch seconds, the service asks for no request at all, or None: the reset
    of an exhausted rate limit, or the end of a Retry-After beyond RETRY_AFTER_MAX_SECONDS."""
    if not is_served_from(error, trusted_url_prefix):
        return None
    # Both may come together (a secondary rate limit of GitHub): the longer wins.
    pause_ends: list[float] = []
    if is_rate_limit_exhausted(error):
        # Without a readable reset, or a reset already past (a clock off): the minute GitHub asks.
        pause_ends.append(
            max(read_rate_limit_reset(error) or 0, now + MIN_PAUSE_SECONDS)
        )
    retry_after = error.headers.get("Retry-After")
    if is_retried_status(error, retry_after) and is_retry_after_beyond_max(retry_after):
        seconds = read_retry_after(retry_after)
        # Too long to read: no request for the rest of the process.
        pause_ends.append(math.inf if seconds is None else now + seconds)
    return max(pause_ends, default=None)


def is_retried_status(error: HTTPError, retry_after: str | None) -> bool:
    """A 429, a 5xx, or a 403 with a Retry-After (the secondary rate limit of GitHub): a 403
    without it is first a refusal."""
    return (
        error.code == HTTPStatus.TOO_MANY_REQUESTS
        or error.code >= HTTPStatus.INTERNAL_SERVER_ERROR
        or (error.code == HTTPStatus.FORBIDDEN and retry_after is not None)
    )


def is_retry_after_beyond_max(retry_after: str | None) -> bool:
    seconds = read_retry_after(retry_after)
    return is_retry_after_too_long(retry_after) or (
        seconds is not None and seconds > RETRY_AFTER_MAX_SECONDS
    )


def read_rate_limit_reset(error: HTTPError) -> int | None:
    """Its reset in epoch seconds; None when missing or unreadable."""
    reset = error.headers.get(RATE_LIMIT_RESET_HEADER)
    if reset is None or not EPOCH_SECONDS.fullmatch(reset):
        return None
    return int(reset)


def is_served_from(error: HTTPError, trusted_url_prefix: str) -> bool:
    """Whether the error answer came from the trusted prefix: urlopen follows redirections."""
    return error.geturl().startswith(trusted_url_prefix)


def is_rate_limit_exhausted(error: HTTPError) -> bool:
    return error.headers.get(RATE_LIMIT_REMAINING_HEADER) == "0"


def note_rate_limit_reset(error: HTTPError, trusted_url_prefix: str) -> None:
    """Note on the error when a rate limit with no request left resets: the log of the failed
    source gives it. An answer served from outside trusted_url_prefix is not read."""
    if not is_served_from(error, trusted_url_prefix) or not is_rate_limit_exhausted(
        error
    ):
        return
    reset = read_rate_limit_reset(error)
    if reset is None:
        error.add_note("rate limit reached, no request left")
        return
    reset_at = datetime.fromtimestamp(reset, UTC)
    error.add_note(f"rate limit reached, no request left until {reset_at.isoformat()}")
