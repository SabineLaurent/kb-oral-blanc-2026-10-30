"""The fake HTTP error answers the tests of the downloads share."""

from email.message import Message
from urllib.error import HTTPError


def http_error(url, status, **headers):
    """An HTTPError served from url; a header name is given with _ for -."""
    message = Message()
    for name, value in headers.items():
        message[name.replace("_", "-")] = value
    return HTTPError(url, status, "error", message, None)
