"""kb digest: the digests asked of the AI API, the sheet of each subject written."""

import logging
import os
import re
import signal
import subprocess
import sys
from datetime import date
from functools import partial

import httpx2
import pytest
from commands.cli_helpers import (
    LOG_LINE_DATE,
    WaitLineSeen,
    list_hidden_files,
    list_sheets,
)
from post.test_ai_api_client import (
    AGENT_DIGEST,
    REQUEST_ID,
    TEST_API_KEY,
    make_response,
    to_body,
)

from k_b import cli
from k_b.ai_api.schemas.digest_schema import FailedSubjectSchema
from k_b.commands import (
    digest_command,
    wait_display,
)
from k_b.commands.run_log import LOGGER_NAME
from k_b.core.config import DigestSubject
from k_b.post import vault_files
from k_b.post.ai_api_client import build_ai_api_client

# The subjects of the tests, never the real ones, as in tests/ai_api/test_digest_router.py.
TEST_DIGEST_SUBJECTS = (
    DigestSubject("security_alerts", "failles et alertes de sécurité"),
    DigestSubject("agent_tools", "outils et cadres d'agents"),
    DigestSubject("research", "articles de recherche"),
)


# The UTC day of the run, the one of the end of the window of make_response.
DIGEST_DAY = date(2026, 10, 21)


RESEARCH_DIGEST = AGENT_DIGEST.model_copy(update={"subject": "research"})


RESEARCH_FAILED = FailedSubjectSchema(
    subject="research", stage="write", error="llm_timeout"
)


@pytest.fixture
def digest_environment(monkeypatch, data_and_vault_dirs):
    """kb digest with its key, the subjects of the tests and the day of the run."""
    monkeypatch.setenv("KB_AI_API_KEY_KB_DIGEST", TEST_API_KEY)
    monkeypatch.setattr(digest_command, "DIGEST_SUBJECTS", TEST_DIGEST_SUBJECTS)
    monkeypatch.setattr(digest_command, "read_today", lambda: DIGEST_DAY)
    return data_and_vault_dirs


def answer_digests(monkeypatch, handle):
    """The AI API of kb digest: handle answers each request, kept in the list returned."""
    requests = []

    def keep_and_handle(request):
        requests.append(request)
        return handle(request)

    monkeypatch.setattr(
        digest_command,
        "build_ai_api_client",
        partial(build_ai_api_client, transport=httpx2.MockTransport(keep_and_handle)),
    )
    return requests


def answer_with(response=None, status=200, content=None, headers=None):
    def handle(request):
        body = to_body(response or make_response()) if content is None else content
        return httpx2.Response(status, content=body, headers=headers)

    return handle


def read_kb_log(data_dir):
    return (data_dir / "logs" / "kb.log").read_text("utf-8")


def test_digest_writes_the_sheet_of_each_subject_and_says_so(
    monkeypatch, digest_environment, capsys
):
    _, vault_dir = digest_environment
    requests = answer_digests(
        monkeypatch, answer_with(make_response([AGENT_DIGEST, RESEARCH_DIGEST]))
    )

    exit_code = cli.main(["digest"])

    assert exit_code == cli.ExitCode.SUCCEEDED
    assert [(r.method, r.url.path) for r in requests] == [("POST", "/digests")]
    assert list_sheets(vault_dir) == [
        "veille/agent_tools/2026-10-21.md",
        "veille/research/2026-10-21.md",
    ]
    assert capsys.readouterr().out == (
        "\n"
        "Digest summary\n"
        "--------------\n"
        "Window: 2026-10-18 → 2026-10-21, 4 items read\n"
        "\n"
        "subject      status         sheet\n"
        "agent_tools  sheet written  veille/agent_tools/2026-10-21.md\n"
        "research     sheet written  veille/research/2026-10-21.md\n"
        f"{'-' * 60}\n"
        "total        written 2, failed 0\n"
        "\n"
        "Model: gpt-6-luna\n"
        f"Request id: {REQUEST_ID}\n"
        "\n"
    )


def test_digest_logs_its_start_and_its_end_with_the_duration(
    monkeypatch, digest_environment, capsys
):
    data_dir, _ = digest_environment
    answer_digests(monkeypatch, answer_with(make_response([AGENT_DIGEST])))

    assert cli.main(["digest"]) == cli.ExitCode.SUCCEEDED
    started = rf"^{LOG_LINE_DATE} INFO k_b\.cli: digest started: 3 subjects asked$"
    finished = rf"^{LOG_LINE_DATE} INFO k_b\.cli: digest finished in \d+ s$"
    err = capsys.readouterr().err
    for line in (started, finished):
        assert re.search(line, err, re.MULTILINE)
        assert re.search(line, read_kb_log(data_dir), re.MULTILINE)


