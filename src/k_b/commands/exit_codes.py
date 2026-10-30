"""The exit codes of kb, the same for every command."""

from enum import IntEnum
from typing import Self


class ExitCode(IntEnum):
    """The code kb exits with, the only way for a job in a container to know what kb did; listed
    by kb --help."""

    meaning: str

    def __new__(cls, value: int, meaning: str) -> Self:
        member = int.__new__(cls, value)
        member._value_ = value
        member.meaning = meaning
        return member

    SUCCEEDED = 0, "the command succeeded"
    FAILED = (
        1,
        (
            "the command ran and failed; kb collect, kb write-tops, kb digest: no part done,"
            " or kb collect: state not written"
        ),
    )
    NOT_STARTED = 2, "nothing was run: a setting, a file or the command line is refused"
    # Not returned by kb: uvicorn exits with it (STARTUP_FAILURE, uvicorn/config.py). Named here
    # so that no other code takes its value.
    SERVER_NOT_STARTED = 3, "kb serve-data, kb serve-ai: the server did not start"
    # 4: the first value after the one of uvicorn. A part is a source, a top or a subject
    # (README).
    PARTIAL_FAILURE = (
        4,
        (
            "kb collect, kb write-tops, kb digest: a part failed, another is done and kept;"
            " kb collect: state written"
        ),
    )
    # 128 + 2, the code a shell reads for a process stopped by SIGINT. A normal exit, not a death by
    # the signal as Python gives a Ctrl-C it leaves uncaught: under uv run, uv exits with 130 itself
    # (seen on 2026-10-28, #352). kb serve-data and kb serve-ai, once started, exit with 0: uvicorn.run
    # swallows the KeyboardInterrupt (uvicorn/main.py); during their start, with 130, 3 or 0.
    STOPPED_BY_SIGINT = (
        130,
        (
            "kb collect, kb import, kb write-tops, kb digest: stopped by SIGINT (Ctrl-C); what was"
            " done before it is kept; kb create-db: stopped by SIGINT, no database left"
        ),
    )
    # 128 + 15, the code of a process stopped by SIGTERM. kb serve-data and kb serve-ai exit with it
    # through uvicorn, which sends the signal again once stopped (uvicorn/server.py): a shell
    # reads 143, a parent process -15.
    STOPPED_BY_SIGTERM = (
        143,
        (
            "kb collect, kb import, kb write-tops, kb digest: stopped by SIGTERM; what was done"
            " before it is kept; kb serve-data, kb serve-ai: stopped by SIGTERM, outside PID 1"
        ),
    )


def find_exit_code(*, has_failed: bool, has_done: bool) -> ExitCode:
    """The code of a command made of parts: 4 when a part failed and another is done, 1 when none
    is."""
    if not has_failed:
        return ExitCode.SUCCEEDED
    return ExitCode.PARTIAL_FAILURE if has_done else ExitCode.FAILED
