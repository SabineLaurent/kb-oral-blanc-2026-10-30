"""The plain-text table of the summary of kb digest, and the counts and durations of the batch
commands."""

from collections.abc import Sequence

COLUMN_GAP = "  "


def render_table(rows: Sequence[Sequence[str]]) -> list[str]:
    """One line per row, the first one the header, each column padded to its widest cell."""
    widths = [max(len(row[column]) for row in rows) for column in range(len(rows[0]))]
    return [
        COLUMN_GAP.join(
            cell.ljust(width) for cell, width in zip(row, widths, strict=True)
        ).rstrip()
        for row in rows
    ]


def render_rule(lines: Sequence[str]) -> str:
    """The rule under the rows of a table, as long as its longest line."""
    return "-" * max(len(line) for line in lines)


def describe_duration(seconds: float) -> str:
    """57 s, or 4 min 6 s."""
    minutes, seconds_left = divmod(round(seconds), 60)
    return f"{minutes} min {seconds_left} s" if minutes else f"{seconds_left} s"


def describe_count(count: int, noun: str) -> str:
    """1 source, 17 sources."""
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"