def test_digest_without_digest_in_the_window_says_so_and_succeeds(
    monkeypatch, digest_environment, capsys
):
    _, vault_dir = digest_environment
    answer_digests(monkeypatch, answer_with(make_response([])))

    assert cli.main(["digest"]) == cli.ExitCode.SUCCEEDED
    assert "total    none in the window\n" in capsys.readouterr().out
    assert list_sheets(vault_dir) == []


def test_digest_with_a_sheet_of_the_day_asks_nothing_and_exits_2(
    monkeypatch, digest_environment, capsys
):
    data_dir, vault_dir = digest_environment
    requests = answer_digests(monkeypatch, answer_with())
    sheet = vault_dir / "veille" / "research" / "2026-10-21.md"
    sheet.parent.mkdir(parents=True)
    sheet.write_bytes(b"read by Sabine\n")

    assert cli.main(["digest"]) == cli.ExitCode.NOT_STARTED
    assert requests == []
    assert sheet.read_bytes() == b"read by Sabine\n"
    assert list_sheets(vault_dir) == ["veille/research/2026-10-21.md"]
    assert re.search(
        rf"^{LOG_LINE_DATE} ERROR k_b\.cli: no digest asked: sheet_exists:"
        r" veille/research/2026-10-21\.md is there; kb digest never rewrites a sheet$",
        read_kb_log(data_dir),
        re.MULTILINE,
    )
    output = capsys.readouterr()
    assert output.out == ""
    assert str(vault_dir) not in output.err


def test_digest_with_a_folder_linked_elsewhere_asks_nothing_and_exits_2(
    monkeypatch, digest_environment, tmp_path
):
    _, vault_dir = digest_environment
    requests = answer_digests(monkeypatch, answer_with())
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (vault_dir / "veille").symlink_to(elsewhere, target_is_directory=True)

    assert cli.main(["digest"]) == cli.ExitCode.NOT_STARTED
    assert requests == []
    assert list(elsewhere.iterdir()) == []


def test_digest_with_a_file_in_place_of_a_folder_asks_nothing_and_exits_1(
    monkeypatch, digest_environment, capsys
):
    _, vault_dir = digest_environment
    requests = answer_digests(monkeypatch, answer_with())
    (vault_dir / "veille").write_bytes(b"a file\n")

    assert cli.main(["digest"]) == cli.ExitCode.FAILED
    assert requests == []
    error_output = capsys.readouterr().err
    assert "no digest asked: NotADirectoryError (Not a directory)" in error_output
    assert str(vault_dir) not in error_output


@pytest.mark.parametrize("vault_dir_variable", [None, ""])
def test_digest_without_kb_vault_dir_asks_nothing_and_exits_2(
    monkeypatch, digest_environment, capsys, vault_dir_variable
):
    data_dir, _ = digest_environment
    if vault_dir_variable is None:
        monkeypatch.delenv("KB_VAULT_DIR")
    else:
        monkeypatch.setenv("KB_VAULT_DIR", vault_dir_variable)
    requests = answer_digests(monkeypatch, answer_with())

    assert cli.main(["digest"]) == cli.ExitCode.NOT_STARTED
    assert requests == []
    assert re.search(
        rf"^{LOG_LINE_DATE} ERROR k_b\.cli: KB_VAULT_DIR not set; the digests are written"
        " in the vault$",
        read_kb_log(data_dir),
        re.MULTILINE,
    )


def test_digest_with_a_vault_that_does_not_exist_creates_no_folder_and_exits_2(
    monkeypatch, digest_environment, tmp_path, capsys
):
    absent_vault = tmp_path / "typo"
    monkeypatch.setenv("KB_VAULT_DIR", str(absent_vault))
    requests = answer_digests(monkeypatch, answer_with())

    assert cli.main(["digest"]) == cli.ExitCode.NOT_STARTED
    assert requests == []
    assert not absent_vault.exists()
    error_output = capsys.readouterr().err
    assert "KB_VAULT_DIR is not an existing directory; no folder is created" in (
        error_output
    )
    assert str(absent_vault) not in error_output


def test_digest_without_its_key_asks_nothing_and_exits_2(
    monkeypatch, digest_environment, capsys
):
    monkeypatch.delenv("KB_AI_API_KEY_KB_DIGEST")
    requests = answer_digests(monkeypatch, answer_with())

    assert cli.main(["digest"]) == cli.ExitCode.NOT_STARTED
    assert requests == []
    assert "ERROR k_b.cli: KB_AI_API_KEY_KB_DIGEST: not set" in capsys.readouterr().err


