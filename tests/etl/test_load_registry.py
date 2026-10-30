"""The register of the raw files read, and the lines of the import log."""

import re

import pytest
from load_helpers import (
    ARXIV_DIR,
    CISA_KEV_DIR,
    CISA_KEV_PATH,
    CVE_ID_IN_ARABIC_INDIC_DIGITS,
    CVE_IDS,
    FIRST_CVE_ID,
    FIRST_IMPORT_AT,
    RELEASES_DIR,
    RELEASES_PATH,
    count_rows,
    dump_database,
    kev_catalog,
    list_logs,
    log_path_at,
    read_log,
    read_log_lines,
    refuse_in_the_base,
    registered_paths,
    run_import,
    write_collection,
)
from test_transform import FIXTURES_DIR, copy_fixture

from k_b.core.config import GITHUB_TRACKED_REPOSITORIES
from k_b.etl.load import (
    ImportLogExistsError,
    import_items,
    read_imported_paths,
)
from k_b.etl.raw_files import TrendReading
from k_b.etl.source_kinds import SOURCE_KINDS
from k_b.etl.transform import read_raw_files


def test_a_file_read_without_item_is_registered_and_not_read_again(data_dir):
    empty = write_collection(
        data_dir, CISA_KEV_DIR, "2026-10-06T093000Z", kev_catalog(without=CVE_IDS)
    )

    first = run_import(data_dir)
    second = run_import(data_dir)

    assert empty in registered_paths(data_dir)
    assert (first.read_count, second.read_count) == (5, 0)
    # A catalog without any row is not a catalog that withdrew every CVE.
    assert first.withdrawn_count == 0


def test_a_file_whose_releases_are_all_out_of_the_list_is_registered_and_not_read_again(
    data_dir,
):
    """The reading rejects every release of the file and still returns it, so that the import
    registers it."""
    still_tracked = tuple(
        repository
        for repository in GITHUB_TRACKED_REPOSITORIES
        if repository.name != "anthropics/claude-code"
    )

    first = run_import(data_dir, still_tracked)
    second = run_import(data_dir, still_tracked)

    assert RELEASES_PATH in registered_paths(data_dir)
    assert (first.rejection_count, second.read_count, second.rejection_count) == (
        3,
        0,
        0,
    )
    assert count_rows(data_dir, "SELECT count(*) FROM release") == 0


def test_a_file_read_with_a_rejected_entry_is_registered_and_not_read_again(data_dir):
    catalog = kev_catalog()
    assert (
        catalog.count("Citrix NetScaler Improper Input Validation Vulnerability") == 1
    )
    with_rejection = write_collection(
        data_dir,
        CISA_KEV_DIR,
        "2026-10-06T093000Z",
        catalog.replace("Citrix NetScaler Improper Input Validation Vulnerability", ""),
    )

    first = run_import(data_dir)
    second = run_import(data_dir)

    assert with_rejection in registered_paths(data_dir)
    assert first.rejection_count == 1
    assert (second.read_count, second.rejection_count) == (0, 0)


def test_an_unreadable_file_and_a_file_without_reader_are_not_registered(data_dir):
    unreadable = write_collection(data_dir, ARXIV_DIR, "2026-10-06T093000Z", "not xml")
    copy_fixture(
        data_dir / "raw",
        "github_most_starred_extract.json",
        "github_most_starred",
        "json",
    )

    run_import(data_dir)
    second = run_import(data_dir)

    assert unreadable not in registered_paths(data_dir)
    assert all("github_most_starred" not in path for path in registered_paths(data_dir))
    # The unreadable file is read and rejected at each import (decision of 2026-10-12).
    assert (second.read_count, second.rejection_count) == (1, 1)


def test_only_a_new_raw_file_is_read_and_the_log_of_the_first_import_stays_whole(
    data_dir,
):
    run_import(data_dir)
    (first_log,) = list_logs(data_dir)
    first_log_content = first_log.read_bytes()
    write_collection(data_dir, CISA_KEV_DIR, "2026-10-06T093000Z", kev_catalog("Known"))

    second = run_import(data_dir)

    assert second.read_count == 1
    assert second.item_count == 1
    assert len(list_logs(data_dir)) == 2
    assert first_log.read_bytes() == first_log_content


def test_a_log_of_the_same_second_stops_the_import_before_the_database(data_dir):
    log_path = log_path_at(data_dir, FIRST_IMPORT_AT)
    log_path.write_text("earlier import\n")
    tables_before = dump_database(data_dir)
    database_path = data_dir / "k-b.sqlite"
    reading = read_raw_files(
        data_dir / "raw",
        SOURCE_KINDS,
        read_imported_paths(database_path),
        GITHUB_TRACKED_REPOSITORIES,
    ).items

    with pytest.raises(ImportLogExistsError):
        import_items(
            reading,
            TrendReading([], [], 0),
            database_path,
            log_path,
            FIRST_IMPORT_AT,
            GITHUB_TRACKED_REPOSITORIES,
        )

    assert dump_database(data_dir) == tables_before
    assert log_path.read_text() == "earlier import\n"


def test_each_line_of_the_log_carries_the_time_of_its_import(data_dir):
    write_collection(data_dir, ARXIV_DIR, "2026-10-06T093000Z", "not xml")
    releases = (FIXTURES_DIR / "github_releases_extract.json").read_text()
    write_collection(data_dir, RELEASES_DIR, "2026-10-06T093000Z", releases)

    run_import(data_dir)

    lines = read_log_lines(data_dir)
    assert {line["kind"] for line in lines} == {"rejection", "discard"}
    assert {line["imported_at"] for line in lines} == {"2026-10-12T15:49:03Z"}
    assert all(
        re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", line["imported_at"])
        for line in lines
    )


def test_the_log_lists_what_the_reading_rejected_before_what_the_writing_refused(
    data_dir, monkeypatch
):
    """What the reading rejected comes first: an unreadable page and a release out of the list
    before an item the base refuses."""
    unreadable = write_collection(data_dir, ARXIV_DIR, "2026-10-06T093000Z", "not xml")
    still_tracked = tuple(
        repository
        for repository in GITHUB_TRACKED_REPOSITORIES
        if repository.name != "anthropics/claude-code"
    )
    refuse_in_the_base(
        monkeypatch, FIRST_CVE_ID, {"cve": CVE_ID_IN_ARABIC_INDIC_DIGITS}
    )

    run_import(data_dir, still_tracked)

    rejections = [line for line in read_log(data_dir) if line["kind"] == "rejection"]
    assert [line["raw_file_path"] for line in rejections] == [
        unreadable,
        RELEASES_PATH,
        RELEASES_PATH,
        RELEASES_PATH,
        CISA_KEV_PATH,
    ]
    assert "not an XML document" in rejections[0]["reason"]
    assert {line["reason"] for line in rejections[1:4]} == {
        "repository not in the tracked list: anthropics/claude-code"
    }
    assert "refused by the base" in rejections[4]["reason"]
