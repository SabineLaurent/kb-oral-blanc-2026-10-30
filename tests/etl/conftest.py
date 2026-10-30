"""The data directory of the tests of the load: the recorded raw files of four sources and an
empty base; a test file that needs another one defines its own."""

import pytest
from test_transform import copy_fixture

from k_b.database.creation import create_database
from k_b.etl import http_download
from k_b.etl.sources import tldr
from k_b.etl.sources.github import api as github_api


@pytest.fixture(autouse=True)
def no_wait_before_a_retry(monkeypatch):
    """A download retried after a 429, a 5xx or a failed connection waits for nothing in a test;
    the waits asked are recorded."""
    waits = []
    monkeypatch.setattr(http_download, "sleep", waits.append)
    return waits


@pytest.fixture(autouse=True)
def no_github_pause(monkeypatch):
    """A pause GitHub asked in one test never reaches the next."""
    monkeypatch.setattr(github_api, "paused_until", 0.0)


@pytest.fixture(autouse=True)
def no_pause_before_a_tldr_page(monkeypatch):
    """A tldr.tech page waits for nothing in a test."""
    monkeypatch.setattr(tldr, "sleep", lambda seconds: None)


@pytest.fixture
def data_dir(tmp_path):
    raw_dir = tmp_path / "raw"
    copy_fixture(raw_dir, "arxiv_extract.xml", "arxiv_cscr_x_csai", "xml")
    copy_fixture(raw_dir, "cisa_kev_extract.csv", "cisa_kev", "csv")
    copy_fixture(
        raw_dir,
        "github_releases_extract.json",
        "github_releases_anthropics_claude_code",
        "json",
    )
    copy_fixture(
        raw_dir, "github_advisories_extract.json", "github_advisories_pip", "json"
    )
    create_database(tmp_path / "k-b.sqlite")
    return tmp_path