def test_digest_without_the_ai_api_writes_nothing_and_exits_1(
    monkeypatch, digest_environment, capsys
):
    data_dir, vault_dir = digest_environment

    def refuse_the_connection(request):
        raise httpx2.ConnectError("refused", request=request)

    answer_digests(monkeypatch, refuse_the_connection)

    assert cli.main(["digest"]) == cli.ExitCode.FAILED
    assert list(vault_dir.iterdir()) == []
    assert re.search(
        rf"^{LOG_LINE_DATE} ERROR k_b\.cli: digests not received: ai_api_unavailable$",
        read_kb_log(data_dir),
        re.MULTILINE,
    )
    assert capsys.readouterr().out == ""


def test_digest_with_a_refused_key_writes_nothing_names_its_variable_never_the_key(
    monkeypatch, digest_environment, capsys
):
    """The request went out: 1, as a source that answers 401 to kb collect."""
    data_dir, vault_dir = digest_environment
    answer_digests(
        monkeypatch,
        answer_with(
            status=401,
            content=b'{"error": "invalid_api_key"}',
            headers={"X-Request-ID": REQUEST_ID, "WWW-Authenticate": "Bearer"},
        ),
    )

    assert cli.main(["digest"]) == cli.ExitCode.FAILED
    assert list(vault_dir.iterdir()) == []
    output = capsys.readouterr()
    assert (
        "digests not received: ai_api_unauthorized, HTTP 401, error invalid_api_key,"
        f" X-Request-ID {REQUEST_ID}; the AI API refused the key of KB_AI_API_KEY_KB_DIGEST"
    ) in output.err
    log = read_kb_log(data_dir)
    for text in (output.out, output.err, log):
        assert TEST_API_KEY not in text


def test_digest_on_a_failure_of_the_ai_api_exits_1_at_once_with_its_retry_after(
    monkeypatch, digest_environment, capsys
):
    _, vault_dir = digest_environment
    requests = answer_digests(
        monkeypatch,
        answer_with(
            status=503,
            content=b'{"error": "llm_rate_limited"}',
            headers={"X-Request-ID": REQUEST_ID, "Retry-After": "20"},
        ),
    )

    assert cli.main(["digest"]) == cli.ExitCode.FAILED
    assert len(requests) == 1
    assert list(vault_dir.iterdir()) == []
    assert (
        "digests not received: ai_api_failed, HTTP 503, error llm_rate_limited,"
        f" X-Request-ID {REQUEST_ID}, Retry-After 20\n"
    ) in capsys.readouterr().err


def test_digest_on_a_response_out_of_the_schema_writes_nothing_and_exits_1(
    monkeypatch, digest_environment, capsys
):
    _, vault_dir = digest_environment
    response = make_response([AGENT_DIGEST.model_copy(update={"subject": "tops"})])
    answer_digests(monkeypatch, answer_with(response))

    assert cli.main(["digest"]) == cli.ExitCode.FAILED
    assert list(vault_dir.iterdir()) == []
    assert (
        "digests not received: ai_api_invalid_response, HTTP 200,"
        f" digests.0.subject (subject_not_listed), request_id {REQUEST_ID}\n"
    ) in capsys.readouterr().err


def test_digest_with_failed_subjects_writes_the_others_names_them_and_exits_4(
    monkeypatch, digest_environment, capsys
):
    _, vault_dir = digest_environment
    answer_digests(
        monkeypatch, answer_with(make_response([AGENT_DIGEST], [RESEARCH_FAILED]))
    )

    assert cli.main(["digest"]) == cli.ExitCode.PARTIAL_FAILURE
    assert list_sheets(vault_dir) == ["veille/agent_tools/2026-10-21.md"]
    output = capsys.readouterr()
    assert "ERROR k_b.cli: research: digest failed: llm_timeout at the stage write" in (
        output.err
    )
    assert (
        "subject      status         sheet\n"
        "agent_tools  sheet written  veille/agent_tools/2026-10-21.md\n"
        "research     digest failed\n"
    ) in output.out
    assert "total        written 1, failed 1\n" in output.out


def test_digest_with_failed_subjects_only_exits_1(monkeypatch, digest_environment):
    _, vault_dir = digest_environment
    answer_digests(monkeypatch, answer_with(make_response([], [RESEARCH_FAILED])))

    assert cli.main(["digest"]) == cli.ExitCode.FAILED
    assert list_sheets(vault_dir) == []


