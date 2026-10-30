import json
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from k_b.etl.source_contract import PageRefusedError, list_sources
from k_b.etl.source_kinds import SOURCE_KINDS
from k_b.etl.sources.github import most_starred as github_most_starred
from k_b.etl.sources.github.most_starred import (
    GithubMostStarredSource,
    read_most_starred,
)
from k_b.etl.tops import TopEntry

FIXTURES_DIR = Path(__file__).parent.parent.parent / "fixtures"
RECORDED_RESPONSE = (FIXTURES_DIR / "github_most_starred_extract.json").read_bytes()


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
    monkeypatch.setattr(github_most_starred, "get_from_github_api", fake)
    return fake


def test_the_most_starred_repositories_are_a_source_of_kb_collect():
    assert "github_most_starred" in {
        source.name for source in list_sources(SOURCE_KINDS)
    }


def test_the_request_asks_for_the_100_most_starred_repositories(fake_github_api):
    GithubMostStarredSource().fetch(last_collected_at=None)

    split_path = urlsplit(fake_github_api.paths[0])
    assert split_path.path == "search/repositories"
    assert {name: values[0] for name, values in parse_qs(split_path.query).items()} == {
        "q": "stars:>10000",
        "sort": "stars",
        "order": "desc",
        "per_page": "100",
    }


def test_every_collection_writes_a_snapshot_of_the_ranking(fake_github_api):
    source = GithubMostStarredSource()

    assert source.fetch(last_collected_at=None) == FULL_RANKING
    assert source.fetch(last_collected_at=None) == FULL_RANKING


def test_a_ranking_shorter_than_100_is_not_written(fake_github_api):
    fake_github_api.response = ranking_of(99)

    with pytest.raises(PageRefusedError, match="99 repositories, expected 100"):
        GithubMostStarredSource().fetch(last_collected_at=None)


def test_an_incomplete_ranking_is_not_written(fake_github_api):
    response = json.loads(FULL_RANKING)
    response["incomplete_results"] = True
    fake_github_api.response = json.dumps(response).encode()

    with pytest.raises(PageRefusedError, match="incomplete"):
        GithubMostStarredSource().fetch(last_collected_at=None)


COLLECTED_AT = datetime(2026, 10, 5, 9, 30, tzinfo=UTC)
RAW_FILE_PATH = "automated_watch/github_most_starred/2026-10-05T093000Z.json"
PUBLISHED_AT = datetime(2026, 10, 1, 18, 0, 22, tzinfo=UTC)


def response_with(**changes):
    response = json.loads(RECORDED_RESPONSE)
    response["items"][0].update(changes)
    return json.dumps(response).encode()


def test_a_recorded_response_gives_the_repositories_by_rank():
    repositories = read_most_starred(RECORDED_RESPONSE, expected_size=3)

    assert [(repository.rank, repository.full_name) for repository in repositories] == [
        (1, "codecrafters-io/build-your-own-x"),
        (2, "public-apis/public-apis"),
        (3, "freeCodeCamp/freeCodeCamp"),
    ]
    first = repositories[0]
    assert first.url == "https://github.com/codecrafters-io/build-your-own-x"
    assert first.stars == 551156
    assert first.created_at == datetime(2018, 5, 9, 12, 3, 18, tzinfo=UTC)
    assert "awesome-list" in first.topics
    assert first.description


def test_a_repository_without_description_keeps_an_empty_description():
    repositories = read_most_starred(response_with(description=None), expected_size=3)

    assert repositories[0].description is None


def test_an_incomplete_ranking_is_refused():
    response = json.loads(RECORDED_RESPONSE)
    response["incomplete_results"] = True

    with pytest.raises(PageRefusedError, match="incomplete"):
        read_most_starred(json.dumps(response).encode(), expected_size=3)


def test_a_ranking_shorter_than_expected_is_refused():
    with pytest.raises(PageRefusedError, match="3 repositories, expected 4"):
        read_most_starred(RECORDED_RESPONSE, expected_size=4)


def test_a_response_without_a_list_of_items_is_refused():
    with pytest.raises(PageRefusedError, match="items"):
        read_most_starred(b'{"message": "Validation Failed"}', expected_size=3)


@pytest.mark.parametrize(
    "changes",
    [
        {"full_name": "../escape"},
        {"full_name": "owner/."},
        {"html_url": "https://evil.example.com/codecrafters-io/build-your-own-x"},
        {"stargazers_count": "many"},
        {"stargazers_count": True},
        {"topics": "awesome-list"},
        {"created_at": "2018-05-09T12:03:18"},
    ],
)
def test_an_invalid_repository_is_refused_with_its_rank(changes):
    with pytest.raises(PageRefusedError, match="repository 1"):
        read_most_starred(response_with(**changes), expected_size=3)


def test_a_refused_name_is_quoted_by_its_length_only():
    # The name carries the owner's login (register of processing activities).
    with pytest.raises(PageRefusedError) as refusal:
        read_most_starred(response_with(full_name="jdoe/../escape"), expected_size=3)

    assert str(refusal.value) == "repository 1: not an owner/name, length 14"


def test_a_name_that_is_not_a_string_is_refused_by_its_type():
    with pytest.raises(PageRefusedError) as refusal:
        read_most_starred(response_with(full_name=["jdoe/escape"]), expected_size=3)

    assert str(refusal.value) == "repository 1: full_name is not a string"


def test_its_entries_of_a_sheet_carry_the_stars_and_a_url_built_from_the_name():
    changed = json.loads(RECORDED_RESPONSE)
    changed["items"][0]["html_url"] = "https://github.com/someone/else"

    entries = github_most_starred.read_top_entries(
        json.dumps(changed).encode(), expected_size=3
    )

    assert entries[0] == TopEntry(
        rank=1,
        full_name="codecrafters-io/build-your-own-x",
        url="https://github.com/codecrafters-io/build-your-own-x",
        description=(
            "Master programming by recreating your favorite technologies from scratch."
        ),
        count=551156,
    )
    assert [entry.count for entry in entries] == [551156, 485463, 456631]


def test_its_entries_of_a_sheet_are_the_100_of_the_ranking_by_default():
    assert len(github_most_starred.read_top_entries(FULL_RANKING)) == 100
