"""The format of the dates of the items database."""

from datetime import UTC, date, datetime

# The date format the CHECK constraints of schema.sql accept.
DATABASE_DATE_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

# The dates of the base are compared as text: the format of its CHECK constraints keeps the
# order of the text the order of time.


def format_date(moment: datetime | None) -> str | None:
    return (
        None
        if moment is None
        else moment.astimezone(UTC).strftime(DATABASE_DATE_FORMAT)
    )


def format_day(day: date) -> str:
    """A day at midnight UTC, in the format of the base; isoformat, as strftime leaves the width
    of the year to the C library."""
    return f"{day.isoformat()}T00:00:00Z"
