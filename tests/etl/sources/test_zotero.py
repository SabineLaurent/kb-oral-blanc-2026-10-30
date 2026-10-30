import json
import logging
import os
import shutil
import sqlite3
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from k_b.etl.extract import collect_all_sources
from k_b.etl.raw_store import RawStore
from k_b.etl.sources import zotero
from k_b.etl.sources.zotero import (
    READER,
    ZoteroChangedDuringCopyError,
    ZoteroCopyInconsistentError,
    ZoteroDocument,
    ZoteroHighlight,
    ZoteroQueryFailedError,
    ZoteroSource,
    convert_document,
    read_documents,
)
from k_b.items import Highlight

PAPER_WITH_HIGHLIGHTS = {
    "key": "AAAAAAA1",
    "item_type": "preprint",
    "title": "Prompt injection through tool results",
    "date": "2026-09-28 2026-09-28",
    "date_added": "2026-10-01 08:15:00",
    "url": "http://arxiv.org/abs/2609.00001",
    "archive_id": "arXiv:2609.00001",
    "authors": [
        {"first_name": "Ada", "last_name": "Example"},
        {"first_name": "Blaise", "last_name": "Sample"},
    ],
    "tags": [
        {"name": "Computer Science - Cryptography and Security", "type": 1},
        {"name": "to read", "type": 0},
    ],
    "pdfs": [
        {
            "key": "PDFAAAA1",
            "path": "storage:Example and Sample - 2026 - Prompt injection.pdf",
        }
    ],
    "highlights": [
        {
            "key": "ANNOTAA2",
            "pdf_key": "PDFAAAA1",
            "text": "A highlighted sentence on the first page.",
            "comment": "A comment on it.",
            "page_label": "1",
            "date_added": "2026-10-05 08:42:30",
        },
        {
            "key": "ANNOTAA1",
            "pdf_key": "PDFAAAA1",
            "text": "A highlighted sentence on the third page.",
            "comment": None,
            "page_label": "3",
            "date_added": "2026-10-05 08:50:00",
        },
    ],
}
PAPER_WITHOUT_ANYTHING_ATTACHED = {
    "key": "BBBBBBB2",
    "item_type": "preprint",
    "title": "A paper saved without its PDF",
    "date": "2026-09-30 2026-09-30",
    "date_added": "2026-10-03 17:40:12",
    "url": "http://arxiv.org/abs/2609.00002",
    "archive_id": "arXiv:2609.00002",
    "authors": [],
    "tags": [],
    "pdfs": [],
    "highlights": [],
}


@pytest.fixture(autouse=True)
def temporary_root(tmp_path, monkeypatch):
    """Where the source makes its temporary copy: a folder each test can look into."""
    root = tmp_path / "temporary"
    root.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(root))
    return root


def create_source(zotero_dir):
    """The source of a data directory without any raw file yet: each extract is returned."""
    return ZoteroSource(zotero_dir, RawStore(zotero_dir.parent / "data"))


def extract(zotero_dir, last_collected_at=None):
    raw_extract = create_source(zotero_dir).fetch(last_collected_at)
    return json.loads(raw_extract)


def collect(zotero_dir, data_dir, collected_at):
    """kb collect with Zotero alone; return the extracts written, by name."""
    outcome = collect_all_sources(
        [ZoteroSource(zotero_dir, RawStore(data_dir))],
        data_dir / "raw",
        data_dir,
        collected_at,
    )
    assert outcome.errors_by_source["zotero"] is None
    return read_files(data_dir / "raw" / "selected_library" / "zotero")


def read_files(folder):
    return {path.name: path.read_bytes() for path in sorted(folder.iterdir())}


def test_a_paper_is_extracted_with_its_authors_tags_pdf_and_highlights_in_order(
    zotero_dir,
):
    assert extract(zotero_dir)[0] == PAPER_WITH_HIGHLIGHTS


def test_a_paper_with_nothing_attached_is_extracted_with_empty_lists(zotero_dir):
    assert extract(zotero_dir)[1] == PAPER_WITHOUT_ANYTHING_ATTACHED


