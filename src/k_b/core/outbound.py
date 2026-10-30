"""The rules of a connection K-B opens by HTTP (AI API to the model and to the data API, kb
digest to the AI API): the URL it accepts and its HTTP client, in one place since both are
security rules."""

import re
from collections.abc import Mapping
from urllib.parse import urlsplit

import httpx2

# A host name in lowercase, as urlsplit gives it: neither an IPv6 address nor a character an HTTP
# client would refuse later.
HOST_NAME = re.compile(r"[a-z0-9]([a-z0-9.-]*[a-z0-9])?")


def find_url_refusal(variable: str, url: str, schemes: tuple[str, ...]) -> str | None:
    """Why the URL of the variable is refused, or None. The URL is never quoted: it may carry a
    key (`https://user:key@host`, `?api-key=…`)."""
    try:
        parts = urlsplit(url)
        has_user_information = parts.username is not None or parts.password is not None
        # Read for its check: a port out of 0 to 65535, or not digits, raises.
        parts.port  # noqa: B018
    except ValueError:
        return f"{variable}: not a URL"
    if parts.scheme not in schemes:
        return f"{variable}: scheme not {' nor '.join(schemes)}"
    if not parts.hostname:
        return f"{variable}: no host"
    if not HOST_NAME.fullmatch(parts.hostname):
        return f"{variable}: host not a lowercase host name"
    if has_user_information:
        return f"{variable}: with user information"
    if parts.query or parts.fragment or "?" in url or "#" in url:
        return f"{variable}: with a query or a fragment"
    return None


def open_http_client(
    base_url: str,
    headers: Mapping[str, str],
    timeout: httpx2.Timeout,
    transport: httpx2.BaseTransport | None,
) -> httpx2.Client:
    """A client that reads neither a proxy nor a certificate of the environment, and follows no
    redirection."""
    return httpx2.Client(
        base_url=base_url,
        headers=headers,
        timeout=timeout,
        follow_redirects=False,
        trust_env=False,
        transport=transport,
    )
