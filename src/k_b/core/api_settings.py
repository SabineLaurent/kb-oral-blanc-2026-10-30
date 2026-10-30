"""Where kb serve-data and kb serve-ai listen: the host and the port, each read by its variable."""

import ipaddress
import re

from k_b.core.quoting import quote_value

# Digits only: int() would also read " 8000", "+8000" and "8_000".
PORT_DIGITS = re.compile(r"[0-9]{1,5}")
PORT_NUMBERS = range(1, 65536)


def find_host_refusal(variable: str, host: str) -> str | None:
    """Why the host is refused, or None. An IPv4 address only: uvicorn binds an empty host to
    every interface, and a client's IPv6 Host keeps brackets ([::1]) no allowed host can name."""
    try:
        ipaddress.IPv4Address(host)
    except ValueError:
        return f"{variable}: {quote_value(host)} not an IPv4 address"
    return None


def find_port_refusal(variable: str, port: str) -> str | None:
    if PORT_DIGITS.fullmatch(port) and int(port) in PORT_NUMBERS:
        return None
    return (
        f"{variable}: {quote_value(port)} not an integer"
        f" from {PORT_NUMBERS.start} to {PORT_NUMBERS.stop - 1}"
    )
