"""Write an item in a savepoint of the import's transaction: an entry the base refuses is undone
alone, and the import goes on (conventions.md, rule 1)."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

# The constraints an entry breaks by its own values, the only ones an import turns into a
# rejection (conventions.md, rule 1), with a value Python cannot hand to SQLite (an integer
# beyond 64 bits, a lone surrogate). A FOREIGN KEY, a PRIMARY KEY or a type of a STRICT table
# refused is not due to the entry: its writer chose the rows it points to, its key and the types
# of its values; the writer or the schema is wrong, and the import stops, as on an error of the
# system (full disk, locked base).
ENTRY_CONSTRAINT_ERROR_NAMES = frozenset(
    {"SQLITE_CONSTRAINT_CHECK", "SQLITE_CONSTRAINT_UNIQUE", "SQLITE_CONSTRAINT_NOTNULL"}
)


class EntryRefusedError(Exception):
    """The base refused an entry: what it refused, without the value refused."""


class NoTransactionError(RuntimeError):
    """A savepoint outside a transaction would begin one, and its release would commit it."""


@contextmanager
def write_in_savepoint(connection: sqlite3.Connection) -> Iterator[None]:
    """Keep what the block writes, or undo it alone on any exception; an error due to the entry
    is raised again as an EntryRefusedError.

    On some errors (a full disk, an I/O error) SQLite undoes the whole transaction: the error is
    then raised unchanged."""
    if not connection.in_transaction:
        raise NoTransactionError(
            "a savepoint is written inside the import's transaction"
        )
    connection.execute("SAVEPOINT entry")
    try:
        yield
    except BaseException as error:
        if connection.in_transaction:
            connection.execute("ROLLBACK TO SAVEPOINT entry")
            connection.execute("RELEASE SAVEPOINT entry")
        if is_entry_refusal(error):
            raise EntryRefusedError(describe_refusal(error)) from error
        raise
    connection.execute("RELEASE SAVEPOINT entry")


def is_entry_refusal(error: BaseException) -> bool:
    if isinstance(error, sqlite3.IntegrityError):
        return error.sqlite_errorname in ENTRY_CONSTRAINT_ERROR_NAMES
    return isinstance(error, (OverflowError, UnicodeEncodeError))


def describe_refusal(error: BaseException) -> str:
    """What the base refused, without the value refused: a constraint by its state and the
    message of SQLite, which names the constraint, never the value; a UnicodeEncodeError by its
    reason and position, as its text quotes the character."""
    if isinstance(error, sqlite3.IntegrityError):
        return f"{error.sqlite_errorname}: {' '.join(str(error).split())}"
    if isinstance(error, UnicodeEncodeError):
        return f"UnicodeEncodeError: {error.reason} at position {error.start}"
    return f"{type(error).__name__}: {error}"
