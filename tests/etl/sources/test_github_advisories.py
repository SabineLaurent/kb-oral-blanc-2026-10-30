import json
import logging
from dataclasses import fields
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from k_b.etl.source_contract import PageRefusedError, list_sources
from k_b.etl.source_kinds import SOURCE_KINDS
from k_b.etl.sources.github import advisories as github_advisories
from k_b.etl.sources.github.advisories import (
    Advisory,
    GithubAdvisoriesSource,
    convert_advisory,
    read_advisories,
)

FIXTURES_DIR = Path(__file__).parent.parent.parent / "fixtures"
RECORDED_RESPONSE = (FIXTURES_DIR / "github_advisories_extract.json").read_bytes()
# The recorded advisories were published and reviewed on 2026-05-13, 06-16 and 06-25, and last
# updated on 06-08, 07-08 and 06-25.
BEFORE_ALL_ADVISORIES = datetime(2026, 5, 1, 0, 0, tzinfo=UTC)
BETWEEN_ADVISORIES = datetime(2026, 6, 20, 0, 0, tzinfo=UTC)
AFTER_ALL_ADVISORIES = datetime(2026, 6, 26, 0, 0, tzinfo=UTC)
PACKAGES = ("langchain", "langchain-core")


@pytest.fixture
def fake_github_api(monkeypatch):
    class FakeGithubApi:
        response = RECORDED_RESPONSE
        paths = []

        def __call__(self, path):
            self.paths.append(path)
            return self.response

    fake = FakeGithubApi()
    monkeypatch.setattr(github_advisories, "get_from_github_api", fake)
    return fake


def query_of(path):
    split_path = urlsplit(path)
    assert split_path.path == "advisories"
    return {name: values[0] for name, values in parse_qs(split_path.query).items()}


def test_each_ecosystem_of_the_tracked_repositories_is_a_source_of_kb_collect():
    source_names = {source.name for source in list_sources(SOURCE_KINDS)}

    assert {"github_advisories_pip", "github_advisories_npm"} <= source_names


def test_an_unknown_ecosystem_is_refused():
    with pytest.raises(ValueError, match="ecosystem"):
        GithubAdvisoriesSource("../escape", PACKAGES)


def test_a_first_collection_asks_for_the_reviewed_advisories_of_the_packages(
    fake_github_api,
):
    raw_response = GithubAdvisoriesSource("pip", PACKAGES).fetch(last_collected_at=None)

    assert raw_response == RECORDED_RESPONSE
    assert query_of(fake_github_api.paths[0]) == {
        "type": "reviewed",
        "ecosystem": "pip",
        "affects": "langchain,langchain-core",
        "sort": "updated",
        "per_page": "100",
    }


def test_a_later_collection_asks_for_advisories_updated_from_the_day_before_the_last_one(
    fake_github_api,
):
    GithubAdvisoriesSource("pip", PACKAGES).fetch(last_collected_at=BETWEEN_ADVISORIES)

    query = query_of(fake_github_api.paths[0])
    assert query["updated"] == ">=2026-06-19"
    assert query["sort"] == "updated"
    # The API ignores a filter it does not know, as reviewed: the whole page would come back.
    assert "reviewed" not in query
    assert "published" not in query


def test_an_advisory_published_before_the_last_collection_but_reviewed_after_is_new(
    fake_github_api,
):
    entries = json.loads(RECORDED_RESPONSE)
    entries[0]["published_at"] = "2026-06-10T00:00:00Z"
    fake_github_api.response = json.dumps(entries).encode()

    raw_response = GithubAdvisoriesSource("pip", PACKAGES).fetch(BETWEEN_ADVISORIES)

    assert raw_response == fake_github_api.response


def test_a_page_with_an_advisory_newer_than_the_last_collection_is_returned(
    fake_github_api,
):
    source = GithubAdvisoriesSource("pip", PACKAGES)

    assert source.fetch(last_collected_at=BETWEEN_ADVISORIES) == RECORDED_RESPONSE


