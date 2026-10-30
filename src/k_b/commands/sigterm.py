"""The stop of kb collect, kb import, kb write-tops and kb digest on SIGTERM."""

import signal
from collections.abc import Iterator
from contextlib import contextmanager
from types import FrameType

from k_b.commands.exit_codes import ExitCode


class StoppedBySigtermError(SystemExit):
    """SIGTERM received, as docker stop sends before SIGKILL."""

    def __init__(self) -> None:
        super().__init__(ExitCode.STOPPED_BY_SIGTERM)


# The commands that stop on SIGTERM; kb serve-data and kb serve-ai keep the handler of uvicorn.
COMMANDS_STOPPED_BY_SIGTERM = frozenset({"collect", "import", "write-tops", "digest"})


@contextmanager
def stopping_on_sigterm(command: str) -> Iterator[None]:
    """Turn SIGTERM into StoppedBySigtermError, a SystemExit, so that the command undoes its work
    as on any exception; the handler found is set back on exit.

    A SIGTERM received during the commit of the import is raised once the commit has returned.
    Main thread only: Python sets a signal handler nowhere else."""
    if command not in COMMANDS_STOPPED_BY_SIGTERM:
        yield
        return
    previous_handler = signal.signal(signal.SIGTERM, raise_stopped_by_sigterm)
    try:
        yield
    finally:
        # None: a handler not set from Python, which signal.signal refuses.
        signal.signal(
            signal.SIGTERM,
            signal.SIG_DFL if previous_handler is None else previous_handler,
        )


def raise_stopped_by_sigterm(signal_number: int, frame: FrameType | None) -> None:
    # A second SIGTERM would stop the clean-up; Docker sends SIGKILL after its delay anyway.
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    raise StoppedBySigtermError
