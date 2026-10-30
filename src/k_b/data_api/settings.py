"""The settings kb serve-data reads: the host and the port it listens on, the hosts it answers."""

import re
from collections.abc import Mapping
from dataclasses import dataclass

from k_b.core.api_settings import find_host_refusal, find_port_refusal
from k_b.core.config import DATA_API_ALLOWED_HOSTS, DATA_API_HOST, DATA_API_PORT
from k_b.core.quoting import quote_value

# Where kb serve-data listens and the hosts it answers; not set: the defaults of core/config.py.
DATA_API_HOST_VARIABLE = "KB_DATA_API_HOST"
DATA_API_PORT_VARIABLE = "KB_DATA_API_PORT"
DATA_API_ALLOWED_HOSTS_VARIABLE = "KB_DATA_API_ALLOWED_HOSTS"
# A host name in lowercase, without * nor port: TrustedHostMiddleware compares the Host received
# as it is (starlette/middleware/trustedhost.py), and an entry of this form has nothing to escape
# in the log.
ALLOWED_HOST = re.compile(r"[a-z0-9]([a-z0-9.-]*[a-z0-9])?")


@dataclass(frozen=True)
class ServeSettings:
    host: str
    port: int
    allowed_hosts: tuple[str, ...]


def read_serve_settings_or_refusal(environ: Mapping[str, str]) -> ServeSettings | str:
    """The host, the port and the hosts allowed of kb serve-data, or why the variable is refused, its
    value quoted."""
    host = environ.get(DATA_API_HOST_VARIABLE, DATA_API_HOST)
    port = environ.get(DATA_API_PORT_VARIABLE, str(DATA_API_PORT))
    allowed_hosts_value = environ.get(DATA_API_ALLOWED_HOSTS_VARIABLE)
    # Split once: the list checked is the list served.
    allowed_hosts = (
        DATA_API_ALLOWED_HOSTS
        if allowed_hosts_value is None
        else split_allowed_hosts(allowed_hosts_value)
    )
    refusal = (
        find_host_refusal(DATA_API_HOST_VARIABLE, host)
        or find_port_refusal(DATA_API_PORT_VARIABLE, port)
        or find_allowed_hosts_refusal(allowed_hosts)
    )
    if refusal is not None:
        return refusal
    return ServeSettings(host=host, port=int(port), allowed_hosts=allowed_hosts)


def split_allowed_hosts(allowed_hosts: str) -> tuple[str, ...]:
    return tuple(entry.strip() for entry in allowed_hosts.split(","))


def find_allowed_hosts_refusal(allowed_hosts: tuple[str, ...]) -> str | None:
    """Why the hosts allowed are refused, or None: TrustedHostMiddleware would serve any host for
    a *."""
    # A value empty or made of spaces gives one empty entry.
    if allowed_hosts == ("",):
        return f"{DATA_API_ALLOWED_HOSTS_VARIABLE}: empty, set the hosts separated by commas"
    for position, entry in enumerate(allowed_hosts, start=1):
        where = f"{DATA_API_ALLOWED_HOSTS_VARIABLE}: entry {position} of {len(allowed_hosts)}"
        if not entry:
            return f"{where} empty"
        if "*" in entry:
            return f"{where} {quote_value(entry)} with '*', name each host"
        if not ALLOWED_HOST.fullmatch(entry):
            return f"{where} {quote_value(entry)} not a lowercase host name"
    return None
