"""The rows every writer shares: raw_data, the registry of the files read, and item."""

import sqlite3
from datetime import datetime

from k_b.core.dates import format_date
from k_b.items import ItemKind

SELECT_ITEM_COLLECTED_AT = """
SELECT raw_data.collected_at FROM item
JOIN raw_data ON raw_data.id = item.raw_data_id
WHERE item.id = ?
"""


class UnknownSourceError(LookupError):
    """An item's source_code has no row in the source table of the base."""


def write_raw_data(
    connection: sqlite3.Connection,
    source_code: str,
    raw_file_path: str,
    stream: str,
    collected_at: datetime,
) -> int:
    """Register the file read, even without item, so that the next import does not read it again."""
    source_row = connection.execute(
        "SELECT id FROM source WHERE source_code = ?", (source_code,)
    ).fetchone()
    if source_row is None:
        raise UnknownSourceError(f"source_code not in the source table: {source_code}")
    connection.execute(
        "INSERT INTO raw_data (source_id, raw_file_path, stream, collected_at)"
        " VALUES (?, ?, ?, ?) ON CONFLICT (raw_file_path) DO NOTHING",
        (source_row[0], raw_file_path, stream, format_date(collected_at)),
    )
    (raw_data_id,) = connection.execute(
        "SELECT id FROM raw_data WHERE raw_file_path = ?", (raw_file_path,)
    ).fetchone()
    return int(raw_data_id)


def insert_item(
    connection: sqlite3.Connection, kind: ItemKind, raw_data_id: int
) -> int:
    (item_id,) = connection.execute(
        "INSERT INTO item (kind, raw_data_id) VALUES (?, ?) RETURNING id",
        (kind, raw_data_id),
    ).fetchone()
    return int(item_id)


def point_item_at_raw_data(
    connection: sqlite3.Connection, item_id: int, raw_data_id: int
) -> None:
    """A known item now points to the raw data of its current version."""
    connection.execute(
        "UPDATE item SET raw_data_id = ? WHERE id = ?", (raw_data_id, item_id)
    )


def read_item_collected_at(connection: sqlite3.Connection, item_id: int) -> str:
    (collected_at,) = connection.execute(
        SELECT_ITEM_COLLECTED_AT, (item_id,)
    ).fetchone()
    return str(collected_at)
