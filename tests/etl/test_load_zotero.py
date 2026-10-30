"""The import of the Zotero extracts, a state catalog at the level of the entry and of the
highlight, each extract written by the collection from the fabricated library."""

import json
import tempfile

import pytest
from load_helpers import (
    count_rows,
    dump_database,
    fetch_rows,
    read_discards,
    read_log,
    refuse_in_the_base,
    run_import,
)
from test_transform import copy_fixture

from k_b.database.creation import create_database
from k_b.etl import transform as read_raw_items_module
from k_b.etl.normalize import normalize_item
from k_b.etl.raw_store import RawStore
from k_b.etl.sources.zotero import ZoteroSource

FIRST = "2026-10-05T093000Z"
SECOND = "2026-10-06T093000Z"
THIRD = "2026-10-07T093000Z"
FIRST_AT = "2026-10-05T09:30:00Z"
SECOND_AT = "2026-10-06T09:30:00Z"
THIRD_AT = "2026-10-07T09:30:00Z"
EXTRACT_DIR = "selected_library/zotero"
ENTRY_QUERY = (
    "SELECT zotero_key, title, url, saved_at, revised_at, withdrawn_at, raw_file_path"
    " FROM zotero_entry JOIN item ON item.id = zotero_entry.item_id"
    " JOIN raw_data ON raw_data.id = item.raw_data_id ORDER BY zotero_key"
)
HIGHLIGHT_QUERY = (
    "SELECT highlight.zotero_key, zotero_entry.zotero_key, text, comment, highlighted_at,"
    " highlight.revised_at, highlight.withdrawn_at"
    " FROM highlight JOIN zotero_entry ON zotero_entry.item_id = highlight.item_id"
    " ORDER BY highlight.zotero_key"
)
WITHDRAWN_QUERY = (
    "SELECT (SELECT count(*) FROM zotero_entry WHERE withdrawn_at IS NOT NULL),"
    " (SELECT count(*) FROM highlight WHERE withdrawn_at IS NOT NULL)"
)
# The item ids of the library (tests/fixtures/zotero_extract.sql).
FIRST_DOCUMENT = 1
SECOND_DOCUMENT = 10
THIRD_PAGE_HIGHLIGHT = 4
FIRST_PAGE_HIGHLIGHT = 5


@pytest.fixture(autouse=True)
def temporary_root(tmp_path, monkeypatch):
    """The copy of the library is made here, not in the temporary folder of the system."""
    root = tmp_path / "temporary"
    root.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(root))


@pytest.fixture
def data_dir(tmp_path):
    create_database(tmp_path / "k-b.sqlite")
    return tmp_path


def extract_of(zotero_dir):
    # A data directory without any raw file: each extract is returned, even a replay.
    return ZoteroSource(zotero_dir, RawStore(zotero_dir.parent / "data")).fetch(None)


def write_extract(data_dir, collection, raw_extract):
    folder = data_dir / "raw" / EXTRACT_DIR
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{collection}.json").write_bytes(raw_extract)
    return f"{EXTRACT_DIR}/{collection}.json"


def collect(data_dir, zotero_dir, collection):
    """kb collect: the extract of the library as it is now."""
    return write_extract(data_dir, collection, extract_of(zotero_dir))


def delete_in_zotero(run_on_zotero_library, zotero_dir, *item_ids):
    """Move items to the trash: the extraction leaves them out."""
    run_on_zotero_library(
        zotero_dir,
        "".join(
            "INSERT INTO deletedItems (itemID, dateDeleted)"  # noqa: S608 — constants of the tests
            f" VALUES ({item_id}, '2026-10-05 20:00:00');"
            for item_id in item_ids
        ),
    )


def restore_in_zotero(run_on_zotero_library, zotero_dir, item_id):
    run_on_zotero_library(
        zotero_dir,
        f"DELETE FROM deletedItems WHERE itemID = {item_id};",  # noqa: S608 — constants of the tests
    )


def withdrawn_counts(data_dir):
    return tuple(fetch_rows(data_dir, WITHDRAWN_QUERY)[0])


