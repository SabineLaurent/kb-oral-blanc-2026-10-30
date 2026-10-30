"""How a value observed in an input is quoted in a log line or an error (conventions.md, rule 3)."""

import re

from pydantic import ValidationError
from pydantic_core import InitErrorDetails

# The length a value is cut to by default, quotes and escapes included.
MAX_QUOTED_VALUE_LENGTH = 100
# A part of the place of an error written as it is: the name of a field of a model. Any other, a
# key of a dict, comes from the input.
FIELD_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def quote_value(value: object, max_length: int = MAX_QUOTED_VALUE_LENGTH) -> str:
    """The value with its line breaks and control characters escaped by repr, cut to max_length:
    it cannot write a line of its own in a log."""
    return repr(value)[:max_length]


def without_values(error: ValidationError) -> ValidationError:
    """The same errors without the value each one received: field, type and message kept (poc.md,
    rule 5), so a validator's message must never quote the value.

    Each error is rebuilt by its type, which must be one pydantic knows: a validator raises a
    ValueError, never a PydanticCustomError, which this would refuse with a KeyError."""
    line_errors = []
    for detail in error.errors():
        line_error: InitErrorDetails = {
            "type": detail["type"],
            "loc": detail["loc"],
            "input": None,
        }
        if "ctx" in detail:
            line_error["ctx"] = detail["ctx"]
        line_errors.append(line_error)
    return ValidationError.from_exception_data(
        error.title, line_errors, hide_input=True
    )


def locate_first_error(error: ValidationError) -> str:
    """The place and the type of the first error, on one line, never its value, with the count
    when there are more; an error on the whole input has no place."""
    first = error.errors(include_url=False, include_context=False, include_input=False)[
        0
    ]
    place = ".".join(describe_place_part(part) for part in first["loc"]) or "input"
    count = error.error_count()
    more = f", {count} errors" if count > 1 else ""
    return f"{place} ({first['type']}){more}"


def describe_place_part(part: int | str) -> str:
    """A position or a field name as it is; another part, a key the input gave, quoted."""
    if isinstance(part, int) or FIELD_NAME.fullmatch(part):
        return str(part)
    return quote_value(part)
