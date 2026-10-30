"""The load of the GH Archive rankings: a trend row per repository of its window, no item."""

import json
from datetime import timedelta

import pytest
from load_helpers import (
    AUTOMATED_WATCH,
    CISA_KEV_DIR,
    FIRST_IMPORT_AT,
    count_rows,
    dump_database,
    fetch_rows,
    kev_catalog,
    list_logs,
    log_path_at,
    read_discards,
    read_log,
    rebuild_database,
    registered_paths,
    run_import,
    write_collection,
)
from test_transform import FIXTURES_DIR

from k_b.core.config import GITHUB_TRACKED_REPOSITORIES
from k_b.database.creation import create_database
from k_b.etl.load import (
    import_items,
    read_imported_paths,
)
from k_b.etl.source_kinds import SOURCE_KINDS
from k_b.etl.transform import read_raw_files

# A reduced extract of the GH Archive ranking collected on 2026-10-11: 5 of the 48 rows of the
# window ending on 2026-10-10.
RANKING = json.loads((FIXTURES_DIR / "gh_archive_extract.json").read_text())


GH_ARCHIVE_DIR = "gh_archive"


RANKING_COLLECTION = "2026-10-11T153130Z"


RANKING_COLLECTED_AT = "2026-10-11T15:31:30Z"


LATER_COLLECTION = "2026-10-12T021500Z"


WINDOW_END = "2026-10-10"


NEXT_WINDOW_END = "2026-10-11"


TREND_QUERY = (
    "SELECT window_end, repository_id, repository_name, stars_gained, forks_gained,"
    " raw_file_path FROM trend JOIN raw_data ON raw_data.id = trend.raw_data_id"
    " ORDER BY window_end, trend.id"
)


def write_ranking(data_dir, collection, rows, window_ends=(WINDOW_END,)):
    """A raw result of GH Archive, written as its collection would."""
    source_dir = data_dir / "raw" / AUTOMATED_WATCH / GH_ARCHIVE_DIR
    source_dir.mkdir(parents=True, exist_ok=True)
    (source_dir / f"{collection}.json").write_text(
        json.dumps({"window_ends": list(window_ends), "rows": rows})
    )
    return f"{AUTOMATED_WATCH}/{GH_ARCHIVE_DIR}/{collection}.json"


def rows_of_window(window_end, stars_gained=None):
    """The rows of the fixture, moved to a window, with other stars if given."""
    return [
        row
        | {"window_end": window_end}
        | ({} if stars_gained is None else {"stars_gained": stars_gained})
        for row in RANKING["rows"]
    ]


@pytest.fixture
def trend_data_dir(tmp_path):
    create_database(tmp_path / "k-b.sqlite")
    write_ranking(tmp_path, RANKING_COLLECTION, RANKING["rows"])
    return tmp_path


def test_a_ranking_gives_a_trend_row_per_repository_of_its_window_and_no_item(
    trend_data_dir,
):
    raw_file_path = f"{AUTOMATED_WATCH}/{GH_ARCHIVE_DIR}/{RANKING_COLLECTION}.json"

    summary = run_import(trend_data_dir)

    # The 5 rows share one window: the file being written leaves none of its own rows out.
    assert summary == (0, 0, 0, 1, 0, 5)
    assert fetch_rows(trend_data_dir, TREND_QUERY) == [
        (
            "2026-10-10T00:00:00Z",
            row["repository_id"],
            row["repository_name"],
            row["stars_gained"],
            row["forks_gained"],
            raw_file_path,
        )
        for row in RANKING["rows"]
    ]
    assert fetch_rows(
        trend_data_dir,
        "SELECT source_code, stream, collected_at FROM raw_data"
        " JOIN source ON source.id = raw_data.source_id",
    ) == [("gh_archive", "gh_archive", RANKING_COLLECTED_AT)]
    assert count_rows(trend_data_dir, "SELECT count(*) FROM item") == 0
    assert read_log(trend_data_dir) == []


def test_a_second_import_reads_no_ranking_again_and_changes_nothing(trend_data_dir):
    run_import(trend_data_dir)
    tables = dump_database(trend_data_dir)

    second = run_import(trend_data_dir)

    assert second == (0, 0, 0, 0, 0, 0)
    assert dump_database(trend_data_dir) == tables


