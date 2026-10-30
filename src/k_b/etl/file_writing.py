"""Write into the base what one raw file read holds: a file of items item by item, each in its
own savepoint, a file of trends whole.

The base removes the duplicates, by the rule of each source's writer (database/writers/); only
exact duplicates are found, the same identifier within its source (docs/specs-collecte.md)."""

import sqlite3
from dataclasses import dataclass, field

from k_b.core.quoting import quote_value
from k_b.database.savepoint import EntryRefusedError, write_in_savepoint
from k_b.database.writing import Discard, write_item, write_raw_data, write_trend_rows
from k_b.etl.import_log import TrendDiscard
from k_b.etl.raw_files import RawFile, Rejection, TrendFile
from k_b.etl.raw_store import find_stream
from k_b.etl.rejections import reject
from k_b.etl.withdrawals import (
    mark_withdrawn_vulnerabilities,
    mark_withdrawn_zotero_rows,
)
from k_b.items import Item


@dataclass(frozen=True)
class FileWriting:
    """What the writing of files gave: one file, or the sum of several.

    A dataclass with default_factory, not a NamedTuple: a NamedTuple's default list is built once
    and shared by every instance."""

    item_count: int = 0
    discards: list[Discard] = field(default_factory=list)
    trend_discards: list[TrendDiscard] = field(default_factory=list)
    rejections: list[Rejection] = field(default_factory=list)
    withdrawn_count: int = 0
    trend_count: int = 0


def write_file(
    connection: sqlite3.Connection, file_read: RawFile | TrendFile
) -> FileWriting:
    if isinstance(file_read, TrendFile):
        return write_trend_file(connection, file_read)
    return write_items_file(connection, file_read)


def write_items_file(connection: sqlite3.Connection, raw_file: RawFile) -> FileWriting:
    """Register the file read, write each of its items, then mark as withdrawn what its catalog
    no longer lists; the file is registered even with an item the base refuses."""
    raw_data_id = register_raw_file(connection, raw_file)
    outcomes = [
        write_item_in_savepoint(connection, item, raw_data_id)
        for item in raw_file.items
    ]
    discards = [outcome for outcome in outcomes if isinstance(outcome, Discard)]
    refusals = [outcome for outcome in outcomes if isinstance(outcome, Rejection)]
    # An item the base refuses is in its file: a catalog with one withdraws nothing.
    counted_file = raw_file._replace(
        rejection_count=raw_file.rejection_count + len(refusals)
    )
    withdrawn_count = mark_withdrawn_vulnerabilities(
        connection, counted_file, raw_data_id
    ) + mark_withdrawn_zotero_rows(connection, counted_file, raw_data_id)
    return FileWriting(
        item_count=outcomes.count(None),
        discards=discards,
        rejections=refusals,
        withdrawn_count=withdrawn_count,
    )


def write_trend_file(
    connection: sqlite3.Connection, trend_file: TrendFile
) -> FileWriting:
    """Register the file of trends and write its rows in one savepoint; return how many were
    written, and each window left out.

    A file with a row the base refuses is refused whole: not registered, it is read again by the
    next import."""
    raw_file_path = trend_file.raw_file.raw_file_path
    try:
        with write_in_savepoint(connection):
            raw_data_id = register_raw_file(connection, trend_file.raw_file)
            written_count, collected_at_by_window_left_out = write_trend_rows(
                connection, trend_file.rows, raw_data_id
            )
    except EntryRefusedError as refusal:
        rejection = reject(raw_file_path, f"ranking refused by the base: {refusal}")
        return FileWriting(rejections=[rejection])
    discards = [
        TrendDiscard(trend_file.raw_file, window_end, collected_at)
        for window_end, collected_at in collected_at_by_window_left_out.items()
    ]
    return FileWriting(trend_discards=discards, trend_count=written_count)


def register_raw_file(connection: sqlite3.Connection, raw_file: RawFile) -> int:
    """Register the file read under its stream, the name of its source folder."""
    return write_raw_data(
        connection,
        raw_file.source_code,
        raw_file.raw_file_path,
        find_stream(raw_file.raw_file_path),
        raw_file.collected_at,
    )


def write_item_in_savepoint(
    connection: sqlite3.Connection, item: Item, raw_data_id: int
) -> Discard | Rejection | None:
    """Write the item in its savepoint: an item the base refuses is returned as a rejection, one
    it left out as a discard."""
    try:
        with write_in_savepoint(connection):
            return write_item(connection, item, raw_data_id)
    except EntryRefusedError as refusal:
        return reject(
            item.raw_file_path,
            f"{item.kind} {quote_value(item.source_item_id)} refused by the base: "
            f"{refusal}",
        )