def test_each_document_and_highlight_of_an_extract_goes_into_the_database(
    data_dir, zotero_dir
):
    extract_path = collect(data_dir, zotero_dir, FIRST)

    summary = run_import(data_dir)

    assert (summary.item_count, summary.rejection_count) == (2, 0)
    assert fetch_rows(data_dir, ENTRY_QUERY) == [
        (
            "AAAAAAA1",
            "Prompt injection through tool results",
            "http://arxiv.org/abs/2609.00001",
            "2026-10-01T08:15:00Z",
            None,
            None,
            extract_path,
        ),
        (
            "BBBBBBB2",
            "A paper saved without its PDF",
            "http://arxiv.org/abs/2609.00002",
            "2026-10-03T17:40:12Z",
            None,
            None,
            extract_path,
        ),
    ]
    assert fetch_rows(data_dir, HIGHLIGHT_QUERY) == [
        (
            "ANNOTAA1",
            "AAAAAAA1",
            "A highlighted sentence on the third page.",
            None,
            "2026-10-05T08:50:00Z",
            None,
            None,
        ),
        (
            "ANNOTAA2",
            "AAAAAAA1",
            "A highlighted sentence on the first page.",
            "A comment on it.",
            "2026-10-05T08:42:30Z",
            None,
            None,
        ),
    ]
    assert fetch_rows(data_dir, "SELECT DISTINCT kind FROM item") == [("zotero_entry",)]


def test_the_publication_of_a_paper_arxiv_has_not_given_has_its_reference_alone(
    data_dir, zotero_dir
):
    collect(data_dir, zotero_dir, FIRST)

    run_import(data_dir)

    assert fetch_rows(
        data_dir,
        "SELECT zotero_key, arxiv_id, publication.item_id, publication.title"
        " FROM zotero_entry"
        " JOIN publication ON publication.id = zotero_entry.publication_id"
        " ORDER BY zotero_key",
    ) == [
        ("AAAAAAA1", "2609.00001", None, None),
        ("BBBBBBB2", "2609.00002", None, None),
    ]


def test_an_entry_of_a_paper_arxiv_has_given_points_to_its_publication(
    data_dir, zotero_dir, run_on_zotero_library
):
    copy_fixture(data_dir / "raw", "arxiv_extract.xml", "arxiv_cscr_x_csai", "xml")
    run_on_zotero_library(
        zotero_dir,
        "UPDATE itemDataValues SET value = 'arXiv:2609.31282' WHERE valueID = 5;",
    )
    collect(data_dir, zotero_dir, SECOND)

    run_import(data_dir)

    ((kind, arxiv_title_given),) = fetch_rows(
        data_dir,
        "SELECT item.kind, publication.title IS NOT NULL FROM zotero_entry"
        " JOIN publication ON publication.id = zotero_entry.publication_id"
        " JOIN item ON item.id = publication.item_id WHERE zotero_key = 'AAAAAAA1'",
    )
    assert (kind, arxiv_title_given) == ("paper", 1)
    assert (
        count_rows(
            data_dir, "SELECT count(*) FROM publication WHERE arxiv_id = '2609.31282'"
        )
        == 1
    )


def test_an_identical_newer_extract_changes_nothing_and_is_logged_unchanged(
    data_dir, zotero_dir
):
    collect(data_dir, zotero_dir, FIRST)
    run_import(data_dir)
    entries = fetch_rows(data_dir, ENTRY_QUERY)
    highlights = fetch_rows(data_dir, HIGHLIGHT_QUERY)
    second_path = collect(data_dir, zotero_dir, SECOND)

    summary = run_import(data_dir)

    assert (summary.item_count, summary.discard_count, summary.withdrawn_count) == (
        0,
        2,
        0,
    )
    assert fetch_rows(data_dir, ENTRY_QUERY) == entries
    assert fetch_rows(data_dir, HIGHLIGHT_QUERY) == highlights
    assert read_discards(data_dir) == [
        {
            "kind": "discard",
            "rule": "unchanged",
            "source_code": "zotero",
            "source_item_id": zotero_key,
            "raw_file_path": second_path,
            "compared_with_collected_at": FIRST_AT,
        }
        for zotero_key in ("AAAAAAA1", "BBBBBBB2")
    ]


def test_a_document_changed_in_zotero_is_written_again_and_dated(
    data_dir, zotero_dir, run_on_zotero_library
):
    collect(data_dir, zotero_dir, FIRST)
    run_import(data_dir)
    run_on_zotero_library(
        zotero_dir, "UPDATE itemDataValues SET value = 'A new title' WHERE valueID = 1;"
    )
    second_path = collect(data_dir, zotero_dir, SECOND)

    summary = run_import(data_dir)

    assert summary.item_count == 1
    first_entry = fetch_rows(data_dir, ENTRY_QUERY)[0]
    assert first_entry[1] == "A new title"
    assert first_entry[4:] == (SECOND_AT, None, second_path)
    assert [line["source_item_id"] for line in read_discards(data_dir)] == ["BBBBBBB2"]


