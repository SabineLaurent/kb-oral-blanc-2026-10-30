"""Import the raw files not imported yet into the items database, in one transaction with the log
of the import.

An entry the base refuses becomes a rejection and the import goes on; any other error writes
nothing in the base and leaves no log."""

import logging
import sqlite3
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import NamedTuple

from k_b.core.config import TrackedRepository
from k_b.core.quoting import quote_value
from k_b.database.connection import check_database_exists, connect_database
from k_b.database.reading import read_raw_file_paths
from k_b.database.writing import write_tracked_repositories
from k_b.etl.file_writing import FileWriting, write_file
from k_b.etl.import_log import write_import_log
from k_b.etl.raw_files import RawFile, RawReading, TrendFile, TrendReading

logger = logging.getLogger(__name__)


class ImportLogExistsError(FileExistsError):
    """The log of this second is already written: the log of an import is never rewritten."""


class ImportSummary(NamedTuple):
    item_count: int
    # The items and the windows of trends left out.
    discard_count: int
    rejection_count: int
    # The raw files opened, files of trends included.
    read_count: int
    withdrawn_count: int
    # The rows written to the trend table.
    trend_count: int


def read_imported_paths(database_path: Path) -> frozenset[str]:
    check_database_exists(database_path)
    connection = connect_database(database_path)
    try:
        return read_raw_file_paths(connection)
    finally:
        connection.close()


def import_items(
    reading: RawReading,
    trend_reading: TrendReading,
    database_path: Path,
    log_path: Path,
    imported_at: datetime,
    tracked_repositories: Sequence[TrackedRepository],
) -> ImportSummary:
    """Write the tracked repositories, each file read and the log, in one transaction; files of
    items and of trends in collection order, so that a rebuilt base registers them in the same
    order."""
    check_database_exists(database_path)
    connection = connect_database(database_path)
    try:
        try:
            write_tracked_repositories(connection, tracked_repositories)
            writing = write_files(connection, reading, trend_reading)
            commit_with_log(connection, log_path, imported_at, writing)
        except BaseException:
            # BaseException: Ctrl-C must not leave half an import either.
            connection.rollback()
            raise
    finally:
        connection.close()
    return ImportSummary(
        writing.item_count,
        len(writing.discards) + len(writing.trend_discards),
        len(writing.rejections),
        reading.read_count + trend_reading.read_count,
        writing.withdrawn_count,
        writing.trend_count,
    )


def write_files(
    connection: sqlite3.Connection, reading: RawReading, trend_reading: TrendReading
) -> FileWriting:
    """Write each file in collection order; what the reading rejected comes first."""
    rejected_when_read = FileWriting(
        rejections=[*reading.rejections, *trend_reading.rejections]
    )
    return join_writings(
        [
            rejected_when_read,
            *(
                write_file(connection, file_read)
                for file_read in list_files_in_collection_order(reading, trend_reading)
            ),
        ]
    )


def commit_with_log(
    connection: sqlite3.Connection,
    log_path: Path,
    imported_at: datetime,
    writing: FileWriting,
) -> None:
    """Write the log, then commit: a file registered is not read again, so its log must not be
    lost. A failed commit, or an interruption before it, removes the log; an interruption
    (Ctrl-C, SIGTERM) once the base has committed keeps the import and its log."""
    # Checked once the base holds the write lock: two imports of the same second cannot both
    # find no log.
    check_log_absent(log_path)
    try:
        write_import_log(
            log_path,
            imported_at,
            writing.rejections,
            writing.discards,
            writing.trend_discards,
        )
        connection.commit()
    except BaseException as error:
        # The log goes with the import it describes. A commit that fails raises a sqlite3.Error,
        # whether SQLite keeps the transaction or undoes it itself. A Ctrl-C or a SIGTERM
        # received before the commit finds the transaction open; one received during the commit
        # is raised by Python once the commit has returned, with no transaction left.
        if isinstance(error, sqlite3.Error) or connection.in_transaction:
            # The log at log_path can only be this import's: it was absent above.
            log_path.unlink(missing_ok=True)
        else:
            # The name, not the path: quote_value cuts a long path before the name.
            logger.info("import committed, log kept: %s", quote_value(log_path.name))
        raise


def check_log_absent(log_path: Path) -> None:
    if log_path.exists():
        raise ImportLogExistsError(f"{log_path} already exists")


def list_files_in_collection_order(
    reading: RawReading, trend_reading: TrendReading
) -> list[RawFile | TrendFile]:
    """The files of items and those of trends, oldest collection first; at the same collection,
    the files of items first (the sort is stable)."""

    def find_collected_at(file_read: RawFile | TrendFile) -> datetime:
        """A file of trends holds its collection time on the raw file it registers."""
        if isinstance(file_read, TrendFile):
            return file_read.raw_file.collected_at
        return file_read.collected_at

    return sorted([*reading.files, *trend_reading.files], key=find_collected_at)


def join_writings(writings: list[FileWriting]) -> FileWriting:
    """The sum of several writings, the discards and the rejections in the order given."""
    return FileWriting(
        item_count=sum(writing.item_count for writing in writings),
        discards=[discard for writing in writings for discard in writing.discards],
        trend_discards=[
            discard for writing in writings for discard in writing.trend_discards
        ],
        rejections=[
            rejection for writing in writings for rejection in writing.rejections
        ],
        withdrawn_count=sum(writing.withdrawn_count for writing in writings),
        trend_count=sum(writing.trend_count for writing in writings),
    )