def test_only_the_documents_of_the_user_library_out_of_the_trash_are_extracted(
    zotero_dir,
):
    """Left out: the trash, a group library, and with them every annotation, PDF and snapshot
    that is not a highlight of a document kept."""
    assert extract(zotero_dir) == [
        PAPER_WITH_HIGHLIGHTS,
        PAPER_WITHOUT_ANYTHING_ATTACHED,
    ]


def test_the_extract_is_utf8_json_and_the_same_bytes_for_the_same_library(
    zotero_dir, run_on_zotero_library
):
    run_on_zotero_library(
        zotero_dir, "UPDATE itemDataValues SET value = 'Résumé' WHERE valueID = 6"
    )
    source = create_source(zotero_dir)

    first_extract = source.fetch(None)

    assert "Résumé".encode() in first_extract
    assert source.fetch(None) == first_extract


def test_what_zotero_has_only_written_to_its_write_ahead_log_is_extracted(zotero_dir):
    # Zotero running: its connection stays open, and its last writes are only in the -wal.
    writer = sqlite3.connect(zotero_dir / "zotero.sqlite")
    try:
        writer.execute("PRAGMA wal_autocheckpoint = 0")
        writer.execute(
            "UPDATE itemDataValues SET value = 'A title changed in the log'"
            " WHERE valueID = 6"
        )
        writer.commit()
        assert (zotero_dir / "zotero.sqlite-wal").stat().st_size > 0

        documents = extract(zotero_dir)
    finally:
        writer.close()

    assert documents[1]["title"] == "A title changed in the log"


def test_a_library_without_a_write_ahead_log_file_is_extracted(zotero_dir):
    (zotero_dir / "zotero.sqlite-wal").unlink(missing_ok=True)
    (zotero_dir / "zotero.sqlite-shm").unlink(missing_ok=True)

    assert len(extract(zotero_dir)) == 2


def test_the_original_is_left_byte_for_byte_and_nothing_is_created_next_to_it(
    zotero_dir,
):
    files_before = read_files(zotero_dir)

    extract(zotero_dir)

    assert read_files(zotero_dir) == files_before


def test_the_temporary_copy_is_deleted_after_an_extraction(zotero_dir, temporary_root):
    extract(zotero_dir)

    assert list(temporary_root.iterdir()) == []


def test_the_temporary_copy_is_deleted_after_a_failure(
    zotero_dir, temporary_root, run_on_zotero_library
):
    run_on_zotero_library(zotero_dir, "DROP TABLE itemAnnotations")

    with pytest.raises(ZoteroQueryFailedError):
        extract(zotero_dir)

    assert list(temporary_root.iterdir()) == []


def test_the_copies_a_killed_process_left_are_removed_before_an_extraction(
    zotero_dir, temporary_root
):
    left_copy = temporary_root / "kb-zotero-left"
    left_copy.mkdir()
    (left_copy / "zotero.sqlite").write_bytes(b"a whole library")
    (temporary_root / "another-program").mkdir()
    (temporary_root / "kb-zotero-not-a-folder").write_bytes(b"")

    extract(zotero_dir)

    assert sorted(path.name for path in temporary_root.iterdir()) == [
        "another-program",
        "kb-zotero-not-a-folder",
    ]


@pytest.mark.parametrize("vanishes_at", ["lstat", "rmtree"])
def test_a_left_copy_that_another_run_removes_first_is_passed_over(
    zotero_dir, temporary_root, monkeypatch, vanishes_at
):
    left_copy = temporary_root / "kb-zotero-left"
    left_copy.mkdir()
    if vanishes_at == "lstat":
        lstat = Path.lstat

        def vanish_then_lstat(self):
            if self.name == "kb-zotero-left":
                self.rmdir()
            return lstat(self)

        monkeypatch.setattr(Path, "lstat", vanish_then_lstat)
    else:
        rmtree = shutil.rmtree

        def vanish_then_rmtree(folder, **options):
            rmtree(folder, **options)
            # tempfile removes its own folder with rmtree too: only the left copy vanishes.
            if Path(folder).name == "kb-zotero-left":
                raise FileNotFoundError(folder)

        monkeypatch.setattr(zotero.shutil, "rmtree", vanish_then_rmtree)

    assert len(extract(zotero_dir)) == 2
    assert not left_copy.exists()


