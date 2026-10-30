"""The two runs of the ETL, which talk through raw/: the collection (etl/extract.py) and the
import (etl/transform.py, then etl/load.py).

The only door of the two runs for commands/ (__all__): it reads no setting, prints nothing and
returns a named outcome its command prints."""

from collections.abc import Collection, Sequence
from datetime import datetime
from pathlib import Path
from typing import NamedTuple

from k_b.core.config import TrackedRepository
from k_b.database.connection import DATABASE_FILE_NAME
from k_b.etl.extract import CollectionOutcome, collect_all_sources
from k_b.etl.load import (
    ImportLogExistsError,
    ImportSummary,
    import_items,
    read_imported_paths,
)
from k_b.etl.raw_files import COLLECTION_NAME_FORMAT
from k_b.etl.raw_purge import delete_superseded_raw_files
from k_b.etl.raw_store import RAW_DIR_NAME, RawStore
from k_b.etl.source_contract import Source, SourceKind
from k_b.etl.transform import read_raw_files

__all__ = [
    "CollectionOutcome",
    "ImportLogExistsError",
    "ImportOutcome",
    "collect",
    "import_raw_files",
    "purge_superseded_raw",
]

# One log per import, named by its second as a raw file by its collection: never rewritten.
IMPORT_LOG_FILE_NAME = "import-log-{imported_at}.jsonl"


class ImportOutcome(NamedTuple):
    """What the import gives its command: what it wrote, and the log that details it."""

    summary: ImportSummary
    log_path: Path


def collect(sources: list[Source], data_dir: Path, now: datetime) -> CollectionOutcome:
    """Run the collection: each source given writes its raw files under raw/, then the collection
    state is written."""
    return collect_all_sources(sources, data_dir / RAW_DIR_NAME, data_dir, now)


def import_raw_files(
    data_dir: Path,
    kinds: Sequence[SourceKind],
    tracked_repositories: Sequence[TrackedRepository],
    imported_at: datetime,
) -> ImportOutcome:
    """Run the import: the raw files not in the registry of the base read in one walk of raw/,
    then written with the log in one transaction.

    The tracked repositories are given once, so that the reading and the base cannot read two
    lists."""
    database_path = data_dir / DATABASE_FILE_NAME
    log_path = data_dir / IMPORT_LOG_FILE_NAME.format(
        imported_at=imported_at.strftime(COLLECTION_NAME_FORMAT)
    )
    imported_paths = read_imported_paths(database_path)
    readings = read_raw_files(
        data_dir / RAW_DIR_NAME, kinds, imported_paths, tracked_repositories
    )
    summary = import_items(
        readings.items,
        readings.trends,
        database_path,
        log_path,
        imported_at,
        tracked_repositories,
    )
    return ImportOutcome(summary, log_path)


def purge_superseded_raw(
    data_dir: Path, latest_only_folder_keys: Collection[str]
) -> None:
    """Delete the raw files of the folders given that the last one imported superseded; the
    purge logs how many.

    Run after the import, so that a file the base registers for the first time can be the one
    kept."""
    delete_superseded_raw_files(
        RawStore(data_dir),
        read_imported_paths(data_dir / DATABASE_FILE_NAME),
        latest_only_folder_keys,
    )