def test_a_highlight_changed_in_zotero_is_written_again_and_its_entry_counted_written(
    data_dir, zotero_dir, run_on_zotero_library
):
    first_path = collect(data_dir, zotero_dir, FIRST)
    run_import(data_dir)
    run_on_zotero_library(
        zotero_dir,
        "UPDATE itemAnnotations SET comment = 'Another comment'"  # noqa: S608 — constants of the tests
        f" WHERE itemID = {THIRD_PAGE_HIGHLIGHT};",
    )
    second_path = collect(data_dir, zotero_dir, SECOND)

    summary = run_import(data_dir)

    assert summary.item_count == 1
    third_page_highlight = fetch_rows(data_dir, HIGHLIGHT_QUERY)[0]
    assert third_page_highlight[3:] == (
        "Another comment",
        "2026-10-05T08:50:00Z",
        SECOND_AT,
        None,
    )
    # The entry itself has not changed: not dated, but it points to its current version.
    entries = fetch_rows(data_dir, ENTRY_QUERY)
    assert entries[0][4:] == (None, None, second_path)
    assert entries[1][6] == first_path


def test_a_highlight_deleted_in_zotero_is_withdrawn_at_the_collection(
    data_dir, zotero_dir, run_on_zotero_library
):
    first_path = collect(data_dir, zotero_dir, FIRST)
    run_import(data_dir)
    delete_in_zotero(run_on_zotero_library, zotero_dir, THIRD_PAGE_HIGHLIGHT)
    collect(data_dir, zotero_dir, SECOND)

    summary = run_import(data_dir)

    assert summary.withdrawn_count == 1
    assert [row[6] for row in fetch_rows(data_dir, HIGHLIGHT_QUERY)] == [
        SECOND_AT,
        None,
    ]
    # An absence is not a version: the entry is unchanged, and keeps its file.
    assert [line["rule"] for line in read_discards(data_dir)] == [
        "unchanged",
        "unchanged",
    ]
    assert fetch_rows(data_dir, ENTRY_QUERY)[0][6] == first_path


def test_a_document_deleted_in_zotero_is_withdrawn_with_its_highlights_not_removed(
    data_dir, zotero_dir, run_on_zotero_library
):
    collect(data_dir, zotero_dir, FIRST)
    run_import(data_dir)
    delete_in_zotero(run_on_zotero_library, zotero_dir, FIRST_DOCUMENT)
    collect(data_dir, zotero_dir, SECOND)

    summary = run_import(data_dir)

    assert summary.withdrawn_count == 3
    assert withdrawn_counts(data_dir) == (1, 2)
    assert fetch_rows(data_dir, ENTRY_QUERY)[0][5] == SECOND_AT
    assert count_rows(data_dir, "SELECT count(*) FROM zotero_entry") == 2
    assert count_rows(data_dir, "SELECT count(*) FROM highlight") == 2


def test_the_last_highlights_of_the_library_deleted_are_withdrawn(
    data_dir, zotero_dir, run_on_zotero_library
):
    collect(data_dir, zotero_dir, FIRST)
    run_import(data_dir)
    delete_in_zotero(
        run_on_zotero_library, zotero_dir, THIRD_PAGE_HIGHLIGHT, FIRST_PAGE_HIGHLIGHT
    )
    collect(data_dir, zotero_dir, SECOND)

    run_import(data_dir)

    assert withdrawn_counts(data_dir) == (0, 2)


def test_a_withdrawal_keeps_the_date_of_the_first_absence(
    data_dir, zotero_dir, run_on_zotero_library
):
    collect(data_dir, zotero_dir, FIRST)
    run_import(data_dir)
    delete_in_zotero(run_on_zotero_library, zotero_dir, FIRST_DOCUMENT)
    collect(data_dir, zotero_dir, SECOND)
    run_import(data_dir)
    collect(data_dir, zotero_dir, THIRD)

    summary = run_import(data_dir)

    assert summary.withdrawn_count == 0
    assert fetch_rows(data_dir, ENTRY_QUERY)[0][5] == SECOND_AT


