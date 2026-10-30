import json
from datetime import UTC, datetime

import pytest

from k_b.core.quoting import quote_value
from k_b.etl.raw_store import (
    COLLECTION_STATE_FILENAME,
    InvalidCollectionStateError,
    RawStore,
    find_stream,
    load_last_collection_dates,
    make_source_folder_key,
    save_last_collection_dates,
    write_new_raw_file,
)


def test_write_new_raw_file_refuses_to_overwrite(tmp_path):
    raw_file_path = tmp_path / "automated_watch" / "fake" / "2026-09-30T100000Z.json"
    write_new_raw_file(raw_file_path, b"first")

    with pytest.raises(FileExistsError):
        write_new_raw_file(raw_file_path, b"second")

    assert raw_file_path.read_bytes() == b"first"


def test_last_collection_dates_are_empty_before_the_first_collection(tmp_path):
    assert load_last_collection_dates(tmp_path) == {}


def test_last_collection_dates_round_trip_as_utc_aware_datetimes(tmp_path):
    collected_at = datetime(2026, 9, 30, 10, 0, tzinfo=UTC)
    save_last_collection_dates(tmp_path, {"fake": collected_at})

    last_collection_dates = load_last_collection_dates(tmp_path)

    assert last_collection_dates == {"fake": collected_at}
    assert last_collection_dates["fake"].tzinfo is not None


@pytest.mark.parametrize(
    "state_file_content",
    [
        '{"fake": "2026-09-30T10:00:00"}',  # naive date
        '{"fake": "not a date"}',
        '{"fake": 42}',
        '["fake"]',
        '{"fake": ',  # truncated JSON
    ],
)
def test_invalid_collection_state_is_refused(tmp_path, state_file_content):
    (tmp_path / COLLECTION_STATE_FILENAME).write_text(
        state_file_content, encoding="utf-8"
    )

    with pytest.raises(InvalidCollectionStateError):
        load_last_collection_dates(tmp_path)


def test_a_collection_state_that_is_not_utf8_is_refused(tmp_path):
    (tmp_path / COLLECTION_STATE_FILENAME).write_bytes(
        '{"caf\u00e9": 1}'.encode("latin-1")
    )

    with pytest.raises(InvalidCollectionStateError):
        load_last_collection_dates(tmp_path)


@pytest.mark.parametrize(
    ("raw_file_path", "stream"),
    [
        ("automated_watch/cisa_kev/2026-10-05T093000Z.csv", "cisa_kev"),
        ("automated_watch/tldr_ai/2026-10-07T084509Z/2026-10-07.html", "tldr_ai"),
    ],
)
def test_the_stream_is_the_folder_of_the_source(raw_file_path, stream):
    assert find_stream(raw_file_path) == stream


def test_a_path_without_a_source_folder_is_refused():
    with pytest.raises(ValueError, match="cisa_kev.csv"):
        find_stream("automated_watch/cisa_kev.csv")


def test_a_long_source_name_and_date_of_the_state_are_quoted_cut_in_the_refusal(
    tmp_path,
):
    long_name = "source\n" + "n" * 50_000
    long_date = "date\n" + "d" * 50_000
    (tmp_path / "collection_state.json").write_text(
        json.dumps({long_name: long_date}), encoding="utf-8"
    )

    with pytest.raises(InvalidCollectionStateError) as raised:
        load_last_collection_dates(tmp_path)

    message = str(raised.value)
    assert (
        f"date of {quote_value(long_name)} is invalid: {quote_value(long_date)}"
        in message
    )
    assert "\n" not in message
    assert len(message) < 1_000


def test_a_key_is_deleted_under_the_data_directory_once(tmp_path):
    raw_file = (
        tmp_path / "raw" / "automated_watch" / "cisa_kev" / "2026-10-05T093000Z.csv"
    )
    raw_file.parent.mkdir(parents=True)
    raw_file.write_bytes(b"catalog")
    store = RawStore(tmp_path)

    assert store.has_file("raw/automated_watch/cisa_kev/2026-10-05T093000Z.csv")
    assert store.delete("raw/automated_watch/cisa_kev/2026-10-05T093000Z.csv")
    assert not raw_file.exists()
    assert not store.has_file("raw/automated_watch/cisa_kev/2026-10-05T093000Z.csv")
    assert not store.delete("raw/automated_watch/cisa_kev/2026-10-05T093000Z.csv")


@pytest.mark.parametrize(
    "key", ["", "/etc/passwd", "raw/../k-b.sqlite", "../outside", "raw\\..\\k-b.sqlite"]
)
def test_a_key_that_is_not_relative_to_the_data_directory_is_refused(tmp_path, key):
    store = RawStore(tmp_path / "data")

    for method in (
        store.delete,
        store.has_file,
        store.read_latest,
        store.list_file_keys,
        store.read,
    ):
        with pytest.raises(
            ValueError, match="not a key relative to the data directory"
        ):
            method(key)


def test_the_latest_raw_file_of_a_folder_is_read_hidden_files_left_out(tmp_path):
    folder = tmp_path / "raw" / "selected_library" / "zotero"
    folder.mkdir(parents=True)
    (folder / "2026-10-05T093000Z.json").write_bytes(b"older")
    (folder / "2026-10-06T093000Z.json").write_bytes(b"latest")
    # A rest of an interrupted write, as write_file_atomically names it.
    (folder / ".2026-10-07T093000Z.json.abc.tmp").write_bytes(b"half")

    assert RawStore(tmp_path).read_latest("raw/selected_library/zotero") == b"latest"


def test_a_folder_without_raw_file_has_no_latest(tmp_path):
    (tmp_path / "raw" / "selected_library" / "zotero").mkdir(parents=True)
    store = RawStore(tmp_path)

    assert store.read_latest("raw/selected_library/zotero") is None
    assert store.read_latest("raw/selected_library/absent") is None


def test_the_keys_of_a_folder_are_listed_in_name_order_hidden_files_left_out(tmp_path):
    folder = tmp_path / "raw" / "automated_watch" / "github_most_starred"
    folder.mkdir(parents=True)
    (folder / "2026-10-06T093000Z.json").write_bytes(b"latest")
    (folder / "2026-10-05T093000Z.json").write_bytes(b"older")
    # A rest of an interrupted write, and the pages of a collection, in a subfolder.
    (folder / ".2026-10-07T093000Z.json.abc.tmp").write_bytes(b"half")
    (folder / "2026-10-07T093000Z").mkdir()
    store = RawStore(tmp_path)

    keys = store.list_file_keys("raw/automated_watch/github_most_starred")

    assert keys == [
        "raw/automated_watch/github_most_starred/2026-10-05T093000Z.json",
        "raw/automated_watch/github_most_starred/2026-10-06T093000Z.json",
    ]
    assert [store.read(key) for key in keys] == [b"older", b"latest"]


def test_a_folder_that_does_not_exist_has_no_key(tmp_path):
    assert RawStore(tmp_path).list_file_keys("raw/automated_watch/absent") == []


def test_the_folder_key_of_a_source_is_under_raw_and_its_acquisition_folder():
    assert make_source_folder_key("selected_library", "zotero") == (
        "raw/selected_library/zotero"
    )
