"""The Retry-After header of an answer (RFC 9110, § 10.2.3): one reading for every client of K-B,
the collection (etl/http_retry.py), the call to the model (ai_api/connectors/llm_client.py) and
kb digest (post/ai_api_client.py)."""

import re

# Its delay-seconds form; its HTTP-date form, rare, is read as no header. Read up to 9 digits,
# about 31 years: int() refuses 4,301 digits or more, and a longer value is a wait beyond any
# maximum (review of #156).
MAX_RETRY_AFTER_DIGITS = 9
RETRY_AFTER_SECONDS = re.compile(rf"[0-9]{{1,{MAX_RETRY_AFTER_DIGITS}}}")
RETRY_AFTER_TOO_LONG = re.compile(rf"[0-9]{{{MAX_RETRY_AFTER_DIGITS + 1},}}")


def read_retry_after(value: str | None) -> int | None:
    """Its seconds; None when there is no header, a date, or a value too long to read (see
    is_retry_after_too_long)."""
    if value is None or not RETRY_AFTER_SECONDS.fullmatch(value):
        return None
    return int(value)


def is_retry_after_too_long(value: str | None) -> bool:
    """Seconds too many to read: a wait beyond any maximum, not a missing header."""
    return value is not None and RETRY_AFTER_TOO_LONG.fullmatch(value) is not None
