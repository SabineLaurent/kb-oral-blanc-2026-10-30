"""An item read again: kept with its first collection, replaced when newer, logged as a discard."""

import json
import shutil

import pytest
from load_helpers import (
    ADVISORIES_DIR,
    ARXIV_DIR,
    AUTOMATED_WATCH,
    CISA_KEV_DIR,
    FIRST_COLLECTION,
    FIRST_CVE_ID,
    FIRST_PAPER_ID,
    FIRST_RELEASE_ID,
    KEV_QUERY,
    PAPER_QUERY,
    RELEASES_DIR,
    count_rows,
    dump_database,
    fetch_rows,
    kev_catalog,
    read_discards,
    read_log,
    run_import,
    write_collection,
)
from test_transform import FIXTURES_DIR


def test_a_second_import_without_new_raw_reads_nothing_and_changes_nothing(data_dir):
    first = run_import(data_dir)
    tables = dump_database(data_dir)

    second = run_import(data_dir)

    assert first.read_count == 4
    assert second == (0, 0, 0, 0, 0, 0)
    assert dump_database(data_dir) == tables
    assert read_log(data_dir) == []


def test_raw_files_read_again_give_the_same_database_and_log_every_item_as_a_discard(
    data_dir,
):
    # A base of before #106 reads every file again: the base still removes the duplicates.
    # Two catalogs that differ: the older one must not be written over the newer.
    newer_catalog = write_collection(
        data_dir, CISA_KEV_DIR, "2026-10-06T093000Z", kev_catalog("Known")
    )
    first = run_import(data_dir)
    first_tables = dump_database(data_dir)

    second = run_import(data_dir, read_again=True)

    assert dump_database(data_dir) == first_tables
    assert second.item_count == 0
    discards = read_discards(data_dir)
    assert (
        second.discard_count == len(discards) == first.item_count + first.discard_count
    )
    rules_by_file = {}
    for line in discards:
        rules_by_file.setdefault(line["raw_file_path"], set()).add(line["rule"])
    assert rules_by_file == {
        f"{AUTOMATED_WATCH}/{ARXIV_DIR}/{FIRST_COLLECTION}.xml": {"revision_not_newer"},
        f"{AUTOMATED_WATCH}/{CISA_KEV_DIR}/{FIRST_COLLECTION}.csv": {
            "no_newer_version"
        },
        # The first CVE has its version in this very catalog; the others, in the older one.
        newer_catalog: {"no_newer_version", "unchanged"},
        f"{AUTOMATED_WATCH}/{RELEASES_DIR}/{FIRST_COLLECTION}.json": {"already_seen"},
        f"{AUTOMATED_WATCH}/{ADVISORIES_DIR}/{FIRST_COLLECTION}.json": {"already_seen"},
    }


def test_a_new_collection_keeps_the_identifier_of_an_item_already_imported(data_dir):
    run_import(data_dir)
    query = "SELECT item_id FROM vulnerability WHERE cve_id = ?"
    item_id = fetch_rows(data_dir, query, (FIRST_CVE_ID,))[0][0]
    shutil.copyfile(
        FIXTURES_DIR / "cisa_kev_extract.csv",
        data_dir / "raw/automated_watch/cisa_kev/2026-10-06T093000Z.csv",
    )

    run_import(data_dir)

    assert fetch_rows(data_dir, query, (FIRST_CVE_ID,))[0][0] == item_id
    assert count_rows(data_dir, "SELECT count(*) FROM item") == 3 + 4 + 3 + 3


def test_a_release_seen_again_keeps_its_first_collection_and_is_logged_already_seen(
    data_dir,
):
    releases = json.loads((FIXTURES_DIR / "github_releases_extract.json").read_text())
    releases[0]["body"] = "Edited after the first collection."
    again = write_collection(
        data_dir, RELEASES_DIR, "2026-10-06T093000Z", json.dumps(releases)
    )

    run_import(data_dir)

    assert fetch_rows(
        data_dir,
        "SELECT substr(body, 1, 17), raw_file_path FROM release"
        " JOIN item ON item.id = release.item_id"
        " JOIN raw_data ON raw_data.id = item.raw_data_id WHERE release_id = ?",
        (FIRST_RELEASE_ID,),
    ) == [
        (
            "## What's changed",
            f"{AUTOMATED_WATCH}/{RELEASES_DIR}/{FIRST_COLLECTION}.json",
        )
    ]
    assert count_rows(data_dir, "SELECT count(*) FROM release") == 3
    assert read_discards(data_dir, str(FIRST_RELEASE_ID)) == [
        {
            "kind": "discard",
            "rule": "already_seen",
            "source_code": "github",
            "source_item_id": str(FIRST_RELEASE_ID),
            "raw_file_path": again,
            "compared_with_collected_at": "2026-10-05T09:30:00Z",
        }
    ]


