"""The wait of a command shown on stderr: a spinner on a terminal, else a log line."""

import io
import logging
import os
import signal
import sys
import threading

import pytest
from commands.cli_helpers import WaitLineSeen

from k_b.commands import wait_display
from k_b.commands.run_log import LOGGER_NAME

READ_TIMEOUT_SECONDS = 240
# Short, so that it fits most windows: the read timeout is in the line logged before the request.
FIRST_FRAME = "\r⠋ waiting for the digests: 0 s"


class FakeStream(io.StringIO):
    """sys.stderr of the tests: a terminal or not; written sets at the first frame."""

    def __init__(self, is_terminal):
        super().__init__()
        self.is_terminal = is_terminal
        self.written = threading.Event()

    def isatty(self):
        return self.is_terminal

    def fileno(self):
        # Read by get_terminal_size only, which the tests replace.
        return 2

    def write(self, text):
        length = super().write(text)
        if "waiting for" in text:
            self.written.set()
        return length


@pytest.fixture
def quick_wait(monkeypatch):
    """The periods made short, and a clock that never moves, so that each frame says 0 s."""
    monkeypatch.setattr(wait_display, "SPINNER_FRAME_SECONDS", 0.001)
    monkeypatch.setattr(wait_display, "WAIT_LOG_INTERVAL_SECONDS", 0.001)
    monkeypatch.setattr(wait_display, "monotonic", lambda: 0.0)


def use_stderr(monkeypatch, is_terminal, columns=80):
    stream = FakeStream(is_terminal)
    monkeypatch.setattr(sys, "stderr", stream)
    monkeypatch.setattr(
        wait_display, "get_terminal_size", lambda fd: os.terminal_size((columns, 24))
    )
    return stream


def thread_names():
    return [thread.name for thread in threading.enumerate()]


def cleared_row(columns):
    return "\r" + " " * (columns - 1) + "\r"


def test_on_a_terminal_a_spinner_turns_then_its_row_is_cleared(
    monkeypatch, quick_wait, caplog
):
    terminal = use_stderr(monkeypatch, is_terminal=True)

    with (
        caplog.at_level(logging.INFO, logger=LOGGER_NAME),
        wait_display.showing_the_wait("the digests", READ_TIMEOUT_SECONDS),
    ):
        shown_in_time = terminal.written.wait(5)

    assert shown_in_time
    assert terminal.getvalue().split("\r")[1] == FIRST_FRAME[1:]
    assert terminal.getvalue().endswith(cleared_row(80))
    assert caplog.messages == []


def test_on_a_narrow_terminal_each_frame_is_cut_to_its_width(monkeypatch, quick_wait):
    terminal = use_stderr(monkeypatch, is_terminal=True, columns=20)

    with wait_display.showing_the_wait("the digests", READ_TIMEOUT_SECONDS):
        shown_in_time = terminal.written.wait(5)

    assert shown_in_time
    frames = terminal.getvalue().removesuffix(cleared_row(20)).split("\r")[1:]
    assert frames[0] == FIRST_FRAME[1:20]
    assert all(len(frame) <= 19 for frame in frames)


def test_a_terminal_that_says_0_columns_is_taken_as_80_wide(monkeypatch, quick_wait):
    terminal = use_stderr(monkeypatch, is_terminal=True, columns=0)

    with wait_display.showing_the_wait("the digests", READ_TIMEOUT_SECONDS):
        shown_in_time = terminal.written.wait(5)

    assert shown_in_time
    assert terminal.getvalue().startswith(FIRST_FRAME)
    assert terminal.getvalue().endswith(cleared_row(80))


def test_the_thread_is_born_with_sigint_and_sigterm_blocked_and_the_main_thread_keeps_its_mask(
    monkeypatch,
):
    use_stderr(monkeypatch, is_terminal=False)
    masks_of_the_thread = []

    def keep_the_mask_then_wait(display, started, stopped):
        masks_of_the_thread.append(signal.pthread_sigmask(signal.SIG_BLOCK, []))
        stopped.wait()

    monkeypatch.setattr(wait_display, "show_until_stopped", keep_the_mask_then_wait)
    mask_before = signal.pthread_sigmask(signal.SIG_BLOCK, [])

    with wait_display.showing_the_wait("the digests", READ_TIMEOUT_SECONDS):
        mask_in_the_block = signal.pthread_sigmask(signal.SIG_BLOCK, [])

    assert {signal.SIGINT, signal.SIGTERM} <= masks_of_the_thread[0]
    assert mask_in_the_block == mask_before


def test_out_of_a_terminal_the_wait_is_a_log_line_and_no_frame(
    monkeypatch, quick_wait, caplog
):
    stream = use_stderr(monkeypatch, is_terminal=False)
    wait_line = WaitLineSeen()
    logger = logging.getLogger(LOGGER_NAME)
    logger.addHandler(wait_line)
    try:
        with (
            caplog.at_level(logging.INFO, logger=LOGGER_NAME),
            wait_display.showing_the_wait("the digests", READ_TIMEOUT_SECONDS),
        ):
            logged_in_time = wait_line.seen.wait(5)
    finally:
        logger.removeHandler(wait_line)

    assert logged_in_time
    assert caplog.messages[0] == (
        "still waiting for the digests: 0 seconds, read timeout 240 seconds"
    )
    assert stream.getvalue() == ""


def test_the_wait_stops_and_clears_its_row_when_the_block_raises(monkeypatch):
    terminal = use_stderr(monkeypatch, is_terminal=True)

    with (
        pytest.raises(LookupError),
        wait_display.showing_the_wait("the digests", READ_TIMEOUT_SECONDS),
    ):
        names_in_the_block = thread_names()
        raise LookupError

    assert "wait-display" in names_in_the_block
    assert "wait-display" not in thread_names()
    assert terminal.getvalue().endswith(cleared_row(80))
