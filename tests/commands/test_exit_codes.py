"""The exit codes of kb: their values and their help."""

import pytest
import uvicorn.config

from k_b import cli
from k_b.commands.exit_codes import find_exit_code


def test_each_exit_code_keeps_its_value():
    assert {member.name: member.value for member in cli.ExitCode} == {
        "SUCCEEDED": 0,
        "FAILED": 1,
        "NOT_STARTED": 2,
        "SERVER_NOT_STARTED": 3,
        "PARTIAL_FAILURE": 4,
        "STOPPED_BY_SIGINT": 130,
        "STOPPED_BY_SIGTERM": 143,
    }


@pytest.mark.parametrize(
    ("has_failed", "has_done", "exit_code"),
    [
        (False, True, cli.ExitCode.SUCCEEDED),
        (False, False, cli.ExitCode.SUCCEEDED),
        (True, True, cli.ExitCode.PARTIAL_FAILURE),
        (True, False, cli.ExitCode.FAILED),
    ],
)
def test_a_part_that_failed_gives_4_beside_a_part_done_and_1_alone(
    has_failed, has_done, exit_code
):
    """The same result, the same code, for kb collect, kb write-tops and kb digest."""
    assert find_exit_code(has_failed=has_failed, has_done=has_done) == exit_code


def test_the_server_not_started_code_is_the_one_uvicorn_exits_with():
    assert cli.ExitCode.SERVER_NOT_STARTED == uvicorn.config.STARTUP_FAILURE


def test_kb_help_lists_each_exit_code_with_its_meaning_on_its_own_line(capsys):
    with pytest.raises(SystemExit) as stop:
        cli.main(["--help"])

    assert stop.value.code == 0
    lines = capsys.readouterr().out.splitlines()
    for member in cli.ExitCode:
        assert [str(member.value), member.meaning] in [
            line.split(maxsplit=1) for line in lines
        ]