def test_a_withdrawn_document_back_in_a_newer_extract_is_written_again_and_dated(
    data_dir, zotero_dir, run_on_zotero_library
):
    collect(data_dir, zotero_dir, FIRST)
    run_import(data_dir)
    delete_in_zotero(run_on_zotero_library, zotero_dir, FIRST_DOCUMENT)
    collect(data_dir, zotero_dir, SECOND)
    run_import(data_dir)
    restore_in_zotero(run_on_zotero_library, zotero_dir, FIRST_DOCUMENT)
    third_path = collect(data_dir, zotero_dir, THIRD)

    summary = run_import(data_dir)

    assert summary.item_count == 1
    assert fetch_rows(data_dir, ENTRY_QUERY)[0][4:] == (THIRD_AT, None, third_path)
    assert [row[5:] for row in fetch_rows(data_dir, HIGHLIGHT_QUERY)] == [
        (THIRD_AT, None),
        (THIRD_AT, None),
    ]


def test_an_extract_with_a_rejected_document_withdraws_nothing(
    data_dir, zotero_dir, run_on_zotero_library
):
    collect(data_dir, zotero_dir, FIRST)
    run_import(data_dir)
    delete_in_zotero(run_on_zotero_library, zotero_dir, THIRD_PAGE_HIGHLIGHT)
    run_on_zotero_library(
        zotero_dir,
        f"UPDATE itemDataValues SET value = '{'x' * 1_001}' WHERE valueID = 6;",  # noqa: S608 — constants of the tests
    )
    collect(data_dir, zotero_dir, SECOND)

    summary = run_import(data_dir)

    assert (summary.rejection_count, summary.withdrawn_count) == (1, 0)
    assert withdrawn_counts(data_dir) == (0, 0)


def test_an_extract_with_a_document_the_base_refuses_withdraws_nothing(
    data_dir, zotero_dir, run_on_zotero_library, monkeypatch
):
    collect(data_dir, zotero_dir, FIRST)
    run_import(data_dir)
    delete_in_zotero(run_on_zotero_library, zotero_dir, THIRD_PAGE_HIGHLIGHT)
    collect(data_dir, zotero_dir, SECOND)
    refuse_in_the_base(monkeypatch, "AAAAAAA1", {"title": "Lone \ud800 surrogate"})

    summary = run_import(data_dir)

    # A document the base refuses is in the extract, as a document rejected when read.
    assert (summary.rejection_count, summary.withdrawn_count) == (1, 0)
    assert withdrawn_counts(data_dir) == (0, 0)


def test_a_document_with_a_highlight_the_base_refuses_is_written_in_none_of_its_parts(
    data_dir, zotero_dir, monkeypatch
):
    extract_path = collect(data_dir, zotero_dir, FIRST)
    [document] = [
        document
        for document in json.loads(extract_of(zotero_dir))
        if document["key"] == "AAAAAAA1"
    ]
    assert document["highlights"]

    def normalize_then_refuse_last_highlight(item):
        normalized_item = normalize_item(item)
        if normalized_item.source_item_id != "AAAAAAA1":
            return normalized_item
        *kept, last = normalized_item.highlights
        refused = last.model_copy(update={"text": "\ud800"})
        return normalized_item.model_copy(update={"highlights": [*kept, refused]})

    monkeypatch.setattr(
        read_raw_items_module, "normalize_item", normalize_then_refuse_last_highlight
    )

    summary = run_import(data_dir)

    # The entry, its highlights and the publication it cites share one savepoint.
    assert (summary.item_count, summary.rejection_count) == (1, 1)
    assert [row[0] for row in fetch_rows(data_dir, ENTRY_QUERY)] == ["BBBBBBB2"]
    assert {row[1] for row in fetch_rows(data_dir, HIGHLIGHT_QUERY)} <= {"BBBBBBB2"}
    assert (
        count_rows(
            data_dir, "SELECT count(*) FROM publication WHERE arxiv_id = '2609.00001'"
        )
        == 0
    )
    assert count_rows(data_dir, "SELECT count(*) FROM item") == 1
    [rejection] = [line for line in read_log(data_dir) if line["kind"] == "rejection"]
    assert rejection == {
        "kind": "rejection",
        "raw_file_path": extract_path,
        "reason": "zotero_entry 'AAAAAAA1' refused by the base: UnicodeEncodeError:"
        " surrogates not allowed at position 0",
    }