def test_a_page_without_any_new_advisory_returns_nothing(fake_github_api):
    source = GithubAdvisoriesSource("pip", PACKAGES)

    assert source.fetch(last_collected_at=AFTER_ALL_ADVISORIES) is None


def test_an_empty_first_collection_returns_nothing(fake_github_api):
    fake_github_api.response = b"[]"

    assert GithubAdvisoriesSource("pip", PACKAGES).fetch(last_collected_at=None) is None


def test_a_full_page_of_new_advisories_logs_the_count_read_and_the_oldest_date(
    fake_github_api, monkeypatch, caplog
):
    monkeypatch.setattr(github_advisories, "ADVISORIES_PER_PAGE", 3)

    with caplog.at_level(logging.WARNING):
        GithubAdvisoriesSource("pip", PACKAGES).fetch(
            last_collected_at=BEFORE_ALL_ADVISORIES
        )

    [record] = caplog.records
    assert record.getMessage() == (
        "github_advisories_pip: 3 advisories read, page size 3; oldest updated at "
        "2026-06-08T23:54:04+00:00, after the last collection at "
        "2026-05-01T00:00:00+00:00; next pages not read"
    )


def test_no_warning_when_the_page_reaches_back_to_the_last_collection(
    fake_github_api, monkeypatch, caplog
):
    monkeypatch.setattr(github_advisories, "ADVISORIES_PER_PAGE", 3)

    with caplog.at_level(logging.WARNING):
        GithubAdvisoriesSource("pip", PACKAGES).fetch(
            last_collected_at=BETWEEN_ADVISORIES
        )

    assert caplog.text == ""


COLLECTED_AT = datetime(2026, 10, 5, 9, 30, tzinfo=UTC)
RAW_FILE_PATH = "automated_watch/github_advisories_pip/2026-10-05T093000Z.json"
PUBLISHED_AT = datetime(2026, 10, 1, 18, 0, 22, tzinfo=UTC)


def advisory_payload(**changes):
    payload = json.loads(RECORDED_RESPONSE)[0]
    payload.update(changes)
    return json.dumps([payload]).encode()


def test_a_recorded_response_gives_one_advisory_per_entry_newest_first():
    advisories = read_advisories(RECORDED_RESPONSE).entries

    assert [advisory.ghsa_id for advisory in advisories] == [
        "GHSA-fjqc-hq36-qh5p",
        "GHSA-gr75-jv2w-4656",
        "GHSA-3644-q5cj-c5c7",
    ]
    newest = advisories[0]
    assert newest.cve_id == "CVE-2026-48775"
    assert newest.severity == "medium"
    assert newest.url == "https://github.com/advisories/GHSA-fjqc-hq36-qh5p"
    assert newest.published_at == datetime(2026, 6, 25, 18, 25, 42, tzinfo=UTC)
    assert newest.reviewed_at == datetime(2026, 6, 25, 18, 25, 42, tzinfo=UTC)
    assert newest.updated_at == datetime(2026, 6, 25, 18, 25, 43, tzinfo=UTC)
    assert newest.summary


def test_each_affected_package_is_kept_once_in_its_order():
    assert read_advisories(RECORDED_RESPONSE).entries[2].packages == (
        "langsmith",
        "langchain-classic",
        "langchain",
    )


def test_the_credited_people_are_not_kept():
    assert not any("credit" in field.name for field in fields(Advisory))


def test_an_advisory_without_cve_keeps_an_empty_cve():
    assert read_advisories(advisory_payload(cve_id=None)).entries[0].cve_id is None


def test_a_response_that_is_not_a_list_is_refused():
    with pytest.raises(PageRefusedError, match="list"):
        read_advisories(b'{"message": "Validation Failed"}')