def test_an_advisory_seen_again_keeps_its_first_collection_and_is_logged_already_seen(
    data_dir,
):
    advisories = json.loads(
        (FIXTURES_DIR / "github_advisories_extract.json").read_text()
    )
    ghsa_id = advisories[0]["ghsa_id"]
    first_summary = advisories[0]["summary"]
    advisories[0]["summary"] = "Edited after the first collection."
    again = write_collection(
        data_dir, ADVISORIES_DIR, "2026-10-06T093000Z", json.dumps(advisories)
    )

    run_import(data_dir)

    assert fetch_rows(
        data_dir,
        "SELECT summary, raw_file_path FROM advisory"
        " JOIN item ON item.id = advisory.item_id"
        " JOIN raw_data ON raw_data.id = item.raw_data_id WHERE ghsa_id = ?",
        (ghsa_id,),
    ) == [
        (
            first_summary,
            f"{AUTOMATED_WATCH}/{ADVISORIES_DIR}/{FIRST_COLLECTION}.json",
        )
    ]
    assert count_rows(data_dir, "SELECT count(*) FROM advisory") == 3
    assert read_discards(data_dir, ghsa_id) == [
        {
            "kind": "discard",
            "rule": "already_seen",
            "source_code": "github",
            "source_item_id": ghsa_id,
            "raw_file_path": again,
            "compared_with_collected_at": "2026-10-05T09:30:00Z",
        }
    ]


def test_two_advisories_of_the_same_cve_are_both_kept_and_share_one_vulnerability(
    data_dir,
):
    advisories = json.loads(
        (FIXTURES_DIR / "github_advisories_extract.json").read_text()
    )
    advisories[1]["cve_id"] = advisories[0]["cve_id"]
    write_collection(data_dir, ADVISORIES_DIR, FIRST_COLLECTION, json.dumps(advisories))

    run_import(data_dir)

    assert fetch_rows(
        data_dir,
        "SELECT count(*), count(DISTINCT vulnerability_id) FROM advisory"
        " JOIN vulnerability ON vulnerability.id = advisory.vulnerability_id"
        " WHERE cve_id = ?",
        (advisories[0]["cve_id"],),
    ) == [(2, 1)]
    assert read_discards(data_dir) == []


def test_a_modified_kev_row_replaces_the_previous_one_and_dates_revised_at(data_dir):
    run_import(data_dir)
    ((item_id, _, _, _),) = fetch_rows(data_dir, KEV_QUERY, (FIRST_CVE_ID,))
    newer = write_collection(
        data_dir, CISA_KEV_DIR, "2026-10-06T093000Z", kev_catalog("Known")
    )

    run_import(data_dir)

    assert fetch_rows(data_dir, KEV_QUERY, (FIRST_CVE_ID,)) == [
        (item_id, "Known", "2026-10-06T09:30:00Z", newer)
    ]
    assert all(
        line["raw_file_path"] != newer for line in read_discards(data_dir, FIRST_CVE_ID)
    )


def test_an_identical_kev_row_of_a_newer_catalog_changes_nothing_and_is_logged_unchanged(
    data_dir,
):
    newer = write_collection(
        data_dir, CISA_KEV_DIR, "2026-10-06T093000Z", kev_catalog()
    )

    summary = run_import(data_dir)

    assert fetch_rows(data_dir, KEV_QUERY, (FIRST_CVE_ID,))[0][1:] == (
        "Unknown",
        None,
        f"{AUTOMATED_WATCH}/{CISA_KEV_DIR}/{FIRST_COLLECTION}.csv",
    )
    discards = read_discards(data_dir)
    assert summary.discard_count == len(discards) == 4
    assert discards[0] == {
        "kind": "discard",
        "rule": "unchanged",
        "source_code": "cisa_kev",
        "source_item_id": FIRST_CVE_ID,
        "raw_file_path": newer,
        "compared_with_collected_at": "2026-10-05T09:30:00Z",
    }