def test_the_copies_a_killed_process_left_are_removed_even_with_nothing_new(
    zotero_dir, temporary_root, tmp_path
):
    data_dir = tmp_path / "data"
    collect(zotero_dir, data_dir, FIRST_COLLECTION_AT)
    (temporary_root / "kb-zotero-left").mkdir()

    assert ZoteroSource(zotero_dir, RawStore(data_dir)).fetch(None) is None
    assert list(temporary_root.iterdir()) == []


def test_the_copy_is_made_in_a_folder_named_after_k_b_and_opened_read_only(
    zotero_dir, temporary_root, monkeypatch
):
    opened = []
    connect = sqlite3.connect

    def record_connection(database, **options):
        opened.append((database, options))
        return connect(database, **options)

    monkeypatch.setattr(zotero.sqlite3, "connect", record_connection)

    extract(zotero_dir)

    [(database, options)] = opened
    assert database.startswith((temporary_root / "kb-zotero-").as_uri())
    assert database.endswith("/zotero.sqlite?mode=ro")
    assert options == {"uri": True}


def test_a_copy_whose_integrity_check_reports_a_problem_is_refused(
    zotero_dir, run_on_zotero_library
):
    # An index declared on another column than the one it was built on: SQLite opens the
    # base, and integrity_check reports the rows missing from the index.
    run_on_zotero_library(
        zotero_dir,
        "CREATE INDEX items_key ON items (key);"
        "PRAGMA writable_schema = ON;"
        "UPDATE sqlite_schema SET sql = 'CREATE INDEX items_key ON items (dateAdded)'"
        " WHERE name = 'items_key';",
    )

    with pytest.raises(ZoteroCopyInconsistentError, match="missing from index"):
        extract(zotero_dir)


