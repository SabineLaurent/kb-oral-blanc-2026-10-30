"""The purge of the state catalogs: of Zotero and CISA KEV, only the last raw file imported is
kept."""

import logging
import shutil

import pytest
from load_helpers import run_import
from test_transform import FIXTURES_DIR

from k_b.database.creation import create_database
from k_b.etl.load import read_imported_paths
from k_b.etl.raw_purge import delete_superseded_raw_files
from k_b.etl.raw_store import RawStore

ZOTERO_FOLDER = "raw/selected_library/zotero"
CISA_FOLDER = "raw/automated_watch/cisa_kev"
FOLDER_KEYS = (ZOTERO_FOLDER, CISA_FOLDER)
EMPTY_EXTRACT = b"[]\n"


@pytest.fixture
def data_dir(tmp_path):
    create_database(tmp_path / "k-b.sqlite")
    return tmp_path


def write_raw(data_dir, key, content):
    path = data_dir / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def write_extract(data_dir, collection):
    return write_raw(data_dir, f"{ZOTERO_FOLDER}/{collection}.json", EMPTY_EXTRACT)


def write_catalog(data_dir, collection):
    path = data_dir / CISA_FOLDER / f"{collection}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(FIXTURES_DIR / "cisa_kev_extract.csv", path)
    return path


def purge(data_dir):
    return delete_superseded_raw_files(
        RawStore(data_dir),
        read_imported_paths(data_dir / "k-b.sqlite"),
        FOLDER_KEYS,
    )


def test_only_the_last_extract_and_catalog_imported_are_kept(data_dir):
    extracts = [
        write_extract(data_dir, collection)
        for collection in (
            "2026-10-05T093000Z",
            "2026-10-06T093000Z",
            "2026-10-07T093000Z",
        )
    ]
    catalogs = [
        write_catalog(data_dir, collection)
        for collection in ("2026-10-05T093000Z", "2026-10-06T093000Z")
    ]
    run_import(data_dir)

    assert purge(data_dir) == 3
    assert [path.exists() for path in extracts] == [False, False, True]
    assert [path.exists() for path in catalogs] == [False, True]


def test_a_raw_file_not_imported_yet_is_never_deleted(data_dir):
    # Unreadable, the older one is not registered: the import reads it again each time.
    older_not_imported = write_raw(
        data_dir, f"{ZOTERO_FOLDER}/2026-10-04T093000Z.json", b"not an extract"
    )
    imported = [
        write_extract(data_dir, collection)
        for collection in ("2026-10-05T093000Z", "2026-10-06T093000Z")
    ]
    run_import(data_dir)
    # Collected after the import.
    newer_not_imported = write_extract(data_dir, "2026-10-07T093000Z")

    assert purge(data_dir) == 1
    assert older_not_imported.exists()
    assert newer_not_imported.exists()
    assert [path.exists() for path in imported] == [False, True]


def test_an_unreadable_raw_file_is_not_imported_so_never_deleted(data_dir):
    write_extract(data_dir, "2026-10-05T093000Z")
    unreadable = write_raw(
        data_dir, f"{ZOTERO_FOLDER}/2026-10-06T093000Z.json", b"not an extract"
    )
    run_import(data_dir)

    assert purge(data_dir) == 0
    assert unreadable.exists()


def test_nothing_is_deleted_when_the_last_imported_file_is_missing(data_dir):
    older = write_extract(data_dir, "2026-10-05T093000Z")
    last = write_extract(data_dir, "2026-10-06T093000Z")
    run_import(data_dir)
    last.unlink()

    assert purge(data_dir) == 0
    assert older.exists()


def test_a_purge_run_again_deletes_nothing_more(data_dir):
    for collection in ("2026-10-05T093000Z", "2026-10-06T093000Z"):
        write_extract(data_dir, collection)
    run_import(data_dir)
    purge(data_dir)

    assert purge(data_dir) == 0


def test_the_raw_files_of_another_source_are_never_deleted(data_dir):
    other_source = [
        write_raw(
            data_dir,
            f"raw/automated_watch/arxiv_cscr_x_csai/{collection}.xml",
            (FIXTURES_DIR / "arxiv_extract.xml").read_bytes(),
        )
        for collection in ("2026-10-05T093000Z", "2026-10-06T093000Z")
    ]
    run_import(data_dir)

    assert purge(data_dir) == 0
    assert all(path.exists() for path in other_source)


def test_the_log_gives_the_number_of_files_deleted_never_a_name(data_dir, caplog):
    for collection in ("2026-10-05T093000Z", "2026-10-06T093000Z"):
        write_extract(data_dir, collection)
    run_import(data_dir)

    with caplog.at_level(logging.INFO, logger="k_b.etl.raw_purge"):
        purge(data_dir)

    assert caplog.messages == ["raw purge: 1 superseded files deleted"]
    assert "2026-10-05" not in caplog.text
