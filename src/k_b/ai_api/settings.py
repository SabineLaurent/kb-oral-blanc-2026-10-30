"""The settings kb serve-ai reads: the host, the port and the default window of a digest request."""

import re
from collections.abc import Mapping
from dataclasses import dataclass

from k_b.core.api_settings import find_host_refusal, find_port_refusal
from k_b.core.config import (
    AI_API_HOST,
    AI_API_PORT,
    DIGEST_WINDOW_DAYS,
    MAX_DIGEST_WINDOW_DAYS,
    MIN_DIGEST_WINDOW_DAYS,
)
from k_b.core.quoting import quote_value

AI_API_HOST_VARIABLE = "KB_AI_API_HOST"
AI_API_PORT_VARIABLE = "KB_AI_API_PORT"
DIGEST_WINDOW_DAYS_VARIABLE = "KB_DIGEST_WINDOW_DAYS"
# Digits only, as the port: int() would also read " 3", "+3" and "0_3".
WINDOW_DAYS_DIGITS = re.compile(r"[0-9]{1,2}")


@dataclass(frozen=True)
class AiApiSettings:
    host: str
    port: int
    window_days: int


def read_ai_api_settings_or_refusal(environ: Mapping[str, str]) -> AiApiSettings | str:
    """The host, the port and the window of kb serve-ai, or why the variable is refused, its
    value quoted."""
    host = environ.get(AI_API_HOST_VARIABLE, AI_API_HOST)
    port = environ.get(AI_API_PORT_VARIABLE, str(AI_API_PORT))
    window_days = environ.get(DIGEST_WINDOW_DAYS_VARIABLE, str(DIGEST_WINDOW_DAYS))
    refusal = (
        find_host_refusal(AI_API_HOST_VARIABLE, host)
        or find_port_refusal(AI_API_PORT_VARIABLE, port)
        or find_window_days_refusal(window_days)
    )
    if refusal is not None:
        return refusal
    return AiApiSettings(host=host, port=int(port), window_days=int(window_days))


def find_window_days_refusal(window_days: str) -> str | None:
    if WINDOW_DAYS_DIGITS.fullmatch(window_days) and (
        MIN_DIGEST_WINDOW_DAYS <= int(window_days) <= MAX_DIGEST_WINDOW_DAYS
    ):
        return None
    return (
        f"{DIGEST_WINDOW_DAYS_VARIABLE}: {quote_value(window_days)} not an integer"
        f" from {MIN_DIGEST_WINDOW_DAYS} to {MAX_DIGEST_WINDOW_DAYS}"
    )