def test_a_copy_that_integrity_check_cannot_read_is_refused(zotero_dir):
    (zotero_dir / "zotero.sqlite-wal").unlink(missing_ok=True)
    database_path = zotero_dir / "zotero.sqlite"
    database_path.write_bytes(
        database_path.read_bytes()[: database_path.stat().st_size // 2]
    )

    with pytest.raises(
        ZoteroCopyInconsistentError,
        match="integrity_check raised DatabaseError: 'database disk image is malformed'",
    ):
        extract(zotero_dir)


def change_during_the_copy(monkeypatch, copied_file_name, change):
    """Run change right after the file of that name was copied."""
    copyfile = shutil.copyfile

    def copy_then_change(source, destination):
        copyfile(source, destination)
        if Path(source).name == copied_file_name:
            change()

    monkeypatch.setattr(zotero.shutil, "copyfile", copy_then_change)


def test_a_main_file_written_during_the_copy_is_refused(zotero_dir, monkeypatch):
    database_path = zotero_dir / "zotero.sqlite"
    (zotero_dir / "zotero.sqlite-wal").write_bytes(b"")
    change_during_the_copy(
        monkeypatch,
        "zotero.sqlite-wal",
        lambda: os.utime(database_path, ns=(1, 1)),
    )

    with pytest.raises(ZoteroChangedDuringCopyError):
        extract(zotero_dir)


def test_a_write_ahead_log_written_during_the_copy_is_refused(zotero_dir, monkeypatch):
    wal_path = zotero_dir / "zotero.sqlite-wal"
    wal_path.write_bytes(b"")
    change_during_the_copy(
        monkeypatch, "zotero.sqlite-wal", lambda: wal_path.write_bytes(b"more")
    )

    with pytest.raises(ZoteroChangedDuringCopyError):
        extract(zotero_dir)


def test_a_write_ahead_log_that_appears_during_the_copy_is_refused(
    zotero_dir, monkeypatch
):
    wal_path = zotero_dir / "zotero.sqlite-wal"
    wal_path.unlink(missing_ok=True)
    change_during_the_copy(
        monkeypatch, "zotero.sqlite", lambda: wal_path.write_bytes(b"")
    )

    with pytest.raises(ZoteroChangedDuringCopyError):
        extract(zotero_dir)


def test_a_library_without_a_table_the_query_reads_fails_naming_the_table(
    zotero_dir, run_on_zotero_library
):
    run_on_zotero_library(zotero_dir, "DROP TABLE itemAnnotations")

    with pytest.raises(ZoteroQueryFailedError, match="no such table: itemAnnotations"):
        extract(zotero_dir)


def test_a_library_without_a_column_the_query_reads_fails_naming_the_column(
    zotero_dir,
    run_on_zotero_library,
):
    run_on_zotero_library(
        zotero_dir, "ALTER TABLE itemAnnotations DROP COLUMN pageLabel"
    )

    with pytest.raises(
        ZoteroQueryFailedError, match="no such column: itemAnnotations.pageLabel"
    ):
        extract(zotero_dir)


def test_a_value_sqlite_cannot_put_in_json_fails_with_the_name_sqlite_gives_the_error(
    zotero_dir,
    run_on_zotero_library,
):
    run_on_zotero_library(
        zotero_dir, "UPDATE itemAnnotations SET text = x'00ff' WHERE itemID = 4"
    )

    with pytest.raises(ZoteroQueryFailedError) as failure:
        extract(zotero_dir)

    assert str(failure.value) == "OperationalError (SQLITE_ERROR)"


@pytest.mark.parametrize(
    ("error", "description"),
    [
        (
            # What a SQLite older than 3.44 answers to ORDER BY in an aggregate.
            sqlite3.OperationalError('near "ORDER": syntax error'),
            'near "ORDER": syntax error',
        ),
        (
            sqlite3.DatabaseError("database disk image is malformed"),
            "DatabaseError",
        ),
        (
            sqlite3.OperationalError(
                "Could not decode to UTF-8 column 'title' with text 'A private title'"
            ),
            "OperationalError",
        ),
    ],
)
def test_a_query_error_is_described_without_what_sqlite_quotes_of_the_library(
    error, description
):
    assert zotero.describe_query_error(error) == description


def test_a_text_that_is_not_utf8_fails_without_its_content_in_the_error(
    zotero_dir,
    run_on_zotero_library,
):
    # A text that is not UTF-8: sqlite3 quotes it in its message, which the log must not get.
    run_on_zotero_library(
        zotero_dir,
        "UPDATE itemDataValues SET value = CAST(x'5365637265742074697472653a20ff' AS TEXT)"
        " WHERE valueID = 6",
    )

    with pytest.raises(ZoteroQueryFailedError) as failure:
        extract(zotero_dir)

    assert str(failure.value) == "OperationalError"
    assert failure.value.__cause__ is None
    assert failure.value.__suppress_context__


def test_a_folder_without_the_zotero_database_fails(tmp_path):
    with pytest.raises(FileNotFoundError):
        extract(tmp_path)


FIRST_COLLECTION_AT = datetime(2026, 10, 5, 9, 30, tzinfo=UTC)
SECOND_COLLECTION_AT = datetime(2026, 10, 6, 9, 30, tzinfo=UTC)
THIRD_COLLECTION_AT = datetime(2026, 10, 7, 9, 30, tzinfo=UTC)
CHANGE_A_TITLE = "UPDATE itemDataValues SET value = 'Résumé' WHERE valueID = 6"
# The title of the paper saved without its PDF (tests/fixtures/zotero_extract.sql).
CHANGE_IT_BACK = "UPDATE itemDataValues SET value = 'A paper saved without its PDF' WHERE valueID = 6"


def test_two_collections_of_an_unchanged_library_write_one_extract(
    zotero_dir, tmp_path
):
    data_dir = tmp_path / "data"
    collect(zotero_dir, data_dir, FIRST_COLLECTION_AT)
    # Zotero running writes to its log even when the library does not change.
    wal_path = zotero_dir / "zotero.sqlite-wal"
    wal_path.write_bytes(b"")

    extracts = collect(zotero_dir, data_dir, SECOND_COLLECTION_AT)

    assert list(extracts) == ["2026-10-05T093000Z.json"]


def test_a_library_changed_since_the_last_extract_is_written_again(
    zotero_dir, tmp_path, run_on_zotero_library
):
    data_dir = tmp_path / "data"
    collect(zotero_dir, data_dir, FIRST_COLLECTION_AT)
    run_on_zotero_library(zotero_dir, CHANGE_A_TITLE)

    extracts = collect(zotero_dir, data_dir, SECOND_COLLECTION_AT)

    assert list(extracts) == ["2026-10-05T093000Z.json", "2026-10-06T093000Z.json"]
    assert "Résumé".encode() in extracts["2026-10-06T093000Z.json"]


def test_another_zotero_folder_with_files_older_than_the_last_collection_is_extracted(
    zotero_dir, tmp_path, run_on_zotero_library
):
    data_dir = tmp_path / "data"
    collect(zotero_dir, data_dir, FIRST_COLLECTION_AT)
    other_zotero_dir = tmp_path / "Other Zotero"
    shutil.copytree(zotero_dir, other_zotero_dir)
    run_on_zotero_library(other_zotero_dir, CHANGE_A_TITLE)
    for path in other_zotero_dir.iterdir():
        os.utime(path, ns=(1, 1))

    extracts = collect(other_zotero_dir, data_dir, SECOND_COLLECTION_AT)

    assert len(extracts) == 2


def test_a_library_changed_back_is_compared_with_the_last_extract_only(
    zotero_dir, tmp_path, run_on_zotero_library
):
    """The library back as in the first extract is a third extract, compared with the second
    only."""
    data_dir = tmp_path / "data"
    collect(zotero_dir, data_dir, FIRST_COLLECTION_AT)
    run_on_zotero_library(zotero_dir, CHANGE_A_TITLE)
    collect(zotero_dir, data_dir, SECOND_COLLECTION_AT)
    run_on_zotero_library(zotero_dir, CHANGE_IT_BACK)

    extracts = collect(zotero_dir, data_dir, THIRD_COLLECTION_AT)

    assert list(extracts) == [
        "2026-10-05T093000Z.json",
        "2026-10-06T093000Z.json",
        "2026-10-07T093000Z.json",
    ]
    assert extracts["2026-10-07T093000Z.json"] == extracts["2026-10-05T093000Z.json"]


def test_an_extract_identical_to_the_last_one_is_logged_as_not_written(
    zotero_dir, tmp_path, caplog
):
    data_dir = tmp_path / "data"
    collect(zotero_dir, data_dir, FIRST_COLLECTION_AT)

    with caplog.at_level(logging.INFO, logger="k_b.etl.sources.zotero"):
        collect(zotero_dir, data_dir, SECOND_COLLECTION_AT)

    assert caplog.messages[-1] == (
        "zotero: extract identical to the latest raw file, not written"
    )


def test_a_library_without_any_document_writes_no_extract_and_fails(
    zotero_dir, tmp_path, run_on_zotero_library
):
    """Imported, an empty extract would supersede the last one, and the purge delete it."""
    data_dir = tmp_path / "data"
    # Both documents of the user library in the trash (tests/fixtures/zotero_extract.sql).
    run_on_zotero_library(
        zotero_dir,
        "INSERT INTO deletedItems (itemID, dateDeleted)"
        " VALUES (1, '2026-10-05 20:00:00'), (10, '2026-10-05 20:00:00');",
    )

    outcome = collect_all_sources(
        [ZoteroSource(zotero_dir, RawStore(data_dir))],
        data_dir / "raw",
        data_dir,
        FIRST_COLLECTION_AT,
    )

    error = outcome.errors_by_source["zotero"]
    assert isinstance(error, ValueError)
    assert str(error) == "empty_catalog: 0 entries"
    assert not (data_dir / "raw").exists()


def test_a_library_whose_documents_are_all_invalid_writes_no_extract_and_fails(
    zotero_dir, tmp_path, run_on_zotero_library
):
    """Written, an extract all invalid would become the last one imported, and the purge would
    delete the last valid one."""
    data_dir = tmp_path / "data"
    run_on_zotero_library(zotero_dir, "UPDATE items SET dateAdded = 'yesterday';")

    outcome = collect_all_sources(
        [ZoteroSource(zotero_dir, RawStore(data_dir))],
        data_dir / "raw",
        data_dir,
        FIRST_COLLECTION_AT,
    )

    error = outcome.errors_by_source["zotero"]
    assert isinstance(error, ValueError)
    assert str(error).startswith("all_items_invalid: 2 entries, all rejected; first: ")
    assert "yesterday" not in str(error)
    assert not (data_dir / "raw").exists()


def test_the_log_gives_two_counts_and_no_author_title_or_highlight(zotero_dir, caplog):
    with caplog.at_level(logging.INFO, logger="k_b.etl.sources.zotero"):
        extract(zotero_dir)

    assert caplog.messages == ["zotero: 2 documents, 2 highlights extracted"]


def test_zotero_is_stored_in_the_library_the_user_selected():
    source = ZoteroSource(Path("anywhere"), RawStore(Path("anywhere else")))

    assert (source.acquisition_dir_name, source.name, source.extension) == (
        "selected_library",
        "zotero",
        "json",
    )


COLLECTED_AT = datetime(2026, 10, 5, 9, 30, tzinfo=UTC)
EXTRACT_PATH = "selected_library/zotero/2026-10-05T093000Z.json"


def convert(document):
    return convert_document(document, COLLECTED_AT, EXTRACT_PATH)


def with_changes(document, **changes):
    return {**document, **changes}


def with_first_highlight_changed(document, **changes):
    first, *others = document["highlights"]
    return {**document, "highlights": [{**first, **changes}, *others]}


def test_the_reader_reads_every_document_of_an_extract(zotero_dir):
    raw_extract = create_source(zotero_dir).fetch(None)

    documents = read_documents(raw_extract).entries

    assert documents == [PAPER_WITH_HIGHLIGHTS, PAPER_WITHOUT_ANYTHING_ATTACHED]
    assert READER.source_code == "zotero"


def test_a_document_becomes_a_zotero_entry_with_its_highlights_in_order():
    item = convert(PAPER_WITH_HIGHLIGHTS)

    assert item.model_dump(exclude={"highlights"}, exclude_defaults=True) == {
        "source_code": "zotero",
        "source_item_id": "AAAAAAA1",
        "kind": "zotero_entry",
        "title": "Prompt injection through tool results",
        "url": "http://arxiv.org/abs/2609.00001",
        "saved_at": datetime(2026, 10, 1, 8, 15, tzinfo=UTC),
        "collected_at": COLLECTED_AT,
        "raw_file_path": EXTRACT_PATH,
        "item_type": "preprint",
        "arxiv_id": "2609.00001",
    }
    assert item.highlights == (
        Highlight(
            zotero_key="ANNOTAA2",
            text="A highlighted sentence on the first page.",
            comment="A comment on it.",
            highlighted_at=datetime(2026, 10, 5, 8, 42, 30, tzinfo=UTC),
        ),
        Highlight(
            zotero_key="ANNOTAA1",
            text="A highlighted sentence on the third page.",
            comment=None,
            highlighted_at=datetime(2026, 10, 5, 8, 50, tzinfo=UTC),
        ),
    )


def test_a_document_with_nothing_attached_becomes_an_entry_without_highlight():
    assert convert(PAPER_WITHOUT_ANYTHING_ATTACHED).highlights == ()


def test_the_version_of_an_arxiv_reference_is_left_out():
    item = convert(with_changes(PAPER_WITH_HIGHLIGHTS, archive_id="arXiv:2609.00001v2"))

    assert item.arxiv_id == "2609.00001"


@pytest.mark.parametrize("archive_id", [None, "", "PMID:12345"])
def test_a_document_without_arxiv_reference_has_no_publication(archive_id):
    item = convert(with_changes(PAPER_WITH_HIGHLIGHTS, archive_id=archive_id))

    assert item.arxiv_id is None


@pytest.mark.parametrize(
    "archive_id, arxiv_id",
    [
        ("arXiv:hep-th/9901001", "hep-th/9901001"),
        ("arXiv:math.GT/0309136v3", "math.GT/0309136"),
    ],
)
def test_an_arxiv_reference_of_before_2007_is_read(archive_id, arxiv_id):
    assert convert(
        with_changes(PAPER_WITH_HIGHLIGHTS, archive_id=archive_id)
    ).arxiv_id == (arxiv_id)


@pytest.mark.parametrize("text", [None, ""])
def test_a_highlight_without_text_is_read_with_its_comment(text):
    item = convert(with_first_highlight_changed(PAPER_WITH_HIGHLIGHTS, text=text))

    assert (item.highlights[0].text, item.highlights[0].comment) == (
        None,
        "A comment on it.",
    )


@pytest.mark.parametrize(
    "text, comment", [(None, None), ("", ""), (None, ""), ("", None)]
)
def test_a_highlight_without_text_or_comment_is_left_out_without_refusing_its_document(
    text, comment
):
    item = convert(
        with_first_highlight_changed(PAPER_WITH_HIGHLIGHTS, text=text, comment=comment)
    )

    assert [highlight.zotero_key for highlight in item.highlights] == ["ANNOTAA1"]


def test_an_empty_comment_is_read_absent():
    item = convert(with_first_highlight_changed(PAPER_WITH_HIGHLIGHTS, comment=""))

    assert item.highlights[0].comment is None


def test_a_document_without_url_is_read():
    assert convert(with_changes(PAPER_WITH_HIGHLIGHTS, url=None)).url is None


@pytest.mark.parametrize(
    "document",
    [
        with_changes(PAPER_WITH_HIGHLIGHTS, archive_id="arXiv:cs/010100"),
        with_changes(
            PAPER_WITH_HIGHLIGHTS, archive_id="arXiv:\uff12\uff16\uff10\uff19.00001"
        ),
        with_changes(PAPER_WITH_HIGHLIGHTS, archive_id="arXiv:2609.00001 "),
        with_changes(PAPER_WITH_HIGHLIGHTS, date_added="2026-13-45 10:00:00"),
        with_changes(PAPER_WITH_HIGHLIGHTS, date_added="2026-10-01T08:15:00Z"),
        with_changes(PAPER_WITH_HIGHLIGHTS, date_added=None),
        with_changes(PAPER_WITH_HIGHLIGHTS, title=None),
        with_changes(PAPER_WITH_HIGHLIGHTS, title="A\0title"),
        with_first_highlight_changed(PAPER_WITH_HIGHLIGHTS, text="A \ud800 highlight"),
        with_changes(PAPER_WITH_HIGHLIGHTS, key=None),
        with_changes(PAPER_WITH_HIGHLIGHTS, url="ftp://arxiv.org/abs/2609.00001"),
        with_first_highlight_changed(PAPER_WITH_HIGHLIGHTS, date_added="2026-10-05"),
        {key: value for key, value in PAPER_WITH_HIGHLIGHTS.items() if key != "key"},
        "a document",
        1,
    ],
)
def test_an_invalid_document_is_refused_alone(document):
    with pytest.raises(ValidationError):
        convert(document)


@pytest.mark.parametrize(
    "document, measure",
    [
        (
            with_changes(PAPER_WITH_HIGHLIGHTS, title="SECRET TITLE\n" * 100),
            "title too long: 1300 characters, maximum 1000",
        ),
        (
            with_first_highlight_changed(
                PAPER_WITH_HIGHLIGHTS, text="SECRET HIGHLIGHT\n" * 1_000
            ),
            "at most 10000 characters",
        ),
        (
            with_changes(PAPER_WITH_HIGHLIGHTS, archive_id="arXiv:SECRET"),
            "archive_id",
        ),
    ],
)
def test_a_refused_document_is_described_by_its_field_and_measure_without_its_value(
    document, measure
):
    with pytest.raises(ValidationError) as refusal:
        convert(document)

    reason = str(refusal.value)
    assert "SECRET" not in reason
    assert measure in reason


@pytest.mark.parametrize("raw_extract", [b"not JSON", b"{}", b'"a list"'])
def test_an_extract_that_is_not_a_json_list_is_unreadable(raw_extract):
    with pytest.raises(ValueError, match="not a JSON list"):
        read_documents(raw_extract)


@pytest.mark.parametrize("model", [ZoteroDocument, ZoteroHighlight])
def test_a_refusal_does_not_quote_the_value_received(model):
    with pytest.raises(ValidationError) as refusal:
        model.model_validate({"date_added": "RECEIVED_TEXT"})

    # Defense in depth behind without_values (core/quoting.py, #212).
    assert "RECEIVED_TEXT" not in str(refusal.value)
