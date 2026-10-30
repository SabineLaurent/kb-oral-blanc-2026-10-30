"""What the load rejects or refuses: logged, the others imported, the base unchanged on a failure."""

import json
import sqlite3

import pytest
from load_helpers import (
    ARXIV_DIR,
    AUTOMATED_WATCH,
    CISA_KEV_PATH,
    CVE_ID_IN_ARABIC_INDIC_DIGITS,
    FIRST_COLLECTION,
    FIRST_CVE_ID,
    FIRST_IMPORT_AT,
    FIRST_PAPER_ID,
    FIRST_RELEASE_ID,
    RELEASES_DIR,
    RELEASES_PATH,
    count_rows,
    dump_database,
    list_logs,
    log_path_at,
    read_log,
    refuse_in_the_base,
    registered_paths,
    run_import,
    write_collection,
)
from test_transform import FIXTURES_DIR, convert_line

from k_b.core.config import GITHUB_TRACKED_REPOSITORIES
from k_b.database import writing
from k_b.database.connection import MissingDatabaseError, connect_database
from k_b.database.creation import create_database
from k_b.database.writing import KindWithoutTableError, UnknownSourceError
from k_b.etl.load import (
    import_items,
    read_imported_paths,
)
from k_b.etl.raw_files import RawReading, TrendReading
from k_b.etl.source_contract import EntryReading, SourceKind, SourceReader


def test_each_rejected_file_is_written_to_the_log(data_dir):
    (
        data_dir / "raw/automated_watch/arxiv_cscr_x_csai/2026-10-06T093000Z.xml"
    ).write_text("not xml")

    summary = run_import(data_dir)

    rejections = [line for line in read_log(data_dir) if line["kind"] == "rejection"]
    assert summary.rejection_count == len(rejections) == 1
    assert (
        rejections[0]["raw_file_path"]
        == "automated_watch/arxiv_cscr_x_csai/2026-10-06T093000Z.xml"
    )
    assert rejections[0]["reason"]


def test_an_invalid_release_is_written_to_the_log_and_the_others_are_imported(
    data_dir,
):
    releases = json.loads((FIXTURES_DIR / "github_releases_extract.json").read_text())
    releases[1]["tag_name"] = None
    raw_file_path = write_collection(
        data_dir, RELEASES_DIR, "2026-10-06T093000Z", json.dumps(releases)
    )

    run_import(data_dir)

    rejections = [line for line in read_log(data_dir) if line["kind"] == "rejection"]
    assert rejections == [
        {
            "kind": "rejection",
            "raw_file_path": raw_file_path,
            "reason": "release 1 (id 400266224): tag_name is not a string",
        }
    ]
    assert count_rows(data_dir, "SELECT count(*) FROM release") == 3


def test_a_failed_import_leaves_the_database_and_the_log_unchanged(
    data_dir, monkeypatch
):
    tables_before = dump_database(data_dir)

    # The papers come first, in folder order: they are written, then the system fails on a
    # vulnerability. An error of the system is not due to the entry: it stops the import.
    def fail_on_vulnerability(connection, item, raw_data_id):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setitem(writing.WRITER_BY_KIND, "vulnerability", fail_on_vulnerability)

    with pytest.raises(sqlite3.OperationalError, match="disk I/O error"):
        run_import(data_dir)

    assert dump_database(data_dir) == tables_before
    assert list_logs(data_dir) == []


def test_a_foreign_key_the_base_refuses_stops_the_import_and_writes_nothing(
    data_dir, monkeypatch
):
    tables_before = dump_database(data_dir)
    write_vulnerability = writing.WRITER_BY_KIND["vulnerability"]

    # A writer that points its item at a raw file the base does not have: not due to the
    # entry, it is not a rejection.
    def write_with_an_unknown_raw_file(connection, item, raw_data_id):
        return write_vulnerability(connection, item, raw_data_id + 1_000)

    monkeypatch.setitem(
        writing.WRITER_BY_KIND,
        "vulnerability",
        write_with_an_unknown_raw_file,
    )

    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        run_import(data_dir)

    assert dump_database(data_dir) == tables_before
    assert list_logs(data_dir) == []


ARXIV_PATH = f"{AUTOMATED_WATCH}/{ARXIV_DIR}/{FIRST_COLLECTION}.xml"