def test_a_sheet_not_written_exits_4_and_the_other_sheet_is_written(
    monkeypatch, digest_environment, capsys
):
    _, vault_dir = digest_environment
    answer_digests(
        monkeypatch, answer_with(make_response([AGENT_DIGEST, RESEARCH_DIGEST]))
    )
    write_file_atomically = vault_files.write_file_atomically

    def refuse_the_agent_tools(path, content):
        if "agent_tools" in path.parts:
            raise PermissionError(13, "Permission denied", str(path))
        write_file_atomically(path, content)

    monkeypatch.setattr(vault_files, "write_file_atomically", refuse_the_agent_tools)

    assert cli.main(["digest"]) == cli.ExitCode.PARTIAL_FAILURE
    assert list_sheets(vault_dir) == ["veille/research/2026-10-21.md"]
    error_output = capsys.readouterr().err
    assert (
        "agent_tools: sheet not written: PermissionError (Permission denied)"
    ) in error_output
    assert str(vault_dir) not in error_output


def test_a_sigterm_while_a_sheet_is_written_stops_digest_and_keeps_the_sheet(
    monkeypatch, digest_environment, sigterm_handler_of_the_test
):
    _, vault_dir = digest_environment
    answer_digests(
        monkeypatch, answer_with(make_response([AGENT_DIGEST, RESEARCH_DIGEST]))
    )
    write_file_atomically = vault_files.write_file_atomically

    def write_then_receive_sigterm(path, content):
        write_file_atomically(path, content)
        signal.raise_signal(signal.SIGTERM)

    monkeypatch.setattr(
        vault_files, "write_file_atomically", write_then_receive_sigterm
    )

    exit_code = cli.main(["digest"])

    assert exit_code == cli.ExitCode.STOPPED_BY_SIGTERM
    assert list_sheets(vault_dir) == ["veille/agent_tools/2026-10-21.md"]
    assert list_hidden_files(vault_dir) == []
    assert signal.getsignal(signal.SIGTERM) is sigterm_handler_of_the_test


def test_a_slow_request_logs_that_the_digests_are_still_awaited(
    monkeypatch, digest_environment, capsys
):
    data_dir, _ = digest_environment
    monkeypatch.setattr(wait_display, "WAIT_LOG_INTERVAL_SECONDS", 0.001)
    wait_line = WaitLineSeen()
    seen_in_time = []

    def answer_once_the_wait_is_logged(request):
        seen_in_time.append(wait_line.seen.wait(5))
        return answer_with(make_response([AGENT_DIGEST]))(request)

    answer_digests(monkeypatch, answer_once_the_wait_is_logged)
    logger = logging.getLogger(LOGGER_NAME)
    logger.addHandler(wait_line)
    try:
        exit_code = cli.main(["digest"])
    finally:
        logger.removeHandler(wait_line)

    assert exit_code == cli.ExitCode.SUCCEEDED
    assert seen_in_time == [True]
    wait_line_logged = (
        rf"^{LOG_LINE_DATE} INFO k_b\.cli: still waiting for the digests:"
        r" \d+ seconds, read timeout 240 seconds$"
    )
    assert re.search(wait_line_logged, capsys.readouterr().err, re.MULTILINE)
    assert re.search(wait_line_logged, read_kb_log(data_dir), re.MULTILINE)


# kb digest run as the kb script runs it, sys.exit(main()), with an AI API that says the request
# has arrived, then never answers: the wait is shown by its thread while SIGTERM comes.
DIGEST_WAITING_FOR_SIGTERM = """
import sys
import time
from functools import partial

import httpx2

from k_b import cli
from k_b.commands import digest_command
from k_b.post.ai_api_client import build_ai_api_client


def wait_for_sigterm(request):
    print("ready", flush=True)
    time.sleep(60)
    return httpx2.Response(500)


digest_command.build_ai_api_client = partial(
    build_ai_api_client, transport=httpx2.MockTransport(wait_for_sigterm)
)
sys.exit(cli.main(["digest"]))
"""


def test_kb_digest_sent_sigterm_while_it_waits_exits_with_143(digest_environment):
    process = subprocess.Popen(  # noqa: S603 — runs this interpreter on a fixed script
        [sys.executable, "-c", DIGEST_WAITING_FOR_SIGTERM],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=dict(os.environ),
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


def test_kb_help_lists_digest(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--help"])

    assert re.search(
        r"^    digest +ask the AI API for the digests of the watch",
        capsys.readouterr().out,
        re.MULTILINE,
    )
