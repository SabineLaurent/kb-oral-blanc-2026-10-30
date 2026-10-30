"""The two types of a top ranking, shared by its source and by post/top_sheets.py."""

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class TopEntry:
    """A repository of a ranking, as its sheet shows it."""

    rank: int
    # owner/name, checked by the reader of its source.
    full_name: str
    # Built from the name by the source, never read in the response.
    url: str
    description: str | None
    # Its stars or its forks.
    count: int


@dataclass(frozen=True)
class Top:
    """A top ranking: the folder of its sheets under veille/tops/, the raw folder of its
    rankings, their reader, and the words of its sheet."""

    name: str
    folder_key: str
    read_entries: Callable[[bytes], list[TopEntry]]
    count_heading: str
    # What it ranks, after "Top <n> des".
    subject: str
