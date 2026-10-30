"""Create the items database from its SQL scripts."""

from importlib.resources import files
from pathlib import Path

from k_b.database.connection import connect_database

DATABASE_SCRIPTS = files("k_b.database")


def create_database(database_path: Path) -> None:
    """Create the tables and the sources; refuse an existing file, and leave no file behind on
    failure: a half-built database would look valid and block the next attempt."""
    if database_path.exists():
        raise FileExistsError(f"{database_path} already exists")
    try:
        connection = connect_database(database_path)
        try:
            # Kept in the file: the pipeline writes while the API reads (docs/stockage.md).
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                DATABASE_SCRIPTS.joinpath("schema.sql").read_text()
            )
            connection.executescript(
                DATABASE_SCRIPTS.joinpath("seed_sources.sql").read_text()
            )
        finally:
            connection.close()
    except BaseException:
        # BaseException: Ctrl-C during the creation must not leave a half-built file either.
        for suffix in ("", "-wal", "-shm"):
            Path(f"{database_path}{suffix}").unlink(missing_ok=True)
        raise
