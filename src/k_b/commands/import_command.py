"""kb import: read the raw files not imported yet and write them into the database."""

import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic

from k_b.commands.batch_summary import describe_duration
from k_b.commands.exit_codes import ExitCode
from k_b.commands.run_log import LOGGER_NAME, describe_os_error
from k_b.core.config import GITHUB_TRACKED_REPOSITORIES
from k_b.core.quoting import quote_value
from k_b.database.connection import MissingDatabaseError
from k_b.database.writing import KindWithoutTableError, UnknownSourceError
from k_b.etl.pipeline import (
    ImportLogExistsError,
    ImportOutcome,
    import_raw_files,
    purge_superseded_raw,
)
from k_b.etl.raw_store import (
    AUTOMATED_WATCH_DIR_NAME,
    SELECTED_LIBRARY_DIR_NAME,
    make_source_folder_key,
)
from k_b.etl.source_kinds import SOURCE_KINDS
from k_b.etl.sources import cisa_kev, zotero

logger = logging.getLogger(LOGGER_NAME)

# The state catalogs: of each, only the last raw file imported is kept (ADR of 2026-10-19).
LATEST_ONLY_FOLDER_KEYS = (
    make_source_folder_key(SELECTED_LIBRARY_DIR_NAME, zotero.FOLDER_PREFIX),
    make_source_folder_key(AUTOMATED_WATCH_DIR_NAME, cisa_kev.FOLDER_PREFIX),
)


def run_import(data_dir: Path) -> int:
    """Run the import, print its summary, then purge the raw files superseded; before the purge,
    so that whatever stops it (an error, Ctrl-C) leaves the summary of the import committed."""
    imported_at = datetime.now(UTC).replace(microsecond=0)
    logger.info("import started")
    started = monotonic()
    try:
        outcome = import_raw_files(
            data_dir, SOURCE_KINDS, GITHUB_TRACKED_REPOSITORIES, imported_at
        )
    except MissingDatabaseError as error:
        logger.error("%s; create it with kb create-db", error)
        return ExitCode.NOT_STARTED
    except ImportLogExistsError as error:
        logger.error("%s; run kb import again", error)
        return ExitCode.NOT_STARTED
    except (
        sqlite3.Error,
        OSError,
        UnknownSourceError,
        KindWithoutTableError,
    ) as error:
        logger.error("%s: %s", type(error).__name__, error)
        return ExitCode.FAILED
    logger.info("import finished in %s", describe_duration(monotonic() - started))
    # Where the rejections are detailed (decision of Sabine of 2026-10-29); its name only, as
    # etl/load.py logs it: the path names the data directory.
    logger.info("import log kept: %s", quote_value(outcome.log_path.name))
    # A blank line before and after the result, apart from the log and from what follows (make's
    # line of an error): decisions of Sabine of 2026-10-29.
    print()
    print(describe_import_summary(outcome))
    print()
    try:
        purge_superseded_raw(data_dir, LATEST_ONLY_FOLDER_KEYS)
    except (sqlite3.Error, OSError, ValueError) as error:
        # The type, and the reason of an OSError, never the name of a raw file.
        logger.error(
            "import committed, raw purge failed: %s; run kb import again",
            describe_os_error(error)
            if isinstance(error, OSError)
            else type(error).__name__,
        )
        return ExitCode.FAILED
    return ExitCode.SUCCEEDED


def describe_import_summary(outcome: ImportOutcome) -> str:
    """The totals of the import on one line; the rejections are detailed in its log."""
    summary = outcome.summary
    return (
        f"import: {summary.read_count} raw read, {summary.item_count} written,"
        f" {summary.trend_count} trend rows, {summary.discard_count} left out,"
        f" {summary.rejection_count} rejected, {summary.withdrawn_count} withdrawn"
    )
