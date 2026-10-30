import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from k_b.etl.raw_store import AUTOMATED_WATCH_DIR_NAME
from k_b.etl.source_contract import PageRefusedError, list_sources
from k_b.etl.source_kinds import SOURCE_KINDS
from k_b.etl.sources.github import most_forked as github_most_forked
from k_b.etl.sources.github.most_forked import (
    GithubMostForkedSource,
    read_most_forked,
)
from k_b.etl.tops import TopEntry

FIXTURES_DIR = Path(__file__).parent.parent.parent / "fixtures"
# Ranks 4 to 6 of the collection of 2026-10-19: the first three are owned by persons (minimisation).
RECORDED_RESPONSE = (FIXTURES_DIR / "github_most_forked_extract.json").read_bytes()


def ranking_of(size):
    """A search response of size repositories, built by repeating the recorded ones."""
    response = json.loads(RECORDED_RESPONSE)
    recorded_items = response["items"]
    response["items"] = [
        recorded_items[rank % len(recorded_items)] for rank in range(size)
    ]
    return json.dumps(response).encode()


FULL_RANKING = ranking_of(100)


@pytest.fixture
def fake_github_api(monkeypatch):
    class FakeGithubApi:
        response = FULL_RANKING
        paths = []

        def __call__(self, path):
            self.paths.append(path)
            return self.response

    fake = FakeGithubApi()
    monkeypatch.setattr(github_most_forked, "get_from_github_api", fake)
    return fake


def test_the_most_forked_repositories_are_a_source_of_kb_collect():
    assert "github_most_forked" in {
        source.name for source in list_sources(SOURCE_KINDS)
    }


def test_its_snapshots_are_written_under_automated_watch():
    source = GithubMostForkedSource()

    assert (source.acquisition_dir_name, source.name, source.extension) == (
        AUTOMATED_WATCH_DIR_NAME,
        "github_most_forked",
        "json",
    )


def test_the_request_asks_for_the_100_most_forked_repositories(fake_github_api):
    GithubMostForkedSource().fetch(last_collected_at=None)

    split_path = urlsplit(fake_github_api.paths[0])
    assert split_path.path == "search/repositories"
    assert {name: values[0] for name, values in parse_qs(split_path.query).items()} == {
        "q": "forks:>10000",
        "sort": "forks",
        "order": "desc",
        "per_page": "100",
    }


def test_every_collection_writes_a_snapshot_of_the_ranking(fake_github_api):
    source = GithubMostForkedSource()

    assert source.fetch(last_collected_at=None) == FULL_RANKING
    assert source.fetch(last_collected_at=None) == FULL_RANKING


def test_a_ranking_shorter_than_100_is_not_written(fake_github_api):
    fake_github_api.response = ranking_of(99)

    with pytest.raises(PageRefusedError, match="99 repositories, expected 100"):
        GithubMostForkedSource().fetch(last_collected_at=None)


def test_an_incomplete_ranking_is_not_written(fake_github_api):
    response = json.loads(FULL_RANKING)
    response["incomplete_results"] = True
    fake_github_api.response = json.dumps(response).encode()

    with pytest.raises(PageRefusedError, match="incomplete"):
        GithubMostForkedSource().fetch(last_collected_at=None)


def response_with(**changes):
    response = json.loads(RECORDED_RESPONSE)
    response["items"][0].update(changes)
    return json.dumps(response).encode()


def test_a_recorded_response_gives_the_repositories_by_rank():
    repositories = read_most_forked(RECORDED_RESPONSE, expected_size=3)

    assert [
        (repository.rank, repository.full_name, repository.forks)
        for repository in repositories
    ] == [
        (1, "firstcontributions/first-contributions", 111263),
        (2, "ultraworkers/claw-code", 107886),
        (3, "TheOdinProject/css-exercises", 94375),
    ]
    assert repositories[0].description == (
        "🚀✨ Help beginners to contribute to open source projects"
    )


def test_a_repository_without_description_has_none():
    repositories = read_most_forked(RECORDED_RESPONSE, expected_size=3)

    assert repositories[2].description is None


def test_an_incomplete_ranking_is_refused():
    response = json.loads(RECORDED_RESPONSE)
    response["incomplete_results"] = True

    with pytest.raises(PageRefusedError, match="incomplete"):
        read_most_forked(json.dumps(response).encode(), expected_size=3)


def test_a_ranking_shorter_than_expected_is_refused():
    with pytest.raises(PageRefusedError, match="3 repositories, expected 4"):
        read_most_forked(RECORDED_RESPONSE, expected_size=4)


def test_a_response_that_is_not_json_is_refused():
    with pytest.raises(ValueError):
        read_most_forked(b"not json", expected_size=3)


def test_a_response_without_a_list_of_items_is_refused():
    with pytest.raises(PageRefusedError, match="items"):
        read_most_forked(b'{"message": "Validation Failed"}', expected_size=3)


@pytest.mark.parametrize(
    "changes",
    [
        {"full_name": None},
        {"full_name": "../escape"},
        {"full_name": "owner/."},
        {"html_url": "https://evil.example.com/firstcontributions/first-contributions"},
        {"description": 42},
        {"forks_count": "many"},
        {"forks_count": True},
    ],
)
def test_an_invalid_repository_is_refused_with_its_rank(changes):
    with pytest.raises(PageRefusedError, match="repository 1"):
        read_most_forked(response_with(**changes), expected_size=3)


def test_a_repository_that_is_not_an_object_is_refused_with_its_rank():
    response = json.loads(RECORDED_RESPONSE)
    response["items"][1] = "firstcontributions/first-contributions"

    with pytest.raises(PageRefusedError, match="repository 2: not an object"):
        read_most_forked(json.dumps(response).encode(), expected_size=3)


def test_a_refused_name_is_quoted_by_its_length_only():
    # The name carries the owner's login (register of processing activities).
    with pytest.raises(PageRefusedError) as refusal:
        read_most_forked(response_with(full_name="jdoe/../escape"), expected_size=3)

    assert str(refusal.value) == "repository 1: not an owner/name, length 14"


def test_a_name_that_is_not_a_string_is_refused_by_its_type():
    with pytest.raises(PageRefusedError) as refusal:
        read_most_forked(response_with(full_name=["jdoe/escape"]), expected_size=3)

    assert str(refusal.value) == "repository 1: full_name is not a string"


def test_its_entries_of_a_sheet_carry_the_forks_and_a_url_built_from_the_name():
    changed = json.loads(RECORDED_RESPONSE)
    changed["items"][0]["html_url"] = "https://github.com/someone/else"

    entries = github_most_forked.read_top_entries(
        json.dumps(changed).encode(), expected_size=3
    )

    assert entries[0] == TopEntry(
        rank=1,
        full_name="firstcontributions/first-contributions",
        url="https://github.com/firstcontributions/first-contributions",
        description="🚀✨ Help beginners to contribute to open source projects",
        count=111263,
    )
    assert [entry.count for entry in entries] == [111263, 107886, 94375]


def test_its_entries_of_a_sheet_are_the_100_of_the_ranking_by_default():
    assert len(github_most_forked.read_top_entries(FULL_RANKING)) == 100
