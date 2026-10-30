"""SIGINT (Ctrl-C): kb stops in one dated line and the code 130, never the traceback of Python."""

import os
import re
import signal
import subprocess
import sys
import threading
from functools import partial

import httpx2
from commands.cli_helpers import LOG_LINE_DATE, collected_kinds, list_hidden_files
from commands.test_sigterm import COLLECT_WAITING_FOR_SIGTERM
from etl.test_extract import WorkingSource
from post.test_ai_api_client import TEST_API_KEY

from k_b import cli
from k_b.commands import collect_command, digest_command
from k_b.post.ai_api_client import build_ai_api_client

STOPPED_LINE = rf"^{LOG_LINE_DATE} ERROR k_b\.cli: kb stopped by SIGINT$"

# The same kb collect, with the handler Python sets at its start only when SIGINT is not ignored,
# as it is in a job started in the background.
COLLECT_WAITING_FOR_SIGINT = (
    "import signal\nsignal.signal(signal.SIGINT, signal.default_int_handler)\n"
    + COLLECT_WAITING_FOR_SIGTERM
)


class InterruptedSource(WorkingSource):
    """A source during which Ctrl-C is pressed."""

    def fetch(self, last_collected_at):
        raise KeyboardInterrupt


def read_kb_log(data_dir):
    return (data_dir / "logs" / "kb.log").read_text("utf-8")


def assert_stopped_in_one_line(error_output, data_dir):
    assert re.search(STOPPED_LINE, error_output, re.MULTILINE)
    assert "Traceback" not in error_output
    assert re.search(STOPPED_LINE, read_kb_log(data_dir), re.MULTILINE)


def test_a_sigint_during_a_source_stops_kb_collect_and_keeps_the_collection_state(
    monkeypatch, tmp_path, capsys
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
        collected_kinds(InterruptedSource("stopped"), WorkingSource("after")),
    )
    capsys.readouterr()

    exit_code = cli.main(["collect"])

    assert exit_code == cli.ExitCode.STOPPED_BY_SIGINT
    assert_stopped_in_one_line(capsys.readouterr().err, tmp_path)
    assert (tmp_path / "collection_state.json").read_bytes() == state_before
    assert not (tmp_path / "raw" / "automated_watch" / "after").exists()
    assert list_hidden_files(tmp_path) == []


def test_a_sigint_while_kb_digest_waits_stops_it_and_its_wait(
    monkeypatch, data_and_vault_dirs, capsys
):
    data_dir, _ = data_and_vault_dirs
    monkeypatch.setenv("KB_AI_API_KEY_KB_DIGEST", TEST_API_KEY)

    def interrupt(request):
        raise KeyboardInterrupt

    monkeypatch.setattr(
        digest_command,
        "build_ai_api_client",
        partial(build_ai_api_client, transport=httpx2.MockTransport(interrupt)),
    )

    exit_code = cli.main(["digest"])

    assert exit_code == cli.ExitCode.STOPPED_BY_SIGINT
    assert_stopped_in_one_line(capsys.readouterr().err, data_dir)
    assert "wait-display" not in [thread.name for thread in threading.enumerate()]


def test_kb_collect_sent_sigint_in_its_own_process_exits_with_130(tmp_path):
    process = subprocess.Popen(  # noqa: S603 — runs this interpreter on a fixed script
        [sys.executable, "-c", COLLECT_WAITING_FOR_SIGINT],
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
        process.send_signal(signal.SIGINT)
        _, error_output = process.communicate(timeout=30)
    finally:
        process.kill()
        process.wait()

    assert process.returncode == cli.ExitCode.STOPPED_BY_SIGINT
    assert_stopped_in_one_line(error_output, tmp_path)
    assert not (tmp_path / "collection_state.json").exists()
