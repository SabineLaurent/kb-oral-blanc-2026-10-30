"""Read raw/ for the import: list the collections of each source folder with the reader of its
kind, then read a page as items, or a raw file of trends whole.

The one place of the import that reads raw/ outside etl/raw_store.py (.claude/rules/poc.md, rule
3)."""

import csv
import logging
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, NamedTuple

from pydantic import ValidationError

from k_b.core.quoting import quote_value, without_values
from k_b.etl.raw_files import (
    RawFile,
    RawReading,
    TrendFile,
    TrendReading,
    join_readings,
    parse_collection_name,
)
from k_b.etl.raw_store import RAW_DIR_NAMES
from k_b.etl.rejections import reject
from k_b.etl.source_contract import (
    SourceKind,
    SourceReader,
    TrendReader,
    find_source_kind,
)

logger = logging.getLogger(__name__)


class Collections(NamedTuple):
    """The collections of raw/ with their reader: as items (a raw file, or a folder of pages), or
    as trends."""

    of_items: list[tuple[Path, SourceReader[Any]]]
    of_trends: list[tuple[Path, TrendReader]]


def list_collections(raw_dir: Path, kinds: Sequence[SourceKind]) -> Collections:
    """Each collection with the reader of its kind, found by the name of its source folder,
    tracked or not."""
    of_items: list[tuple[Path, SourceReader[Any]]] = []
    of_trends: list[tuple[Path, TrendReader]] = []
    source_dirs = [
        source_dir
        for raw_dir_name in RAW_DIR_NAMES
        for source_dir in sorted((raw_dir / raw_dir_name).glob("*/"))
    ]
    for source_dir in source_dirs:
        kind = find_source_kind(source_dir.name, kinds)
        if kind is None:
            # The name of a folder of raw/ is not checked: it is quoted.
            logger.info(
                "folder without a source kind, skipped: %s",
                quote_value(source_dir.name),
            )
            continue
        item_reader = kind.reader
        trend_reader = kind.trend_reader
        # Listed only for a kind that has a reader: a folder left unread is never opened.
        if item_reader is not None:
            of_items.extend(
                (path, item_reader) for path in list_visible_entries(source_dir)
            )
        elif trend_reader is not None:
            of_trends.extend(
                (path, trend_reader) for path in list_visible_entries(source_dir)
            )
        else:
            # The rankings of the two GitHub tops: they do not enter the base (ADR of
            # 2026-10-07), and kb write-tops reads them from raw/.
            logger.info(
                "source without a reader, skipped: %s", quote_value(source_dir.name)
            )
    return Collections(of_items, of_trends)


def read_collection(
    collection_path: Path,
    raw_dir: Path,
    reader: SourceReader[Any],
    imported_paths: frozenset[str],
) -> RawReading:
    """One collection, read page by page: the raw files read, their rejections, and how many
    pages were opened; a page of imported_paths is not opened."""
    try:
        if collection_path.is_dir():
            # A folder name is taken whole: a file name without its extension.
            collected_at = parse_collection_name(collection_path.name)
            pages = list_pages(collection_path, raw_dir)
        else:
            collected_at = parse_collection_name(collection_path.stem)
            pages = [collection_path]
    except (ValueError, OSError) as error:
        relative_collection_path = collection_path.relative_to(raw_dir).as_posix()
        return RawReading(
            [],
            [reject(relative_collection_path, f"{type(error).__name__}: {error}")],
            0,
        )
    return join_readings(
        [
            read_page(page_path, raw_dir, reader, collected_at)
            for page_path in pages
            if page_path.relative_to(raw_dir).as_posix() not in imported_paths
        ]
    )


def read_page(
    page_path: Path, raw_dir: Path, reader: SourceReader[Any], collected_at: datetime
) -> RawReading:
    """One page, read whole: the raw file with its items, the entries rejected, and the one page
    opened.

    A page its reader cannot read is rejected and not registered, so the next import reads it
    again; an entry is rejected without its values."""
    raw_file_path = page_path.relative_to(raw_dir).as_posix()
    try:
        entry_reading = reader.read_entries(page_path.read_bytes())
    # RecursionError: a JSON nested too deep (#103); the page alone is rejected.
    except (ValueError, csv.Error, OSError, RecursionError) as error:
        # Not a file read: it is not registered, and is read again at the next import.
        return RawReading(
            [], [reject(raw_file_path, f"{type(error).__name__}: {error}")], 1
        )
    items = []
    rejections = [
        reject(raw_file_path, reason) for reason in entry_reading.rejection_reasons
    ]
    # The position counts the entries read: those rejected by the reader are not.
    for position, entry in enumerate(entry_reading.entries):
        try:
            items.append(reader.convert(entry, collected_at, raw_file_path))
        except ValidationError as error:
            rejections.append(
                reject(
                    raw_file_path,
                    f"read entry {position}: {type(error).__name__}: "
                    f"{without_values(error)}",
                )
            )
    raw_file = RawFile(
        raw_file_path, reader.source_code, collected_at, items, len(rejections)
    )
    return RawReading([raw_file], rejections, 1)


def read_trend_file(path: Path, raw_dir: Path, reader: TrendReader) -> TrendReading:
    """One raw file of trends, read whole: its rows, or one rejection; a rejected file is not
    registered, so the next import reads it again."""
    raw_file_path = path.relative_to(raw_dir).as_posix()
    if path.is_dir():
        return TrendReading([], [reject(raw_file_path, "a folder, not a raw file")], 0)
    try:
        collected_at = parse_collection_name(path.stem)
    except ValueError as error:
        reason = f"{type(error).__name__}: {error}"
        return TrendReading([], [reject(raw_file_path, reason)], 0)
    try:
        rows = reader.read_rows(path.read_bytes())
    except (ValueError, OSError) as error:
        # Counted as opened, as a page of items that cannot be read.
        reason = f"{type(error).__name__}: {error}"
        return TrendReading([], [reject(raw_file_path, reason)], 1)
    raw_file = RawFile(raw_file_path, reader.source_code, collected_at, [], 0)
    return TrendReading([TrendFile(raw_file, rows)], [], 1)


def list_pages(collection_dir: Path, raw_dir: Path) -> list[Path]:
    pages = []
    for path in list_visible_entries(collection_dir):
        if path.is_dir():
            logger.info(
                "folder inside a collection folder, skipped: %s",
                quote_value(path.relative_to(raw_dir).as_posix()),
            )
            continue
        pages.append(path)
    return pages


def list_visible_entries(folder: Path) -> list[Path]:
    """The entries of a folder in name order, hidden ones (.DS_Store, a rest of an interrupted
    write) left out."""
    return sorted(path for path in folder.iterdir() if not path.name.startswith("."))
