"""The commit of an import and its log: both kept, or neither."""

import logging
import shutil
import sqlite3

import pytest
from load_helpers import (
    dump_database,
    list_logs,
    run_import,
)

from k_b.etl import load as import_items_module


def test_a_log_that_cannot_be_written_leaves_the_database_unchanged(
    data_dir, monkeypatch
):
    tables_before = dump_database(data_dir)

    def fail_to_write(path, content):
        raise OSError("disk full")

    monkeypatch.setattr("k_b.etl.import_log.write_file_atomically", fail_to_write)

    with pytest.raises(OSError, match="disk full"):
        run_import(data_dir)

    # The log is written before the commit: no file is registered, all are read again.
    assert dump_database(data_dir) == tables_before
    assert list_logs(data_dir) == []


class CommitFailingConnection(sqlite3.Connection):
    def commit(self):
        raise sqlite3.OperationalError("disk I/O error")


def test_a_commit_that_fails_leaves_neither_the_import_nor_its_log(
    data_dir, monkeypatch, connect_with_factory
):
    tables_before = dump_database(data_dir)
    monkeypatch.setattr(
        import_items_module,
        "connect_database",
        connect_with_factory(CommitFailingConnection),
    )

    with pytest.raises(sqlite3.OperationalError, match="disk I/O error"):
        run_import(data_dir)

    assert dump_database(data_dir) == tables_before
    assert list_logs(data_dir) == []


class CommitUndoneBySqliteConnection(sqlite3.Connection):
    """A commit refused that SQLite undoes itself (disk full, I/O error): no transaction is left."""

    def commit(self):
        self.rollback()
        raise sqlite3.OperationalError("database or disk is full")


def test_a_commit_undone_by_sqlite_leaves_neither_the_import_nor_its_log(
    data_dir, monkeypatch, connect_with_factory
):
    tables_before = dump_database(data_dir)
    monkeypatch.setattr(
        import_items_module,
        "connect_database",
        connect_with_factory(CommitUndoneBySqliteConnection),
    )

    with pytest.raises(sqlite3.OperationalError, match="disk is full"):
        run_import(data_dir)

    assert dump_database(data_dir) == tables_before
    assert list_logs(data_dir) == []


class InterruptedAfterCommitConnection(sqlite3.Connection):
    """A Ctrl-C handled once the commit has returned, as Python runs a signal handler after the C
    call."""

    def commit(self):
        super().commit()
        raise KeyboardInterrupt


def test_a_ctrl_c_once_the_base_has_committed_keeps_the_import_and_its_log(
    data_dir, monkeypatch, connect_with_factory, tmp_path_factory, caplog
):
    # The same import, not interrupted, on a copy: what the interrupted one must keep.
    reference_dir = tmp_path_factory.mktemp("reference")
    shutil.copytree(data_dir, reference_dir, dirs_exist_ok=True)
    run_import(reference_dir)
    monkeypatch.setattr(
        import_items_module,
        "connect_database",
        connect_with_factory(InterruptedAfterCommitConnection),
    )

    caplog.set_level(logging.INFO, logger="k_b.etl.load")

    with pytest.raises(KeyboardInterrupt):
        run_import(data_dir)

    assert dump_database(data_dir) == dump_database(reference_dir)
    [log_path] = list_logs(data_dir)
    [reference_log_path] = list_logs(reference_dir)
    assert log_path.name == reference_log_path.name
    assert log_path.read_bytes() == reference_log_path.read_bytes()
    assert f"import committed, log kept: '{log_path.name}'" in caplog.messages


def test_a_ctrl_c_after_the_log_is_written_leaves_neither_the_import_nor_its_log(
    data_dir, monkeypatch
):
    tables_before = dump_database(data_dir)
    write_import_log = import_items_module.write_import_log

    def write_then_interrupt(*arguments):
        write_import_log(*arguments)
        raise KeyboardInterrupt

    monkeypatch.setattr(import_items_module, "write_import_log", write_then_interrupt)

    with pytest.raises(KeyboardInterrupt):
        run_import(data_dir)

    assert dump_database(data_dir) == tables_before
    assert list_logs(data_dir) == []
