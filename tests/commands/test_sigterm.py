"""SIGTERM: what kb collect and kb import keep when they are stopped."""

import json
import os
import re
import signal
import sqlite3
import subprocess
import sys
from datetime import datetime

from commands.cli_helpers import (
    LOG_LINE_DATE,
    collected_kinds,
    list_hidden_files,
)
from etl.test_extract import WorkingSource
from etl.test_transform import copy_fixture

from k_b import cli
from k_b.commands import (
    collect_command,
)
from k_b.etl import load


class SigtermSource(WorkingSource):
    """A source during which the container is stopped."""

    def fetch(self, last_collected_at):
        signal.raise_signal(signal.SIGTERM)
        return super().fetch(last_collected_at)


def dump_database(data_dir):
    """Every table, row by row, as SQL text, as in etl/test_load.py, which cannot be imported
    from here."""
    connection = sqlite3.connect(data_dir / "k-b.sqlite")
    try:
        return list(connection.iterdump())
    finally:
        connection.close()


def test_a_sigterm_after_the_import_log_is_written_leaves_neither_the_import_nor_its_log(
    monkeypatch, tmp_path, sigterm_handler_of_the_test
):
    monkeypatch.setenv("KB_DATA_DIR", str(tmp_path))
    cli.main(["create-db"])
    copy_fixture(tmp_path / "raw", "cisa_kev_extract.csv", "cisa_kev", "csv")
    tables_before = dump_database(tmp_path)
    write_import_log = load.write_import_log

    def write_then_receive_sigterm(log_path, *arguments):
        write_import_log(log_path, *arguments)
        assert log_path.is_file()
        signal.raise_signal(signal.SIGTERM)

    monkeypatch.setattr(load, "write_import_log", write_then_receive_sigterm)

    exit_code = cli.main(["import"])

    assert exit_code == cli.ExitCode.STOPPED_BY_SIGTERM
    assert dump_database(tmp_path) == tables_before
    assert list(tmp_path.glob("import-log-*.jsonl")) == []
    assert list_hidden_files(tmp_path) == []
    assert signal.getsignal(signal.SIGTERM) is sigterm_handler_of_the_test
    assert re.search(
        rf"^{LOG_LINE_DATE} ERROR k_b\.cli: kb stopped by SIGTERM$",
        (tmp_path / "logs" / "kb.log").read_text("utf-8"),
        re.MULTILINE,
    )


class SigtermAfterCommitConnection(sqlite3.Connection):
    """A SIGTERM received during the commit: its handler runs once the commit has returned."""

    def commit(self):
        super().commit()
        signal.raise_signal(signal.SIGTERM)


def test_a_sigterm_once_the_base_has_committed_keeps_the_import_and_its_log(
    monkeypatch, tmp_path, capsys, sigterm_handler_of_the_test, connect_with_factory
):
    monkeypatch.setenv("KB_DATA_DIR", str(tmp_path))
    cli.main(["create-db"])
    copy_fixture(tmp_path / "raw", "cisa_kev_extract.csv", "cisa_kev", "csv")
    # An unreadable page: a rejection, so that the log kept has a line.
    unreadable_path = copy_fixture(
        tmp_path / "raw", "cisa_kev_extract.csv", "github_advisories_pip", "json"
    )
    unreadable_path.write_text("not json", encoding="utf-8")
    capsys.readouterr()
    monkeypatch.setattr(
        load, "connect_database", connect_with_factory(SigtermAfterCommitConnection)
    )

    exit_code = cli.main(["import"])

    assert exit_code == cli.ExitCode.STOPPED_BY_SIGTERM
    connection = sqlite3.connect(tmp_path / "k-b.sqlite")
    assert connection.execute("SELECT count(*) FROM raw_data").fetchone() == (1,)
    connection.close()
    [log_path] = tmp_path.glob("import-log-*.jsonl")
    log_lines = [json.loads(line) for line in log_path.read_text("utf-8").splitlines()]
    assert [line["kind"] for line in log_lines] == ["rejection"]
    imported_at = datetime.fromisoformat(log_lines[0]["imported_at"])
    assert log_path.name == f"import-log-{imported_at:%Y-%m-%dT%H%M%SZ}.jsonl"
    output = capsys.readouterr()
    assert output.out == ""
    assert f"INFO k_b.etl.load: import committed, log kept: '{log_path.name}'" in (
        output.err
    )


def test_a_sigterm_during_a_source_stops_kb_collect_and_keeps_the_collection_state(
    monkeypatch, tmp_path, sigterm_handler_of_the_test
):
    monkeypatch.setenv("KB_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        collect_command, "SOURCE_KINDS", collected_kinds(WorkingSource())
    )
    cli.main(["collect"])
    state_before = (tmp_path / "collection_state.json").read_bytes()
    monkeypatch.setattr(
        collect_command,
        "SOURCE_KINDS",
        collected_kinds(SigtermSource("stopped"), WorkingSource("after")),
    )

    exit_code = cli.main(["collect"])

    assert exit_code == cli.ExitCode.STOPPED_BY_SIGTERM
    assert (tmp_path / "collection_state.json").read_bytes() == state_before
    assert not (tmp_path / "raw" / "automated_watch" / "after").exists()
    assert list_hidden_files(tmp_path) == []
    assert signal.getsignal(signal.SIGTERM) is sigterm_handler_of_the_test


# kb collect run as the kb script runs it, sys.exit(main()), with a source that says it has
# started, then waits for the signal.
COLLECT_WAITING_FOR_SIGTERM = """
import sys
import time

from k_b import cli
from k_b.commands import collect_command
from k_b.etl.source_contract import SourceKind


class WaitingSource:
    name = "waiting"
    extension = "json"
    acquisition_dir_name = "automated_watch"

    def fetch(self, last_collected_at):
        print("ready", flush=True)
        time.sleep(60)
        return b"{}"


collect_command.SOURCE_KINDS = (SourceKind("test", (WaitingSource(),), None),)
sys.exit(cli.main(["collect"]))
"""


def test_kb_collect_sent_sigterm_in_its_own_process_exits_with_143(tmp_path):
    process = subprocess.Popen(  # noqa: S603 — runs this interpreter on a fixed script
        [sys.executable, "-c", COLLECT_WAITING_FOR_SIGTERM],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={
            **{
                name: value
                for name, value in os.environ.items()
                if name != "KB_COLLECT_ONLY"
            },
            "KB_DATA_DIR": str(tmp_path),
        },
    )
    try:
        assert process.stdout.readline() == "ready\n"
        process.send_signal(signal.SIGTERM)
        _, error_output = process.communicate(timeout=30)
    finally:
        process.kill()
        process.wait()

    assert process.returncode == cli.ExitCode.STOPPED_BY_SIGTERM
    assert "ERROR k_b.cli: kb stopped by SIGTERM" in error_output
    assert not (tmp_path / "collection_state.json").exists()