def test_an_older_extract_imported_late_changes_nothing_and_writes_its_own_entry_withdrawn(
    data_dir, zotero_dir, run_on_zotero_library
):
    older_extract = extract_of(zotero_dir)
    run_on_zotero_library(
        zotero_dir, "UPDATE itemDataValues SET value = 'A new title' WHERE valueID = 1;"
    )
    delete_in_zotero(run_on_zotero_library, zotero_dir, SECOND_DOCUMENT)
    collect(data_dir, zotero_dir, SECOND)
    run_import(data_dir)
    older_path = write_extract(data_dir, FIRST, older_extract)

    summary = run_import(data_dir)

    assert summary.withdrawn_count == 0
    entries = fetch_rows(data_dir, ENTRY_QUERY)
    assert entries[0][1] == "A new title"
    # Only the older extract has it, and the latest one does not list it: withdrawn at it.
    assert entries[1][5:] == (SECOND_AT, older_path)
    assert [
        (line["source_item_id"], line["rule"]) for line in read_discards(data_dir)
    ] == [("AAAAAAA1", "no_newer_version")]


def test_a_rebuilt_database_has_the_content_of_the_one_kept_import_after_import(
    tmp_path, zotero_dir, run_on_zotero_library
):
    kept_dir = tmp_path / "kept"
    rebuilt_dir = tmp_path / "rebuilt"
    for folder in (kept_dir, rebuilt_dir):
        folder.mkdir()
        create_database(folder / "k-b.sqlite")
    changes = [
        lambda: None,
        lambda: (
            run_on_zotero_library(
                zotero_dir,
                "UPDATE itemAnnotations SET comment = 'Another'"  # noqa: S608 — constants of the tests
                f" WHERE itemID = {FIRST_PAGE_HIGHLIGHT};",
            ),
            delete_in_zotero(run_on_zotero_library, zotero_dir, SECOND_DOCUMENT),
        ),
        lambda: restore_in_zotero(run_on_zotero_library, zotero_dir, SECOND_DOCUMENT),
    ]
    for collection, change in zip((FIRST, SECOND, THIRD), changes, strict=True):
        change()
        raw_extract = extract_of(zotero_dir)
        write_extract(kept_dir, collection, raw_extract)
        run_import(kept_dir)
        write_extract(rebuilt_dir, collection, raw_extract)

    run_import(rebuilt_dir)

    assert dump_database(rebuilt_dir) == dump_database(kept_dir)


def test_a_rejected_document_is_logged_without_its_title_or_highlight_text(
    data_dir, zotero_dir
):
    documents = json.loads(extract_of(zotero_dir))
    documents[0]["highlights"][0]["text"] = "SECRET HIGHLIGHT\n" * 1_000
    documents[1]["title"] = "SECRET TITLE\n" * 100
    extract_path = write_extract(
        data_dir, FIRST, json.dumps(documents, ensure_ascii=False).encode()
    )

    summary = run_import(data_dir)

    assert summary.rejection_count == 2
    rejections = [line for line in read_log(data_dir) if line["kind"] == "rejection"]
    assert [line["raw_file_path"] for line in rejections] == [extract_path] * 2
    assert "SECRET" not in json.dumps(rejections)
    assert "title too long: 1300 characters, maximum 1000" in rejections[1]["reason"]


def test_a_highlight_without_text_goes_into_the_database_with_its_comment_only(
    data_dir, zotero_dir, run_on_zotero_library
):
    # The highlight of the first page has a comment, the one of the third page has none.
    run_on_zotero_library(
        zotero_dir,
        "UPDATE itemAnnotations SET text = NULL"  # noqa: S608 — constants of the tests
        f" WHERE itemID IN ({THIRD_PAGE_HIGHLIGHT}, {FIRST_PAGE_HIGHLIGHT});",
    )
    collect(data_dir, zotero_dir, FIRST)

    summary = run_import(data_dir)

    assert summary.rejection_count == 0
    assert [row[:4] for row in fetch_rows(data_dir, HIGHLIGHT_QUERY)] == [
        ("ANNOTAA2", "AAAAAAA1", None, "A comment on it.")
    ]


def test_an_older_extract_imported_late_creates_no_publication_for_a_reference_since_changed(
    data_dir, zotero_dir, run_on_zotero_library
):
    run_on_zotero_library(
        zotero_dir,
        "UPDATE itemDataValues SET value = 'arXiv:2609.11111' WHERE valueID = 5;",
    )
    older_extract = extract_of(zotero_dir)
    run_on_zotero_library(
        zotero_dir,
        "UPDATE itemDataValues SET value = 'arXiv:2609.00001' WHERE valueID = 5;",
    )
    collect(data_dir, zotero_dir, SECOND)
    run_import(data_dir)
    write_extract(data_dir, FIRST, older_extract)

    run_import(data_dir)

    assert fetch_rows(
        data_dir, "SELECT arxiv_id FROM publication ORDER BY arxiv_id"
    ) == [
        ("2609.00001",),
        ("2609.00002",),
    ]
