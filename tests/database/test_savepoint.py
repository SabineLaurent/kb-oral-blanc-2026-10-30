"""The savepoint each item is written in: what it keeps, what it undoes, which errors are those
of an entry, and a refusal described without the value."""

import sqlite3

import pytest

from k_b.database.connection import connect_database
from k_b.database.savepoint import (
    EntryRefusedError,
    NoTransactionError,
    write_in_savepoint,
)

SCHEMA = """
CREATE TABLE parent (id INTEGER PRIMARY KEY) STRICT;
CREATE TABLE note (
    id INTEGER PRIMARY KEY,
    text TEXT NOT NULL UNIQUE CHECK (length(text) > 0),
    rank INTEGER CHECK (
        rank > 0
    ),
    parent_id INTEGER REFERENCES parent (id)
) STRICT;
"""


@pytest.fixture
def connection(tmp_path):
    connection = connect_database(tmp_path / "test.sqlite")
    connection.executescript(SCHEMA)
    # The import opens its transaction with its first write, before any savepoint.
    connection.execute("INSERT INTO note (id, text) VALUES (1, 'before')")
    yield connection
    connection.close()


def read_notes(connection):
    return [text for (text,) in connection.execute("SELECT text FROM note ORDER BY id")]


def test_what_is_written_in_a_savepoint_is_kept(connection):
    with write_in_savepoint(connection):
        connection.execute("INSERT INTO note (text) VALUES ('kept')")

    assert read_notes(connection) == ["before", "kept"]
    assert connection.in_transaction


def test_an_entry_refused_undoes_its_savepoint_alone_and_keeps_the_error_of_the_base(
    connection,
):
    with pytest.raises(EntryRefusedError) as refusal, write_in_savepoint(connection):
        connection.execute("INSERT INTO note (text) VALUES ('undone')")
        connection.execute("INSERT INTO note (text) VALUES ('')")

    assert isinstance(refusal.value.__cause__, sqlite3.IntegrityError)
    assert read_notes(connection) == ["before"]
    # The savepoint is released: the next one starts from a stack without it.
    with write_in_savepoint(connection):
        connection.execute("INSERT INTO note (text) VALUES ('after')")
    # No release committed anything: the rollback of the transaction undoes it all.
    connection.rollback()
    assert read_notes(connection) == []


# The errors due to the entry (conventions.md, rule 1), each with its description: the state
# of SQLite and its message on one line, or the type of the Python error, never the value.
# UNIQUE is tested here: the writers of K-B meet it only behind their ON CONFLICT or NOT
# EXISTS, as a safety net no import reaches.
@pytest.mark.parametrize(
    ("statement", "parameters", "description"),
    [
        (
            "INSERT INTO note (text) VALUES (?)",
            ("",),
            "SQLITE_CONSTRAINT_CHECK: CHECK constraint failed: length(text) > 0",
        ),
        (
            "INSERT INTO note (text, rank) VALUES ('ranked', ?)",
            (0,),
            "SQLITE_CONSTRAINT_CHECK: CHECK constraint failed: rank > 0",
        ),
        (
            "INSERT INTO note (text) VALUES (?)",
            (None,),
            "SQLITE_CONSTRAINT_NOTNULL: NOT NULL constraint failed: note.text",
        ),
        (
            "INSERT INTO note (text) VALUES (?)",
            ("before",),
            "SQLITE_CONSTRAINT_UNIQUE: UNIQUE constraint failed: note.text",
        ),
        (
            "INSERT INTO note (text, rank) VALUES ('ranked', ?)",
            (2**63,),
            "OverflowError: Python int too large to convert to SQLite INTEGER",
        ),
        (
            "INSERT INTO note (text) VALUES (?)",
            ("ab\ud800",),
            "UnicodeEncodeError: surrogates not allowed at position 2",
        ),
    ],
)
def test_an_error_due_to_the_entry_is_refused_and_described_without_its_value(
    connection, statement, parameters, description
):
    with pytest.raises(EntryRefusedError) as refusal, write_in_savepoint(connection):
        connection.execute(statement, parameters)

    assert str(refusal.value) == description
    assert read_notes(connection) == ["before"]


# Not due to the entry: a writer or a schema that does not match the base. They stop the
# import, raised unchanged. Named by their extended code: Python 3.13 names the type refused
# by a STRICT table "unknown" (observed on 2026-10-16, SQLite 3.50.4).
@pytest.mark.parametrize(
    ("statement", "error_code"),
    [
        (
            "INSERT INTO note (text, parent_id) VALUES ('orphan', 99)",
            787,  # SQLITE_CONSTRAINT_FOREIGNKEY
        ),
        (
            "INSERT INTO note (id, text) VALUES (1, 'same id')",
            1555,  # SQLITE_CONSTRAINT_PRIMARYKEY
        ),
        (
            "INSERT INTO note (text, rank) VALUES ('typed', 'not an integer')",
            3091,  # SQLITE_CONSTRAINT_DATATYPE
        ),
    ],
)
def test_another_constraint_of_the_base_is_undone_and_raised_unchanged(
    connection, statement, error_code
):
    with pytest.raises(sqlite3.IntegrityError) as error, write_in_savepoint(connection):
        connection.execute(statement)

    assert error.value.sqlite_errorcode == error_code
    assert read_notes(connection) == ["before"]


def test_an_error_after_the_base_undid_the_transaction_is_raised_unchanged(connection):
    # SQLite undoes the whole transaction on a full disk, for instance: no savepoint is left
    # to roll back to, and the error of the base is the one raised.
    with (
        pytest.raises(sqlite3.OperationalError, match="database or disk is full"),
        write_in_savepoint(connection),
    ):
        connection.rollback()
        raise sqlite3.OperationalError("database or disk is full")


def test_a_savepoint_outside_a_transaction_is_refused(tmp_path):
    connection = connect_database(tmp_path / "test.sqlite")
    try:
        # Outside a transaction, its release would commit what it wrote.
        with pytest.raises(NoTransactionError), write_in_savepoint(connection):
            pass
        assert not connection.in_transaction
    finally:
        connection.close()