# The four cases of PR #131 (docs/2026-10-11-revue-homogeneite.md): each one written to the
# base, the error it raises is one of an entry.
@pytest.mark.parametrize(
    ("source_item_id", "changes", "raw_file_path", "reason", "value"),
    [
        (
            str(FIRST_RELEASE_ID),
            {"source_item_id": str(2**63)},
            RELEASES_PATH,
            # The identifier names the item, quoted (conventions.md, rule 3), even when it is
            # the value refused; the refusal itself never quotes it.
            (
                "release '9223372036854775808' refused by the base: OverflowError: Python"
                " int too large to convert to SQLite INTEGER"
            ),
            str(2**63),
        ),
        (
            FIRST_CVE_ID,
            {"cve": CVE_ID_IN_ARABIC_INDIC_DIGITS},
            CISA_KEV_PATH,
            (
                "vulnerability 'CVE-2026-86950' refused by the base:"
                " SQLITE_CONSTRAINT_CHECK: CHECK constraint failed: cve_id GLOB"
                " 'CVE-[0-9][0-9][0-9][0-9]-[0-9][0-9][0-9][0-9]*'"
            ),
            "٨",
        ),
        (
            FIRST_PAPER_ID,
            {"title": "Lone \ud800 surrogate"},
            ARXIV_PATH,
            (
                "paper '2609.31282' refused by the base: UnicodeEncodeError: surrogates"
                " not allowed at position 5"
            ),
            "Lone",
        ),
        (
            "400266224",
            {"tag_name": "\x00v1.0.0"},
            RELEASES_PATH,
            (
                "release '400266224' refused by the base: SQLITE_CONSTRAINT_CHECK: CHECK"
                " constraint failed: length(tag_name) > 0"
            ),
            "v1.0.0",
        ),
        # Beyond the four cases: a NOT NULL refused is due to the entry too.
        (
            "399375132",
            {"tag_name": None},
            RELEASES_PATH,
            (
                "release '399375132' refused by the base: SQLITE_CONSTRAINT_NOTNULL: NOT"
                " NULL constraint failed: release.tag_name"
            ),
            "None",
        ),
    ],
)
def test_an_item_the_base_refuses_is_rejected_and_logged_and_the_others_are_imported(
    data_dir, monkeypatch, source_item_id, changes, raw_file_path, reason, value
):
    refuse_in_the_base(monkeypatch, source_item_id, changes)

    summary = run_import(data_dir)

    rejections = [line for line in read_log(data_dir) if line["kind"] == "rejection"]
    assert rejections == [
        {"kind": "rejection", "raw_file_path": raw_file_path, "reason": reason}
    ]
    assert value not in rejections[0]["reason"].partition("refused by the base: ")[2]
    assert summary.rejection_count == 1
    # The other items are written; the item refused is not counted, and leaves no row.
    assert summary.item_count == count_rows(data_dir, "SELECT count(*) FROM item") > 0
    assert (
        count_rows(
            data_dir,
            "SELECT count(*) FROM item WHERE id NOT IN (SELECT item_id FROM publication"
            " WHERE item_id IS NOT NULL UNION SELECT item_id FROM vulnerability"
            " WHERE item_id IS NOT NULL UNION SELECT item_id FROM release"
            " UNION SELECT item_id FROM advisory)",
        )
        == 0
    )
    assert raw_file_path in registered_paths(data_dir)


def test_an_item_of_a_kind_without_writer_is_refused_and_nothing_written(
    tmp_path, monkeypatch
):
    # Every kind has its writer: one is removed, as for a kind added before its table.
    monkeypatch.delitem(writing.WRITER_BY_KIND, "newsletter_article")
    source_dir = tmp_path / "raw" / "automated_watch" / "fake"
    source_dir.mkdir(parents=True)
    (source_dir / "2026-10-05T093000Z.txt").write_text("v1.0\n")
    create_database(tmp_path / "k-b.sqlite")
    kinds = [
        SourceKind(
            "fake",
            (),
            SourceReader(
                "tldr",
                lambda raw: EntryReading(raw.decode().splitlines(), []),
                convert_line,
            ),
        )
    ]

    with pytest.raises(KindWithoutTableError, match="newsletter_article"):
        run_import(tmp_path, kinds=kinds)

    assert count_rows(tmp_path, "SELECT count(*) FROM item") == 0
    assert count_rows(tmp_path, "SELECT count(*) FROM raw_data") == 0


def test_an_item_of_a_source_missing_from_the_database_is_refused(data_dir):
    connection = connect_database(data_dir / "k-b.sqlite")
    with connection:
        connection.execute("DELETE FROM source WHERE source_code = 'cisa_kev'")
    connection.close()

    with pytest.raises(UnknownSourceError, match="cisa_kev"):
        run_import(data_dir)

    assert count_rows(data_dir, "SELECT count(*) FROM item") == 0


def test_a_missing_database_is_refused_and_not_created(tmp_path):
    with pytest.raises(MissingDatabaseError):
        read_imported_paths(tmp_path / "k-b.sqlite")
    with pytest.raises(MissingDatabaseError):
        import_items(
            RawReading([], [], 0),
            TrendReading([], [], 0),
            tmp_path / "k-b.sqlite",
            log_path_at(tmp_path, FIRST_IMPORT_AT),
            FIRST_IMPORT_AT,
            GITHUB_TRACKED_REPOSITORIES,
        )

    assert not (tmp_path / "k-b.sqlite").exists()