@pytest.mark.parametrize(
    "changes",
    [
        {"ghsa_id": "GHSA-../../etc"},
        {"cve_id": "CVE-26-1"},
        {"cve_id": "CVE-\uff12\uff10\uff12\uff16-1234"},
        {"severity": "urgent"},
        {"summary": None},
        {"html_url": "https://evil.example.com/advisories/GHSA-fjqc-hq36-qh5p"},
        {"published_at": "2026-06-25T18:25:42"},
        {"github_reviewed_at": None},
        {"updated_at": None},
        {"vulnerabilities": [{"package": {"name": 42}}]},
    ],
)
def test_an_invalid_advisory_is_rejected_with_its_position(changes):
    reading = read_advisories(advisory_payload(**changes))

    assert reading.entries == []
    [reason] = reading.rejection_reasons
    assert reason.startswith("advisory 0")


def test_a_recorded_advisory_gives_its_item_with_its_cve_and_packages():
    advisory = read_advisories(RECORDED_RESPONSE).entries[2]

    item = convert_advisory(advisory, COLLECTED_AT, RAW_FILE_PATH)

    assert item.source_code == "github"
    assert item.kind == "advisory"
    assert item.source_item_id == "GHSA-3644-q5cj-c5c7"
    assert item.title == advisory.summary
    assert item.url == advisory.url
    assert item.published_at == advisory.published_at
    assert item.cve == "CVE-2026-45134"
    assert item.package_names == ("langsmith", "langchain-classic", "langchain")


def page_with_invalid_advisories(*positions):
    """The recorded page, its advisories at these positions with an unknown severity."""
    entries = json.loads(RECORDED_RESPONSE)
    for position in positions:
        entries[position]["severity"] = "urgent"
    return json.dumps(entries).encode()


def test_an_invalid_advisory_is_rejected_with_its_ghsa_and_field_and_the_others_are_read():
    reading = read_advisories(page_with_invalid_advisories(1))

    assert [advisory.ghsa_id for advisory in reading.entries] == [
        "GHSA-fjqc-hq36-qh5p",
        "GHSA-3644-q5cj-c5c7",
    ]
    assert reading.rejection_reasons == [
        "advisory 1 ('GHSA-gr75-jv2w-4656'): unknown severity: 'urgent'"
    ]


def test_a_page_with_an_invalid_advisory_is_collected(fake_github_api):
    fake_github_api.response = page_with_invalid_advisories(1)

    assert (
        GithubAdvisoriesSource("pip", PACKAGES).fetch(BETWEEN_ADVISORIES)
        == fake_github_api.response
    )


def test_a_page_with_an_invalid_advisory_is_collected_even_without_a_new_valid_one(
    fake_github_api,
):
    # The rejected advisory leaves the page as the window moves on: kept now, it reaches the
    # import log.
    fake_github_api.response = page_with_invalid_advisories(1)

    assert (
        GithubAdvisoriesSource("pip", PACKAGES).fetch(AFTER_ALL_ADVISORIES)
        == fake_github_api.response
    )


def test_a_page_whose_advisories_are_all_invalid_fails_the_collection(fake_github_api):
    fake_github_api.response = page_with_invalid_advisories(0, 1, 2)

    with pytest.raises(
        PageRefusedError, match="^all_items_invalid: 3 entries, all rejected"
    ):
        GithubAdvisoriesSource("pip", PACKAGES).fetch(last_collected_at=None)


def test_a_full_page_counts_its_rejected_advisories(
    fake_github_api, monkeypatch, caplog
):
    fake_github_api.response = page_with_invalid_advisories(0)
    monkeypatch.setattr(github_advisories, "ADVISORIES_PER_PAGE", 3)

    with caplog.at_level(logging.WARNING):
        GithubAdvisoriesSource("pip", PACKAGES).fetch(
            last_collected_at=BEFORE_ALL_ADVISORIES
        )

    [record] = caplog.records
    assert record.getMessage().startswith(
        "github_advisories_pip: 3 advisories read, page size 3"
    )