def test_a_kev_row_of_an_older_catalog_changes_nothing_and_is_logged_no_newer_version(
    data_dir,
):
    run_import(data_dir)
    # A file left unimported, older than the catalog already imported.
    older = write_collection(
        data_dir, CISA_KEV_DIR, "2026-10-04T093000Z", kev_catalog("Known")
    )

    run_import(data_dir)

    assert fetch_rows(data_dir, KEV_QUERY, (FIRST_CVE_ID,))[0][1:] == (
        "Unknown",
        None,
        f"{AUTOMATED_WATCH}/{CISA_KEV_DIR}/{FIRST_COLLECTION}.csv",
    )
    assert [
        line
        for line in read_discards(data_dir, FIRST_CVE_ID)
        if line["raw_file_path"] == older
    ] == [
        {
            "kind": "discard",
            "rule": "no_newer_version",
            "source_code": "cisa_kev",
            "source_item_id": FIRST_CVE_ID,
            "raw_file_path": older,
            "compared_with_collected_at": "2026-10-05T09:30:00Z",
        }
    ]


def test_a_newer_revision_rewrites_the_paper_under_the_same_identifier(data_dir):
    run_import(data_dir)
    item_id = fetch_rows(data_dir, PAPER_QUERY, (FIRST_PAPER_ID,))[0][0]
    revision = (
        data_dir / "raw/automated_watch/arxiv_cscr_x_csai/2026-10-05T093000Z.xml"
    ).read_text()
    revision = revision.replace(
        "<updated>2026-09-25T13:53:50Z</updated>",
        "<updated>2026-10-02T08:00:00Z</updated>",
        1,
    ).replace("Asadullah Abdullah Khan", "Another Author")
    (
        data_dir / "raw/automated_watch/arxiv_cscr_x_csai/2026-10-06T093000Z.xml"
    ).write_text(revision)

    run_import(data_dir)

    ((new_item_id, _, raw_file_path, _, authors, submitted_at, revised_at),) = (
        fetch_rows(data_dir, PAPER_QUERY, (FIRST_PAPER_ID,))
    )
    assert new_item_id == item_id
    assert raw_file_path == "automated_watch/arxiv_cscr_x_csai/2026-10-06T093000Z.xml"
    assert json.loads(authors) == ["Toqeer Ali Syed", "Another Author"]
    assert (submitted_at, revised_at) == (
        "2026-09-25T13:53:50Z",
        "2026-10-02T08:00:00Z",
    )
    assert count_rows(data_dir, "SELECT count(*) FROM item") == 3 + 4 + 3 + 3


@pytest.mark.parametrize("last_revision", ["older", "equal"])
def test_an_arxiv_revision_not_newer_is_discarded_as_revision_not_newer(
    data_dir, last_revision
):
    recorded = (FIXTURES_DIR / "arxiv_extract.xml").read_text()
    revision = recorded.replace(
        "<updated>2026-09-25T13:53:50Z</updated>",
        "<updated>2026-10-02T08:00:00Z</updated>",
        1,
    )
    newer = write_collection(data_dir, ARXIV_DIR, "2026-10-06T093000Z", revision)
    last = write_collection(
        data_dir,
        ARXIV_DIR,
        "2026-10-07T093000Z",
        recorded if last_revision == "older" else revision,
    )

    run_import(data_dir)

    ((_, _, raw_file_path, _, _, _, revised_at),) = fetch_rows(
        data_dir, PAPER_QUERY, (FIRST_PAPER_ID,)
    )
    assert (raw_file_path, revised_at) == (newer, "2026-10-02T08:00:00Z")
    assert [
        line
        for line in read_discards(data_dir, FIRST_PAPER_ID)
        if line["raw_file_path"] == last
    ] == [
        {
            "kind": "discard",
            "rule": "revision_not_newer",
            "source_code": "arxiv",
            "source_item_id": FIRST_PAPER_ID,
            "raw_file_path": last,
            "compared_with_revised_at": "2026-10-02T08:00:00Z",
        }
    ]
