"""Open a connection to the items database that checks its foreign keys."""

import sqlite3
from pathlib import Path

DATABASE_FILE_NAME = "k-b.sqlite"


class MissingDatabaseError(FileNotFoundError):
    pass


def check_database_exists(database_path: Path) -> None:
    """Connecting to a missing file would create an empty base."""
    if not database_path.is_file():
        raise MissingDatabaseError(f"{database_path} does not exist")


def connect_database(database_path: Path) -> sqlite3.Connection:
    """Open the database; SQLite ignores foreign keys unless each connection turns them on."""
    connection = sqlite3.connect(database_path)
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def connect_database_read_only(database_path: Path) -> sqlite3.Connection:
    """Open the database read only, one connection per request of the data API
    (check_same_thread=False).

    The base is checked first: mode=ro does not create a missing file, but its error does not
    tell it from another; as_uri escapes a space, # or ? of the path."""
    check_database_exists(database_path)
    connection = sqlite3.connect(
        f"{database_path.absolute().as_uri()}?mode=ro",
        uri=True,
        check_same_thread=False,
    )
    connection.execute("PRAGMA foreign_keys = ON")
    return connection
