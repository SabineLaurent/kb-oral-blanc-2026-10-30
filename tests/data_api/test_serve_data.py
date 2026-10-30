"""kb serve-data and kb openapi: the keys and the settings checked at the start, the hosts allowed,
the log, the OpenAPI written without data directory."""

import os
import secrets
import signal
import sys
from pathlib import Path

import pytest
import uvicorn.config
from fastapi.testclient import TestClient

from k_b import cli
from k_b.commands import serve_data_command

VERSIONED_OPENAPI = Path(__file__).parents[2] / "docs" / "api" / "openapi-donnees.json"


@pytest.fixture(autouse=True)
def no_api_key_of_the_terminal(monkeypatch):
    """A terminal that exports a key must not give it to the tests."""
    for name in os.environ:
        if name.startswith("KB_DATA_API_KEY_"):
            monkeypatch.delenv(name)


@pytest.fixture
def uvicorn_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(
        serve_data_command.uvicorn,
        "run",
        lambda app, **options: calls.append((app, options)),
    )
    return calls


@pytest.fixture
def data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("KB_DATA_DIR", str(tmp_path))
    return tmp_path


def make_key(length=43):
    """A key of the test; token_urlsafe(32) gives 43 characters."""
    return secrets.token_urlsafe(32)[:length]


@pytest.mark.parametrize(
    ("variables", "message"),
    [
        ({}, "no API key: set KB_DATA_API_KEY_<CLIENT>"),
        (
            {"KB_DATA_API_KEY_": make_key()},
            "'KB_DATA_API_KEY_': client name empty or not [A-Z0-9_]+",
        ),
        (
            {"KB_DATA_API_KEY_terminal": make_key()},
            "'KB_DATA_API_KEY_terminal': client name empty or not [A-Z0-9_]+",
        ),
        (
            {"KB_DATA_API_KEY_TERMINAL": make_key(31)},
            "'KB_DATA_API_KEY_TERMINAL': key of 31 characters, minimum 32",
        ),
        (
            {"KB_DATA_API_KEY_TERMINAL": ""},
            "'KB_DATA_API_KEY_TERMINAL': key of 0 characters, minimum 32",
        ),
        # HTTPBearer strips the spaces around the key received: this key would never match.
        (
            {"KB_DATA_API_KEY_TERMINAL": make_key() + " "},
            "'KB_DATA_API_KEY_TERMINAL': key with a character outside [A-Za-z0-9_-]",
        ),
    ],
)
def test_serve_exits_2_and_names_the_variable_without_its_value(
    monkeypatch, capsys, data_dir, uvicorn_calls, variables, message
):
    for name, value in variables.items():
        monkeypatch.setenv(name, value)

    exit_code = cli.main(["serve-data"])

    error_output = capsys.readouterr().err
    assert exit_code == 2
    assert message in error_output
    for value in variables.values():
        if value:
            assert value not in error_output
    assert uvicorn_calls == []


def test_serve_exits_2_when_two_clients_have_the_same_key(
    monkeypatch, capsys, data_dir, uvicorn_calls
):
    key = make_key()
    monkeypatch.setenv("KB_DATA_API_KEY_TERMINAL", key)
    monkeypatch.setenv("KB_DATA_API_KEY_AGENT", key)

    exit_code = cli.main(["serve-data"])

    error_output = capsys.readouterr().err
    assert exit_code == 2
    assert (
        "'KB_DATA_API_KEY_AGENT' and 'KB_DATA_API_KEY_TERMINAL': same key"
        in error_output
    )
    assert key not in error_output
    assert uvicorn_calls == []


def test_serve_logs_the_names_of_the_clients_never_their_keys(
    monkeypatch, capsys, data_dir, uvicorn_calls
):
    keys = {"KB_DATA_API_KEY_TERMINAL": make_key(), "KB_DATA_API_KEY_AGENT": make_key()}
    for name, value in keys.items():
        monkeypatch.setenv(name, value)

    exit_code = cli.main(["serve-data"])

    error_output = capsys.readouterr().err
    assert exit_code == 0
    assert "API clients loaded: AGENT, TERMINAL" in error_output
    for value in keys.values():
        assert value not in error_output


def test_serve_listens_on_127_0_0_1_8000_by_default_without_uvicorn_logging_config(
    monkeypatch, data_dir, uvicorn_calls
):
    monkeypatch.setenv("KB_DATA_API_KEY_TERMINAL", make_key())

    cli.main(["serve-data"])

    [(_, options)] = uvicorn_calls
    assert options == {
        "host": "127.0.0.1",
        "port": 8000,
        "log_config": None,
        "access_log": False,
    }


