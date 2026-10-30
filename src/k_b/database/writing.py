"""Write the items database: the door of the import to database/writers/, which chooses the
writer of each kind (WRITER_BY_KIND).

Only objects pass the door, never a module of writers/: __all__ lists them, and mypy --strict
refuses any other name."""

import sqlite3
from collections.abc import Callable

from k_b.database.writers import (
    advisory_writer,
    clip_writer,
    newsletter_article_writer,
    paper_writer,
    release_writer,
    vulnerability_writer,
    zotero_entry_writer,
)
from k_b.database.writers.discard import Discard, DiscardRule
from k_b.database.writers.release_writer import write_tracked_repositories
from k_b.database.writers.shared_rows import UnknownSourceError, write_raw_data
from k_b.database.writers.trend_writer import DISCARD_RULE as TREND_DISCARD_RULE
from k_b.database.writers.trend_writer import write as write_trend_rows
from k_b.database.writers.vulnerability_writer import (
    mark_withdrawn as mark_withdrawn_vulnerabilities_not_in,
)
from k_b.database.writers.zotero_entry_writer import (
    mark_withdrawn as mark_withdrawn_zotero_rows_not_in,
)
from k_b.items import Item

__all__ = [
    "TREND_DISCARD_RULE",
    "WRITER_BY_KIND",
    "Discard",
    "DiscardRule",
    "KindWithoutTableError",
    "UnknownSourceError",
    "mark_withdrawn_vulnerabilities_not_in",
    "mark_withdrawn_zotero_rows_not_in",
    "write_item",
    "write_raw_data",
    "write_tracked_repositories",
    "write_trend_rows",
]


class KindWithoutTableError(LookupError):
    """An item's kind has no table in the base yet: the import refuses it."""


# The kinds that have a table in the base, each with its writer.
WRITER_BY_KIND: dict[str, Callable[[sqlite3.Connection, Item, int], Discard | None]] = {
    "paper": paper_writer.write,
    "vulnerability": vulnerability_writer.write,
    "release": release_writer.write,
    "advisory": advisory_writer.write,
    "newsletter_article": newsletter_article_writer.write,
    "zotero_entry": zotero_entry_writer.write,
    "clip": clip_writer.write,
}


def write_item(
    connection: sqlite3.Connection, item: Item, raw_data_id: int
) -> Discard | None:
    """Write the item of the raw data raw_data_id, or return why the base left it out."""
    write_kind = WRITER_BY_KIND.get(item.kind)
    if write_kind is None:
        raise KindWithoutTableError(f"kind without a table in the base: {item.kind}")
    return write_kind(connection, item, raw_data_id)
