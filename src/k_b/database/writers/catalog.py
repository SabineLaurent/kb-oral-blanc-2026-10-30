"""The rule of a state catalog (CISA KEV, Zotero): the version of its latest collection is
kept."""

import sqlite3
from datetime import datetime
from typing import NamedTuple

from k_b.core.dates import format_date
from k_b.database.writers import shared_rows
from k_b.database.writers.discard import Discard
from k_b.items import Item

# State catalog: the collection of the latest catalog of the source already imported, and
# whether the file being imported was collected after it. That file is left out, unless it
# brought the version in the base: a replay, which the registry prevents since #106, kept for
# a base of before it, or for two imports at once.
SELECT_LATEST_CATALOG = """
SELECT max(raw_data.collected_at), :collected_at > max(raw_data.collected_at) FROM raw_data
JOIN source ON source.id = raw_data.source_id
WHERE source.source_code = :source_code
    AND (raw_data.id <> :raw_data_id
        OR raw_data.id = (SELECT raw_data_id FROM item WHERE id = :item_id))
"""


class LatestCatalog(NamedTuple):
    """The latest catalog of a source already imported, seen from the file being imported."""

    # Its collection, in the format of the base; None when the source has none yet.
    collected_at: str | None
    # Whether the file being imported was collected after it: 1 or 0; None when there is none.
    is_file_after: int | None


def read_latest(
    connection: sqlite3.Connection,
    source_code: str,
    collected_at: datetime,
    raw_data_id: int,
    item_id: int | None,
) -> LatestCatalog:
    """The latest catalog of the source already imported, the file being imported left out,
    unless it brought the version of item_id in the base."""
    collected_at_of_latest, is_file_after = connection.execute(
        SELECT_LATEST_CATALOG,
        {
            "source_code": source_code,
            "raw_data_id": raw_data_id,
            "item_id": item_id,
            "collected_at": format_date(collected_at),
        },
    ).fetchone()
    return LatestCatalog(collected_at_of_latest, is_file_after)


def find_withdrawn_at_of_new_row(latest: LatestCatalog) -> str | None:
    """A row brought only by a file older than the latest catalog is written withdrawn at the
    latest one."""
    if latest.is_file_after or latest.collected_at is None:
        return None
    return latest.collected_at


def build_discard(
    connection: sqlite3.Connection, item: Item, item_id: int, latest: LatestCatalog
) -> Discard:
    """The discard of an item the base did not write, naming which of the two conditions failed."""
    if latest.is_file_after:
        return Discard(
            "unchanged", item, shared_rows.read_item_collected_at(connection, item_id)
        )
    return Discard("no_newer_version", item, latest.collected_at)