def test_a_ranking_read_again_changes_nothing_and_its_window_is_logged_already_seen(
    trend_data_dir,
):
    run_import(trend_data_dir)
    tables = dump_database(trend_data_dir)

    second = run_import(trend_data_dir, read_again=True)

    assert second == (0, 1, 0, 1, 0, 0)
    assert dump_database(trend_data_dir) == tables
    assert read_log(trend_data_dir) == [
        {
            "kind": "discard",
            "rule": "already_seen",
            "source_code": "gh_archive",
            "source_item_id": WINDOW_END,
            "raw_file_path": (
                f"{AUTOMATED_WATCH}/{GH_ARCHIVE_DIR}/{RANKING_COLLECTION}.json"
            ),
            "compared_with_collected_at": RANKING_COLLECTED_AT,
        }
    ]


def test_a_window_collected_again_keeps_its_first_ranking_whole_and_is_logged(
    trend_data_dir,
):
    run_import(trend_data_dir)
    first_ranking = fetch_rows(trend_data_dir, TREND_QUERY)
    # Other figures, and a repository the first ranking did not hold: a new key.
    newcomer = RANKING["rows"][0] | {
        "repository_id": 724712,
        "repository_name": "newcomer/repository",
    }
    again = write_ranking(
        trend_data_dir,
        LATER_COLLECTION,
        [*rows_of_window(WINDOW_END, stars_gained=9_999), newcomer],
    )

    second = run_import(trend_data_dir)

    assert second == (0, 1, 0, 1, 0, 0)
    assert fetch_rows(trend_data_dir, TREND_QUERY) == first_ranking
    assert again in registered_paths(trend_data_dir)
    log = list_logs(trend_data_dir)[-1].read_text()
    assert "newcomer" not in log
    assert RANKING["rows"][0]["repository_name"] not in log
    assert read_log(trend_data_dir) == [
        {
            "kind": "discard",
            "rule": "already_seen",
            "source_code": "gh_archive",
            "source_item_id": WINDOW_END,
            "raw_file_path": again,
            "compared_with_collected_at": RANKING_COLLECTED_AT,
        }
    ]


def test_a_ranking_with_a_known_window_and_a_new_one_writes_the_new_one_only(
    trend_data_dir,
):
    run_import(trend_data_dir)
    later = write_ranking(
        trend_data_dir,
        LATER_COLLECTION,
        [
            *rows_of_window(WINDOW_END, stars_gained=9_999),
            *rows_of_window(NEXT_WINDOW_END, stars_gained=7),
        ],
        window_ends=(WINDOW_END, NEXT_WINDOW_END),
    )

    second = run_import(trend_data_dir)

    assert second == (0, 1, 0, 1, 0, 5)
    assert fetch_rows(
        trend_data_dir,
        "SELECT window_end, count(*), max(stars_gained), count(DISTINCT raw_data_id)"
        " FROM trend GROUP BY window_end ORDER BY window_end",
    ) == [("2026-10-10T00:00:00Z", 5, 1087, 1), ("2026-10-11T00:00:00Z", 5, 7, 1)]
    assert [line["source_item_id"] for line in read_discards(trend_data_dir)] == [
        WINDOW_END
    ]
    assert later in registered_paths(trend_data_dir)


def test_two_rankings_of_a_window_in_one_import_keep_the_one_collected_first(
    trend_data_dir,
):
    later = write_ranking(
        trend_data_dir, LATER_COLLECTION, rows_of_window(WINDOW_END, stars_gained=9_999)
    )

    summary = run_import(trend_data_dir)

    assert summary == (0, 1, 0, 2, 0, 5)
    assert {row[3] for row in fetch_rows(trend_data_dir, TREND_QUERY)} == {
        row["stars_gained"] for row in RANKING["rows"]
    }
    assert [line["raw_file_path"] for line in read_discards(trend_data_dir)] == [later]


def test_a_ranking_refused_is_rejected_logged_and_read_again_at_the_next_import(
    trend_data_dir,
):
    refused = write_ranking(
        trend_data_dir,
        LATER_COLLECTION,
        [RANKING["rows"][0] | {"window_end": NEXT_WINDOW_END, "stars_gained": -1}],
        window_ends=(NEXT_WINDOW_END,),
    )

    first = run_import(trend_data_dir)
    second = run_import(trend_data_dir)

    # The ranking of the fixture is written; the refused one is opened by both imports.
    assert first == (0, 0, 1, 2, 0, 5)
    assert second == (0, 0, 1, 1, 0, 0)
    assert refused not in registered_paths(trend_data_dir)
    [rejection] = read_log(trend_data_dir)
    assert (rejection["kind"], rejection["raw_file_path"]) == ("rejection", refused)
    assert rejection["reason"].startswith(
        "TrendsExtractInvalidError: row 0: stars_gained"
    )
    assert count_rows(trend_data_dir, "SELECT count(*) FROM trend") == 5


