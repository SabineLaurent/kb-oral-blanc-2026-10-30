"""Read the raw files not imported yet into common items, reject the releases of a repository out
of the tracked list and normalize the items kept (docs/specs-collecte.md, steps 2 and 3)."""

from collections.abc import Sequence
from pathlib import Path
from typing import Any, NamedTuple

from k_b.core.config import TrackedRepository
from k_b.etl.normalize import normalize_item
from k_b.etl.raw_files import (
    RawFile,
    RawReading,
    TrendReading,
    join_readings,
    join_trend_readings,
)
from k_b.etl.raw_reading import (
    list_collections,
    read_collection,
    read_trend_file,
)
from k_b.etl.rejections import reject
from k_b.etl.source_contract import SourceKind, SourceReader, TrendReader
from k_b.items import Item


class ImportReadings(NamedTuple):
    """What one walk of raw/ gives the import: the files read as items, and those read as trends."""

    items: RawReading
    trends: TrendReading


def read_raw_files(
    raw_dir: Path,
    kinds: Sequence[SourceKind],
    imported_paths: frozenset[str],
    tracked_repositories: Sequence[TrackedRepository],
) -> ImportReadings:
    """Every raw file not in imported_paths read as its kind says, in one walk of raw/."""
    collections = list_collections(raw_dir, kinds)
    return ImportReadings(
        read_raw_items(
            collections.of_items, raw_dir, imported_paths, tracked_repositories
        ),
        read_raw_trends(collections.of_trends, raw_dir, imported_paths),
    )


def read_raw_items(
    collections: list[tuple[Path, SourceReader[Any]]],
    raw_dir: Path,
    imported_paths: frozenset[str],
    tracked_repositories: Sequence[TrackedRepository],
) -> RawReading:
    """Every collection of items not in imported_paths, in collection order, without the
    untracked releases, each item normalized."""
    reading = join_readings(
        [
            read_collection(collection_path, raw_dir, reader, imported_paths)
            for collection_path, reader in collections
        ]
    )
    # Stable sort: within a collection, the files keep their page order.
    reading.files.sort(key=lambda raw_file: raw_file.collected_at)
    # The filter first: it reads each item as its source gave it.
    kept = reject_untracked_releases(reading, tracked_repositories)
    return normalize_items(kept)


def reject_untracked_releases(
    reading: RawReading, tracked_repositories: Sequence[TrackedRepository]
) -> RawReading:
    """The same reading, without the releases of a repository out of the list, each one rejected;
    a file keeps its place even with no item left, so that the import registers it."""
    tracked_names = {repository.name.lower() for repository in tracked_repositories}
    files: list[RawFile] = []
    rejections = list(reading.rejections)
    for raw_file in reading.files:
        kept: list[Item] = []
        for item in raw_file.items:
            if is_untracked_release(item, tracked_names):
                rejections.append(
                    reject(
                        item.raw_file_path,
                        f"repository not in the tracked list: {item.repository_name}",
                    )
                )
                continue
            kept.append(item)
        # A release left out does not count in rejection_count, which stays the entries its
        # reader rejected or the item refused (raw_files.py): it was read whole, and no state
        # catalog holds a release, so no withdrawal depends on it (decision of Sabine of
        # 2026-10-22).
        files.append(raw_file._replace(items=kept))
    return RawReading(files, rejections, reading.read_count)


def is_untracked_release(item: Item, tracked_names: set[str]) -> bool:
    # A release always has its repository (items.py): str() only satisfies the typing.
    return (
        item.kind == "release"
        and str(item.repository_name).lower() not in tracked_names
    )


def normalize_items(reading: RawReading) -> RawReading:
    """The same reading, each item normalized.

    Outside the reading of a page, which turns a ValidationError into a rejection: the second
    validation of normalize_item must stop the whole import."""
    return reading._replace(
        files=[
            raw_file._replace(items=[normalize_item(item) for item in raw_file.items])
            for raw_file in reading.files
        ]
    )


def read_raw_trends(
    collections: list[tuple[Path, TrendReader]],
    raw_dir: Path,
    imported_paths: frozenset[str],
) -> TrendReading:
    """Every raw file of trends not in imported_paths, in collection order, each read whole."""
    # A raw file is named by its collection time: the order of the names is the order of time.
    return join_trend_readings(
        [
            read_trend_file(path, raw_dir, reader)
            for path, reader in collections
            if path.relative_to(raw_dir).as_posix() not in imported_paths
        ]
    )
