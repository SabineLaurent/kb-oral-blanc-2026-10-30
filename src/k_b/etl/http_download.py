"""Download one URL for a source: the network boundary every collection goes through."""

import logging
from time import sleep
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from k_b.core.config import HTTP_TIMEOUT_SECONDS
from k_b.core.quoting import quote_value
from k_b.etl.http_retry import (
    RETRY_WAIT_SECONDS,
    find_retry_wait,
    note_rate_limit_reset,
)
from k_b.etl.source_contract import PageRefusedError
from k_b.etl.user_agent import USER_AGENT

logger = logging.getLogger(__name__)


class RedirectedOutsideError(PageRefusedError):
    """The answer came from outside the trusted prefix; redirect_url says where it led, resolved
    against the requested url (tldr.tech redirects a day without an edition)."""

    def __init__(self, trusted_url_prefix: str, redirect_url: str):
        super().__init__(
            f"response redirected to {redirect_url}, outside {trusted_url_prefix}, refused"
        )
        self.redirect_url = redirect_url


def download(
    url: str,
    trusted_url_prefix: str,
    max_bytes: int,
    headers: dict[str, str] | None = None,
    unredirected_headers: dict[str, str] | None = None,
    rate_limited_wait_seconds: float = RETRY_WAIT_SECONDS,
) -> bytes:
    """Return the body of GET url, refused if served from outside trusted_url_prefix or too
    large; one retry after a 429, a 5xx or a failed connection (etl/http_retry.py), after
    rate_limited_wait_seconds for a 429 without Retry-After.

    An unredirected header (a token) is never forwarded to a redirection; an HTTPError not
    retried is left to the caller, who alone knows what a status such as 304 means."""
    check_trusted_url_prefix(url, trusted_url_prefix)
    try:
        return download_once(
            url, trusted_url_prefix, max_bytes, headers, unredirected_headers
        )
    except (URLError, ConnectionError) as error:
        wait = find_retry_wait(error, trusted_url_prefix, rate_limited_wait_seconds)
        if wait is None:
            raise
        # Without the query: the url of a source may carry anything there.
        logger.warning(
            "GET %s failed: %s; one retry in %s s",
            quote_value(urlsplit(url)._replace(query="", fragment="").geturl()),
            quote_value(str(error)),
            wait,
        )
        if isinstance(error, HTTPError):
            error.close()
        sleep(wait)
    return download_once(
        url, trusted_url_prefix, max_bytes, headers, unredirected_headers
    )


def download_once(
    url: str,
    trusted_url_prefix: str,
    max_bytes: int,
    headers: dict[str, str] | None,
    unredirected_headers: dict[str, str] | None,
) -> bytes:
    # https only: check_trusted_url_prefix refuses any other scheme.
    request = Request(url, headers={**(headers or {}), "User-Agent": USER_AGENT})  # noqa: S310
    for name, value in (unredirected_headers or {}).items():
        request.add_unredirected_header(name, value)
    try:
        with urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:  # noqa: S310
            # urlopen follows redirections, even to plain HTTP: only an answer from here is
            # trusted.
            if not response.url.startswith(trusted_url_prefix):
                raise RedirectedOutsideError(trusted_url_prefix, response.url)
            body: bytes = response.read(max_bytes + 1)
    except HTTPError as error:
        note_rate_limit_reset(error, trusted_url_prefix)
        raise
    if len(body) > max_bytes:
        raise PageRefusedError(f"response larger than {max_bytes} bytes, refused")
    return body


def check_trusted_url_prefix(url: str, trusted_url_prefix: str) -> None:
    """Refuse a prefix that would trust too much, or a url the prefix does not cover.

    The prefix names an https host and a path, so that "https://example.org.evil.com" cannot
    pass; no user info either: urlsplit reads "https://evil.com\\@good.com/" as good.com, urllib
    does not."""
    prefix_parts = urlsplit(trusted_url_prefix)
    is_prefix_strict = (
        prefix_parts.scheme == "https"
        and bool(prefix_parts.hostname)
        and "@" not in prefix_parts.netloc
        and prefix_parts.path.startswith("/")
    )
    if not is_prefix_strict:
        raise ValueError(
            f"trusted prefix {trusted_url_prefix!r} is not https://<host>/, refused"
        )
    if not url.startswith(trusted_url_prefix):
        raise ValueError(
            f"url outside its trusted prefix {trusted_url_prefix!r}, refused"
        )
