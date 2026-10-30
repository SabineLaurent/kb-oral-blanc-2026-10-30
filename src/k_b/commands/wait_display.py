"""The wait of a command for an answer, shown on stderr: a spinner on a terminal, else a dated log
line every WAIT_LOG_INTERVAL_SECONDS (docs/fiches-veille.md, "Console")."""

import logging
import signal
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from itertools import cycle
from os import get_terminal_size
from time import monotonic
from typing import TextIO

from k_b.commands.run_log import LOGGER_NAME
from k_b.core.config import WAIT_LOG_INTERVAL_SECONDS

logger = logging.getLogger(LOGGER_NAME)

# The frames of the "dots" spinner, the most common in command-line tools.
SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
SPINNER_FRAME_SECONDS = 0.1
# The width of a terminal that says 0 columns (some pseudo-terminals): the fallback of
# shutil.get_terminal_size.
FALLBACK_COLUMNS = 80
WAIT_THREAD_NAME = "wait-display"
# Blocked in the thread of the wait, so that the system gives them to the main thread: only there do
# they stop the read of the answer, which would else last up to its timeout.
STOP_SIGNALS = {signal.SIGINT, signal.SIGTERM}


class SpinnerRow:
    """The wait on a terminal: one row rewritten in place, then cleared."""

    def __init__(self, stream: TextIO, waited_for: str) -> None:
        self.stream = stream
        self.waited_for = waited_for
        self.frames = cycle(SPINNER_FRAMES)
        self.period_seconds = SPINNER_FRAME_SECONDS

    def show(self, elapsed_seconds: int) -> None:
        # Short, so that it fits most windows: the read timeout is in the line logged before the
        # wait. A row the terminal has already folded on a resize stays behind: \r goes back to the
        # start of the last row only (docs/fiches-veille.md, "Console").
        line = f"{next(self.frames)} waiting for {self.waited_for}: {elapsed_seconds} s"
        # Cut to the width of the terminal: a longer line would wrap at each frame.
        self.write("\r" + line[: self.read_columns() - 1])

    def clear(self) -> None:
        # The whole row, so that the ^C a terminal echoes on a Ctrl-C goes too.
        self.write("\r" + " " * (self.read_columns() - 1) + "\r")

    def write(self, text: str) -> None:
        self.stream.write(text)
        self.stream.flush()

    def read_columns(self) -> int:
        # Of the stream itself: shutil.get_terminal_size would read COLUMNS first.
        return get_terminal_size(self.stream.fileno()).columns or FALLBACK_COLUMNS


class WaitLogLine:
    """The wait out of a terminal: a dated log line every WAIT_LOG_INTERVAL_SECONDS."""

    def __init__(self, waited_for: str, read_timeout_seconds: int) -> None:
        self.waited_for = waited_for
        self.read_timeout_seconds = read_timeout_seconds
        self.period_seconds = WAIT_LOG_INTERVAL_SECONDS

    def show(self, elapsed_seconds: int) -> None:
        logger.info(
            "still waiting for %s: %d seconds, read timeout %d seconds",
            self.waited_for,
            elapsed_seconds,
            self.read_timeout_seconds,
        )

    def clear(self) -> None:
        """Nothing to clear: a log line stays."""


@contextmanager
def showing_the_wait(waited_for: str, read_timeout_seconds: int) -> Iterator[None]:
    """Show that the block waits for waited_for, by a thread stopped and waited on any exit, Ctrl-C
    and SIGTERM included."""
    # Read once: the stream of the log handler of run_log.py, whose mode is chosen on it.
    stream = sys.stderr
    display = (
        SpinnerRow(stream, waited_for)
        if stream.isatty()
        else WaitLogLine(waited_for, read_timeout_seconds)
    )
    stopped = threading.Event()
    thread = threading.Thread(
        target=show_until_stopped,
        args=(display, monotonic(), stopped),
        name=WAIT_THREAD_NAME,
        daemon=True,
    )
    try:
        # In the try: a stop signal held during the start is raised once the mask is set back.
        start_without_stop_signals(thread)
        yield
    finally:
        stopped.set()
        # Not bounded, unlike the stop of ai_api/connectors/trace_export.py: Event.wait returns at
        # once and nothing goes through the network. Waited so that the row is cleared before what
        # follows, and no line is logged once run_log.py has removed its handlers.
        if thread.is_alive():
            thread.join()
        display.clear()


def start_without_stop_signals(thread: threading.Thread) -> None:
    """Start the thread with STOP_SIGNALS blocked, the mask it inherits from its first instruction;
    the mask of the main thread is set back at once."""
    previous_mask = signal.pthread_sigmask(signal.SIG_BLOCK, STOP_SIGNALS)
    try:
        thread.start()
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)


def show_until_stopped(
    display: SpinnerRow | WaitLogLine, started: float, stopped: threading.Event
) -> None:
    while not stopped.wait(display.period_seconds):
        display.show(round(monotonic() - started))