def test_serve_listens_on_the_host_and_port_of_the_environment(
    monkeypatch, data_dir, uvicorn_calls
):
    monkeypatch.setenv("KB_DATA_API_KEY_TERMINAL", make_key())
    monkeypatch.setenv("KB_DATA_API_HOST", "192.0.2.10")
    monkeypatch.setenv("KB_DATA_API_PORT", "8080")

    cli.main(["serve-data"])

    [(_, options)] = uvicorn_calls
    assert (options["host"], options["port"]) == ("192.0.2.10", 8080)


@pytest.mark.parametrize(
    ("variable", "value", "message"),
    [
        # uvicorn would bind an empty host to every interface.
        ("KB_DATA_API_HOST", "", "KB_DATA_API_HOST: '' not an IPv4 address"),
        # A Host in IPv6 keeps its brackets, which no allowed host can name: never reached.
        ("KB_DATA_API_HOST", "::1", "KB_DATA_API_HOST: '::1' not an IPv4 address"),
        (
            "KB_DATA_API_HOST",
            "api-donnees",
            "KB_DATA_API_HOST: 'api-donnees' not an IPv4 address",
        ),
        ("KB_DATA_API_PORT", "", "KB_DATA_API_PORT: '' not an integer from 1 to 65535"),
        (
            "KB_DATA_API_PORT",
            "abc",
            "KB_DATA_API_PORT: 'abc' not an integer from 1 to 65535",
        ),
        # int() would read these three.
        (
            "KB_DATA_API_PORT",
            " 8000",
            "KB_DATA_API_PORT: ' 8000' not an integer from 1 to 65535",
        ),
        (
            "KB_DATA_API_PORT",
            "+8000",
            "KB_DATA_API_PORT: '+8000' not an integer from 1 to 65535",
        ),
        (
            "KB_DATA_API_PORT",
            "8_000",
            "KB_DATA_API_PORT: '8_000' not an integer from 1 to 65535",
        ),
        (
            "KB_DATA_API_PORT",
            "0",
            "KB_DATA_API_PORT: '0' not an integer from 1 to 65535",
        ),
        (
            "KB_DATA_API_PORT",
            "65536",
            "KB_DATA_API_PORT: '65536' not an integer from 1 to 65535",
        ),
    ],
)
def test_serve_exits_2_on_a_host_or_port_refused(
    monkeypatch, capsys, data_dir, uvicorn_calls, variable, value, message
):
    monkeypatch.setenv("KB_DATA_API_KEY_TERMINAL", make_key())
    monkeypatch.setenv(variable, value)

    exit_code = cli.main(["serve-data"])

    assert exit_code == 2
    assert message in capsys.readouterr().err
    assert uvicorn_calls == []


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("", "KB_DATA_API_ALLOWED_HOSTS: empty, set the hosts separated by commas"),
        ("  ", "KB_DATA_API_ALLOWED_HOSTS: empty, set the hosts separated by commas"),
        (" , ", "KB_DATA_API_ALLOWED_HOSTS: entry 1 of 2 empty"),
        ("localhost,", "KB_DATA_API_ALLOWED_HOSTS: entry 2 of 2 empty"),
        ("*", "KB_DATA_API_ALLOWED_HOSTS: entry 1 of 1 '*' with '*', name each host"),
        (
            "localhost,*.example.com",
            "KB_DATA_API_ALLOWED_HOSTS: entry 2 of 2 '*.example.com' with '*', name each host",
        ),
        (
            "API-donnees",
            "KB_DATA_API_ALLOWED_HOSTS: entry 1 of 1 'API-donnees' not a lowercase host name",
        ),
        (
            "localhost:8000",
            "KB_DATA_API_ALLOWED_HOSTS: entry 1 of 1 'localhost:8000' not a lowercase host name",
        ),
        # A line break would write a line of its own in the log: quoted, it cannot.
        (
            "localhost\nfake",
            "KB_DATA_API_ALLOWED_HOSTS: entry 1 of 1 'localhost\\nfake' not a lowercase host name",
        ),
    ],
)
def test_serve_exits_2_and_names_the_allowed_hosts_refused(
    monkeypatch, capsys, data_dir, uvicorn_calls, value, message
):
    monkeypatch.setenv("KB_DATA_API_KEY_TERMINAL", make_key())
    monkeypatch.setenv("KB_DATA_API_ALLOWED_HOSTS", value)

    exit_code = cli.main(["serve-data"])

    assert exit_code == 2
    assert message in capsys.readouterr().err
    assert uvicorn_calls == []