def run_import_with_a_ranking_row_changed(data_dir, raw_file_path, **changes):
    """kb import, the last row of the ranking changed after its reading, as by a reader that let
    it through."""
    imported_at = FIRST_IMPORT_AT + timedelta(seconds=len(list_logs(data_dir)))
    database_path = data_dir / "k-b.sqlite"
    imported_paths = read_imported_paths(database_path)
    readings = read_raw_files(
        data_dir / "raw", SOURCE_KINDS, imported_paths, GITHUB_TRACKED_REPOSITORIES
    )
    changed_files = [
        trend_file._replace(
            rows=[*trend_file.rows[:-1], trend_file.rows[-1].model_copy(update=changes)]
        )
        if trend_file.raw_file.raw_file_path == raw_file_path
        else trend_file
        for trend_file in readings.trends.files
    ]
    return import_items(
        readings.items,
        readings.trends._replace(files=changed_files),
        database_path,
        log_path_at(data_dir, imported_at),
        imported_at,
        GITHUB_TRACKED_REPOSITORIES,
    )


def test_a_ranking_with_a_row_the_base_refuses_is_refused_whole_and_read_again(
    trend_data_dir,
):
    refused = write_ranking(
        trend_data_dir,
        LATER_COLLECTION,
        rows_of_window(NEXT_WINDOW_END),
        window_ends=(NEXT_WINDOW_END,),
    )

    first = run_import_with_a_ranking_row_changed(
        trend_data_dir, refused, stars_gained=2**63
    )

    # The ranking of the fixture is written; none of the rows of the refused one is.
    assert first == (0, 0, 1, 2, 0, 5)
    assert refused not in registered_paths(trend_data_dir)
    assert read_log(trend_data_dir) == [
        {
            "kind": "rejection",
            "raw_file_path": refused,
            "reason": "ranking refused by the base: OverflowError: Python int too large"
            " to convert to SQLite INTEGER",
        }
    ]
    assert fetch_rows(trend_data_dir, "SELECT DISTINCT window_end FROM trend") == [
        ("2026-10-10T00:00:00Z",)
    ]

    # Read again by the next import, as an unreadable file: written once it is accepted.
    second = run_import(trend_data_dir)

    assert second == (0, 0, 0, 1, 0, 5)
    assert refused in registered_paths(trend_data_dir)


def test_a_rebuilt_database_with_rankings_equals_the_one_kept_import_after_import(
    data_dir,
):
    # Each import brings a file of items and a ranking: a rebuild reads the four in
    # collection order, as the imports did.
    write_ranking(data_dir, RANKING_COLLECTION, RANKING["rows"])
    run_import(data_dir)
    write_collection(data_dir, CISA_KEV_DIR, "2026-10-12T011500Z", kev_catalog("Known"))
    write_ranking(
        data_dir,
        LATER_COLLECTION,
        rows_of_window(NEXT_WINDOW_END),
        window_ends=(NEXT_WINDOW_END,),
    )
    run_import(data_dir)
    kept = dump_database(data_dir)

    rebuild_database(data_dir)

    assert count_rows(data_dir, "SELECT count(*) FROM trend") == 10
    assert dump_database(data_dir) == kept


def test_a_window_of_the_year_1_is_written_with_a_year_of_four_digits(trend_data_dir):
    # The base refuses a year not written on 4 digits: the day is not formatted by the C
    # library, which does not pad the year on every platform.
    write_ranking(
        trend_data_dir,
        LATER_COLLECTION,
        rows_of_window("0001-01-01"),
        window_ends=("0001-01-01",),
    )

    summary = run_import(trend_data_dir)

    assert summary.trend_count == 10
    assert fetch_rows(
        trend_data_dir, "SELECT DISTINCT window_end FROM trend ORDER BY window_end"
    ) == [("0001-01-01T00:00:00Z",), ("2026-10-10T00:00:00Z",)]
