import csv

import pytest

from k_b.etl.http_download import RedirectedOutsideError
from k_b.etl.source_contract import (
    EntryReading,
    ExpectedSourceError,
    PageRefusedError,
    SourceKind,
    check_not_all_invalid,
    check_not_empty,
    find_source_kind,
    list_sources,
    read_each_entry,
)
from k_b.etl.sources.clipping import ClippingsFolderMissingError, ClipTooLargeError
from k_b.etl.sources.gh_archive import (
    DayTableNotClosedError,
    DryRunOverLimitError,
    DryRunVolumeUnknownError,
    TrendsExtractInvalidError,
)
from k_b.etl.sources.github.api import GithubPausedError
from k_b.etl.sources.zotero import (
    ZoteroChangedDuringCopyError,
    ZoteroCopyInconsistentError,
    ZoteroQueryFailedError,
)

RELEASES = SourceKind("github_releases", (), None)
STARRED = SourceKind("github_most_starred", (), None)
KINDS = (RELEASES, STARRED)


def test_a_folder_named_after_a_prefix_belongs_to_its_kind():
    assert find_source_kind("github_most_starred", KINDS) is STARRED


def test_a_folder_starting_with_a_prefix_and_an_underscore_belongs_to_its_kind():
    assert find_source_kind("github_releases_someone_untracked", KINDS) is RELEASES


def test_a_folder_of_no_kind_has_none():
    assert find_source_kind("github_releasesx", KINDS) is None
    assert find_source_kind("unknown", KINDS) is None


def test_the_sources_are_listed_kind_after_kind():
    first, second, third = object(), object(), object()
    kinds = (
        SourceKind("a", (first, second), None),  # type: ignore[arg-type]
        SourceKind("b", (third,), None),  # type: ignore[arg-type]
    )

    assert list_sources(kinds) == [first, second, third]


def parse_even(raw_entry: int, number: int) -> int:
    if raw_entry % 2:
        raise ValueError(f"entry {number}: value is odd")
    return raw_entry


def test_an_entry_refused_by_its_parser_is_rejected_and_the_others_are_read():
    reading = read_each_entry(enumerate([2, 3, 4]), parse_even)

    assert reading == EntryReading([2, 4], ["entry 1: value is odd"])


def test_an_error_while_listing_the_entries_is_not_a_rejection():
    def entries_then_error():
        yield 0, 2
        raise csv.Error("line contains NUL")

    with pytest.raises(csv.Error):
        read_each_entry(entries_then_error(), parse_even)


def test_a_page_whose_entries_are_all_invalid_is_refused_as_all_items_invalid():
    with pytest.raises(
        PageRefusedError,
        match="^all_items_invalid: 2 entries, all rejected; first: entry 0: value is odd$",
    ):
        check_not_all_invalid(
            EntryReading([], ["entry 0: value is odd", "entry 1: value is odd"])
        )


@pytest.mark.parametrize(
    "reading",
    [EntryReading([], []), EntryReading([2], ["entry 1: value is odd"])],
)
def test_an_empty_page_or_a_page_with_a_valid_entry_is_accepted(reading):
    check_not_all_invalid(reading)


def test_a_state_catalog_without_any_entry_is_refused_as_empty_catalog():
    with pytest.raises(PageRefusedError, match="^empty_catalog: 0 entries$"):
        check_not_empty(EntryReading([], []))


@pytest.mark.parametrize(
    "reading",
    [EntryReading([2], []), EntryReading([], ["entry 1: value is odd"])],
)
def test_a_state_catalog_with_an_entry_valid_or_not_is_not_empty(reading):
    check_not_empty(reading)


def test_a_page_refused_is_an_expected_failure_and_still_a_value_error():
    # A ValueError still: the import rejects a page it cannot read on a ValueError.
    assert issubclass(PageRefusedError, ExpectedSourceError)
    assert issubclass(PageRefusedError, ValueError)


@pytest.mark.parametrize(
    ("error_class", "family"),
    [
        (ClipTooLargeError, ExpectedSourceError),
        (ClippingsFolderMissingError, ExpectedSourceError),
        (ZoteroCopyInconsistentError, ExpectedSourceError),
        (ZoteroChangedDuringCopyError, ExpectedSourceError),
        (ZoteroQueryFailedError, ExpectedSourceError),
        (DayTableNotClosedError, ExpectedSourceError),
        (DryRunOverLimitError, ExpectedSourceError),
        (DryRunVolumeUnknownError, ExpectedSourceError),
        (GithubPausedError, ExpectedSourceError),
        (TrendsExtractInvalidError, PageRefusedError),
        (RedirectedOutsideError, PageRefusedError),
    ],
)
def test_each_named_failure_of_a_source_is_an_expected_one(error_class, family):
    assert issubclass(error_class, family)