def test_serve_allows_the_hosts_of_the_environment_and_no_other(
    monkeypatch, capsys, data_dir, uvicorn_calls
):
    monkeypatch.setenv("KB_DATA_API_KEY_TERMINAL", make_key())
    monkeypatch.setenv("KB_DATA_API_ALLOWED_HOSTS", " api-donnees ,localhost")

    cli.main(["serve-data"])

    [(app, _)] = uvicorn_calls
    client = TestClient(app, base_url="http://api-donnees")
    assert client.get("/health").status_code == 200
    assert client.get("/health", headers={"Host": "127.0.0.1"}).status_code == 400
    assert "API allowed hosts: api-donnees, localhost" in capsys.readouterr().err


def test_serve_reads_the_base_and_writes_its_own_log_file_under_kb_data_dir(
    monkeypatch, data_dir, uvicorn_calls
):
    """kb serve-data runs for long: a rotation done by kb collect in kb.log would leave it writing in
    the file of the day before."""
    monkeypatch.setenv("KB_DATA_API_KEY_TERMINAL", make_key())

    cli.main(["serve-data"])

    [(app, _)] = uvicorn_calls
    assert app.state.database_path == data_dir / "k-b.sqlite"
    assert "API clients loaded: TERMINAL" in (
        data_dir / "logs" / "kb-serve-data.log"
    ).read_text(encoding="utf-8")
    assert not (data_dir / "logs" / "kb.log").exists()


def test_without_log_file_kb_logs_on_stderr_only(
    monkeypatch, capsys, data_dir, uvicorn_calls
):
    """In a container, stderr goes to the logs of the container: a file would be a copy."""
    monkeypatch.setenv("KB_DATA_API_KEY_TERMINAL", make_key())
    monkeypatch.setenv("KB_LOG_TO_FILE", "false")

    exit_code = cli.main(["serve-data"])

    assert exit_code == 0
    assert "API clients loaded: TERMINAL" in capsys.readouterr().err
    assert not (data_dir / "logs").exists()


@pytest.mark.parametrize("value", ["", "yes", "False", "0"])
def test_a_log_to_file_value_refused_exits_2(
    monkeypatch, capsys, data_dir, uvicorn_calls, value
):
    monkeypatch.setenv("KB_DATA_API_KEY_TERMINAL", make_key())
    monkeypatch.setenv("KB_LOG_TO_FILE", value)

    exit_code = cli.main(["serve-data"])

    assert exit_code == 2
    assert (
        f"error: KB_LOG_TO_FILE: '{value}' neither true nor false"
        in capsys.readouterr().err
    )
    assert uvicorn_calls == []
    assert not (data_dir / "logs").exists()


def test_openapi_writes_the_document_without_data_dir(monkeypatch, tmp_path):
    monkeypatch.delenv("KB_DATA_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "docs" / "api").mkdir(parents=True)

    exit_code = cli.main(["openapi"])

    written = tmp_path / "docs" / "api" / "openapi-donnees.json"
    assert exit_code == 0
    assert written.read_text(encoding="utf-8") == VERSIONED_OPENAPI.read_text(
        encoding="utf-8"
    )
    assert not (tmp_path / "logs").exists()


def test_openapi_writes_nothing_outside_the_root_of_the_repository(
    monkeypatch, tmp_path, capsys
):
    """Run from another folder, it would write a document the repository never sees."""
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["openapi"])

    assert exit_code == 2
    assert (
        "docs/api is not a directory of the current directory"
        in capsys.readouterr().err
    )
    assert list(tmp_path.iterdir()) == []


def test_serve_exits_with_the_code_of_uvicorn_when_it_does_not_start_and_sets_no_sigterm_handler(
    monkeypatch, data_dir
):
    monkeypatch.setenv("KB_DATA_API_KEY_TERMINAL", make_key())
    sigterm_handlers = []

    def fail_to_start(app, **options):
        # uvicorn sets its own SIGTERM handler: kb serve-data leaves it the one it found.
        sigterm_handlers.append(signal.getsignal(signal.SIGTERM))
        sys.exit(uvicorn.config.STARTUP_FAILURE)

    monkeypatch.setattr(serve_data_command.uvicorn, "run", fail_to_start)
    handler_before = signal.getsignal(signal.SIGTERM)

    with pytest.raises(SystemExit) as stop:
        cli.main(["serve-data"])

    assert stop.value.code == cli.ExitCode.SERVER_NOT_STARTED
    assert sigterm_handlers == [handler_before]
