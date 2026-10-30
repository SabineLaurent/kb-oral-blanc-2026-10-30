"""What the tests of the kb commands share: the settings of the terminal removed, the vault and the tops of the tests."""

import signal

import pytest
from commands.cli_helpers import (
    SigtermNotHandledByKbError,
)


@pytest.fixture(autouse=True)
def no_zotero_directory(monkeypatch):
    """A terminal that exports KB_ZOTERO_DIR must not make the tests read a real library."""
    monkeypatch.delenv("KB_ZOTERO_DIR", raising=False)


@pytest.fixture(autouse=True)
def no_vault_directory(monkeypatch):
    """A terminal that exports KB_VAULT_DIR must not make the tests read a real vault."""
    monkeypatch.delenv("KB_VAULT_DIR", raising=False)


@pytest.fixture(autouse=True)
def no_bigquery_key(monkeypatch):
    """A terminal that exports the BigQuery key must not make the tests query BigQuery."""
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)


@pytest.fixture(autouse=True)
def no_collect_only(monkeypatch):
    """A terminal that exports KB_COLLECT_ONLY must not filter the sources of the tests."""
    monkeypatch.delenv("KB_COLLECT_ONLY", raising=False)


@pytest.fixture(autouse=True)
def no_ai_api_client_settings(monkeypatch):
    """A terminal that exports the key or the URL of kb digest must not make the tests reach a
    real AI API."""
    monkeypatch.delenv("KB_AI_API_KEY_KB_DIGEST", raising=False)
    monkeypatch.delenv("KB_AI_API_URL", raising=False)


@pytest.fixture(autouse=True)
def no_github_token(monkeypatch):
    """A terminal that exports KB_GITHUB_TOKEN must not hide the line said without it."""
    monkeypatch.delenv("KB_GITHUB_TOKEN", raising=False)


@pytest.fixture
def sigterm_handler_of_the_test():
    """A SIGTERM handler set before kb runs, which kb must set back; without it, a kb that sets
    none would leave the default one, which kills pytest."""

    def raise_in_the_test(signal_number, frame):
        raise SigtermNotHandledByKbError

    previous_handler = signal.signal(signal.SIGTERM, raise_in_the_test)
    yield raise_in_the_test
    signal.signal(signal.SIGTERM, previous_handler)


@pytest.fixture
def data_and_vault_dirs(monkeypatch, tmp_path):
    data_dir, vault_dir = tmp_path / "data", tmp_path / "vault"
    data_dir.mkdir()
    vault_dir.mkdir()
    monkeypatch.setenv("KB_DATA_DIR", str(data_dir))
    monkeypatch.setenv("KB_VAULT_DIR", str(vault_dir))
    return data_dir, vault_dir
