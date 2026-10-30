"""kb create-db: create the items database."""

import logging
import sqlite3
from pathlib import Path

from k_b.commands.exit_codes import ExitCode
from k_b.commands.run_log import LOGGER_NAME
from k_b.database.connection import DATABASE_FILE_NAME
from k_b.database.creation import create_database

logger = logging.getLogger(LOGGER_NAME)


def run_create_db(data_dir: Path) -> int:
    database_path = data_dir / DATABASE_FILE_NAME
    try:
        create_database(database_path)
    except FileExistsError as error:
        logger.error("%s", error)
        return ExitCode.NOT_STARTED
    except (sqlite3.Error, OSError) as error:
        logger.error("%s: %s: %s", database_path, type(error).__name__, error)
        return ExitCode.FAILED
    print(f"database created: {database_path}")
    return ExitCode.SUCCEEDED
