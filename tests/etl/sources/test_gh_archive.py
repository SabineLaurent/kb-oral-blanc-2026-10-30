import json
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from k_b.core.config import GH_ARCHIVE_MAX_CATCH_UP_WINDOWS
from k_b.etl.sources import gh_archive
from k_b.etl.sources.gh_archive import (
    MAX_BYTES_BILLED,
    MAX_BYTES_PER_WINDOW,
    QUERY_TIMEOUT_SECONDS,
    TRENDS_QUERY,
    DayTableNotClosedError,
    DryRunOverLimitError,
    DryRunVolumeUnknownError,
    GhArchiveSource,
    TrendsExtractInvalidError,
    compute_bytes_cap,
    list_window_ends,
    read_trends,
)

FIXTURE_PATH = (
    Path(__file__).parent.parent.parent / "fixtures" / "gh_archive_extract.json"
)
TODAY = date(2026, 10, 11)
YESTERDAY = TODAY - timedelta(days=1)
# The table of yesterday took its last hour at 00:08 UTC today (seen on 2026-10-11).
CLOSED_AT = datetime(2026, 10, 11, 0, 8, 24, tzinfo=UTC)


def read_fixture():
    return json.loads(FIXTURE_PATH.read_bytes())


def fixture_rows_for(window_ends):
    """The rows of the fixture, once per window, as BigQuery gives them (dates, not text)."""
    [fixture_window_end] = read_fixture()["window_ends"]
    rows = [
        row for row in read_fixture()["rows"] if row["window_end"] == fixture_window_end
    ]
    return [
        {**row, "window_end": window_end} for window_end in window_ends for row in rows
    ]


class FakeTable:
    def __init__(self, modified):
        self.modified = modified


class FakeJob:
    def __init__(self, total_bytes_processed, rows):
        self.total_bytes_processed = total_bytes_processed
        self.rows = rows
        self.result_timeouts = []

    def result(self, timeout=None):
        self.result_timeouts.append(timeout)
        return self.rows


class FakeClient:
    """Answers like BigQuery: a dry run gives a volume, a job gives the rows of its windows."""

    def __init__(
        self,
        dry_run_bytes=lambda window_count: 134_000_000 * window_count,
        table_modified=CLOSED_AT,
    ):
        self.dry_run_bytes = dry_run_bytes
        self.table_modified = table_modified
        self.table_requests = []
        self.queries = []
        self.jobs = []
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *exception):
        self.closed = True

    def get_table(self, table_id, timeout=None):
        self.table_requests.append((table_id, timeout))
        return FakeTable(self.table_modified)

    def query(self, query, job_config=None, timeout=None):
        self.queries.append((query, job_config, timeout))
        window_ends = read_parameters(job_config)["window_ends"]
        if job_config.dry_run:
            job = FakeJob(self.dry_run_bytes(len(window_ends)), [])
        else:
            job = FakeJob(None, fixture_rows_for(window_ends))
        self.jobs.append(job)
        return job

    def executed_configs(self):
        return [config for _, config, _ in self.queries if not config.dry_run]

    def dry_run_configs(self):
        return [config for _, config, _ in self.queries if config.dry_run]


def read_parameters(job_config):
    parameters = {}
    for parameter in job_config.query_parameters:
        if hasattr(parameter, "values"):
            parameters[parameter.name] = list(parameter.values)
        else:
            parameters[parameter.name] = parameter.value
    return parameters


@pytest.fixture
def today(monkeypatch):
    monkeypatch.setattr(gh_archive, "current_day", lambda: TODAY)
    return TODAY


# The windows of a collection.


def test_a_first_collection_takes_the_window_that_ends_yesterday():
    assert list_window_ends(None, TODAY) == [YESTERDAY]


def test_a_collection_the_day_after_the_last_takes_one_window():
    last_collected_at = datetime(2026, 10, 10, 9, 0, tzinfo=UTC)

    assert list_window_ends(last_collected_at, TODAY) == [YESTERDAY]


def test_the_windows_missed_since_the_last_collection_are_caught_up():
    # Collected on 10/08: its window ended on 10/07; those ending 10/08, 10/09, 10/10 are missed.
    last_collected_at = datetime(2026, 10, 8, 9, 0, tzinfo=UTC)

    assert list_window_ends(last_collected_at, TODAY) == [
        date(2026, 10, 8),
        date(2026, 10, 9),
        date(2026, 10, 10),
    ]


def test_a_second_collection_the_same_day_takes_no_window():
    last_collected_at = datetime(2026, 10, 11, 0, 30, tzinfo=UTC)

    assert list_window_ends(last_collected_at, TODAY) == []


def test_the_catch_up_keeps_the_most_recent_windows_up_to_its_maximum():
    last_collected_at = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)

    window_ends = list_window_ends(last_collected_at, TODAY)

    assert len(window_ends) == GH_ARCHIVE_MAX_CATCH_UP_WINDOWS == 15
    assert window_ends[-1] == YESTERDAY
    assert window_ends[0] == YESTERDAY - timedelta(days=14)


# The cap grows with the windows, up to a bound.


@pytest.mark.parametrize(
    ("window_count", "cap"),
    [(1, 650_000_000), (3, 1_950_000_000), (4, 2_000_000_000), (15, 2_000_000_000)],
)
def test_the_cap_is_650_mb_a_window_up_to_2_gb(window_count, cap):
    # The heaviest window alone, 433.5 MB (ending 2026-09-28, dry run of 2026-10-11), x 1.5.
    assert MAX_BYTES_PER_WINDOW == 650_000_000
    assert MAX_BYTES_BILLED == 2_000_000_000
    assert compute_bytes_cap(window_count) == cap


def test_the_day_of_the_last_collection_is_read_in_utc():
    # 01:00 in Paris on 10/11 is 23:00 UTC on 10/10: the window ending 10/09 was collected.
    last_collected_at = datetime(
        2026, 10, 11, 1, 0, tzinfo=timezone(timedelta(hours=2))
    )

    assert list_window_ends(last_collected_at, TODAY) == [YESTERDAY]


def test_a_last_collection_after_today_is_refused():
    with pytest.raises(ValueError, match="after today"):
        list_window_ends(datetime(2026, 10, 12, 9, 0, tzinfo=UTC), TODAY)


# The collection.


def test_fetch_runs_a_dry_run_then_the_query_capped_with_its_parameters(today):
    client = FakeClient()

    raw = GhArchiveSource(lambda: client).fetch(None)

    dry_run, executed = client.queries
    assert dry_run[1].dry_run is True
    assert dry_run[1].use_query_cache is False
    assert executed[1].dry_run is not True
    assert executed[1].maximum_bytes_billed == MAX_BYTES_PER_WINDOW
    # With the table wildcard day.20*, _TABLE_SUFFIX is YYMMDD.
    assert read_parameters(executed[1]) == {
        "first_suffix": "261008",
        "last_suffix": "261010",
        "window_ends": [YESTERDAY],
        "window_days": 3,
        "top_size": 30,
    }
    assert read_parameters(dry_run[1]) == read_parameters(executed[1])
    assert json.loads(raw)["window_ends"] == ["2026-10-10"]
    assert read_trends(raw).window_ends == [YESTERDAY]


def test_fetch_gives_the_query_and_its_result_a_timeout(today):
    client = FakeClient()

    GhArchiveSource(lambda: client).fetch(None)

    # On BigQuery's side too: a client timeout alone leaves the job running, and billed.
    [executed] = client.executed_configs()
    # The library keeps it as the REST API writes it: a string of milliseconds.
    assert int(executed.job_timeout_ms) == QUERY_TIMEOUT_SECONDS * 1000

    assert [timeout for _, _, timeout in client.queries] == [
        QUERY_TIMEOUT_SECONDS,
        QUERY_TIMEOUT_SECONDS,
    ]
    assert client.jobs[-1].result_timeouts == [QUERY_TIMEOUT_SECONDS]
    assert client.table_requests == [
        ("githubarchive.day.20261010", QUERY_TIMEOUT_SECONDS)
    ]


def test_fetch_catches_up_every_missed_window_in_one_query(today):
    client = FakeClient()

    raw = GhArchiveSource(lambda: client).fetch(datetime(2026, 10, 8, 9, 0, tzinfo=UTC))

    [executed] = client.executed_configs()
    assert executed.maximum_bytes_billed == 3 * MAX_BYTES_PER_WINDOW
    assert read_parameters(executed)["first_suffix"] == "261006"
    assert read_parameters(executed)["window_ends"] == [
        date(2026, 10, 8),
        date(2026, 10, 9),
        date(2026, 10, 10),
    ]
    assert json.loads(raw)["window_ends"] == ["2026-10-08", "2026-10-09", "2026-10-10"]


def test_fetch_drops_the_oldest_windows_while_the_bound_applies(today):
    # 15 windows (the most a catch-up takes) and 14 are both capped by the bound: dropping the
    # oldest lowers the volume, not the cap.
    client = FakeClient(
        dry_run_bytes=lambda window_count: (
            MAX_BYTES_BILLED + 1 if window_count == 15 else MAX_BYTES_BILLED
        )
    )

    raw = GhArchiveSource(lambda: client).fetch(datetime(2026, 9, 26, 9, 0, tzinfo=UTC))

    assert compute_bytes_cap(14) == compute_bytes_cap(15) == MAX_BYTES_BILLED
    assert [
        len(read_parameters(config)["window_ends"])
        for config in client.dry_run_configs()
    ] == [15, 14]
    [executed] = client.executed_configs()
    assert read_parameters(executed)["window_ends"][0] == date(2026, 9, 27)
    assert json.loads(raw)["window_ends"][-1] == "2026-10-10"


def test_fetch_drops_no_window_below_the_bound(today):
    # Below the bound, a window dropped lowers the cap by a whole window and the volume by one
    # day: dropping can never fit the cap.
    client = FakeClient(dry_run_bytes=lambda window_count: 10 * MAX_BYTES_BILLED)

    with pytest.raises(DryRunOverLimitError):
        GhArchiveSource(lambda: client).fetch(datetime(2026, 10, 8, 9, 0, tzinfo=UTC))

    assert len(client.dry_run_configs()) == 1
    assert client.executed_configs() == []


def test_fetch_refuses_to_run_a_single_window_over_the_cap(today):
    # A daily collection that would read 20 times its usual volume.
    client = FakeClient(dry_run_bytes=lambda window_count: 2_700_000_000)

    with pytest.raises(DryRunOverLimitError) as raised:
        GhArchiveSource(lambda: client).fetch(None)

    assert client.executed_configs() == []
    assert "2700000000" in str(raised.value)
    assert str(MAX_BYTES_PER_WINDOW) in str(raised.value)


def test_fetch_refuses_to_run_when_the_dry_run_gives_no_volume(today):
    client = FakeClient(dry_run_bytes=lambda window_count: None)

    with pytest.raises(DryRunVolumeUnknownError):
        GhArchiveSource(lambda: client).fetch(None)

    assert client.executed_configs() == []


def test_fetch_refuses_a_window_whose_last_day_is_not_closed(today):
    # Modified before midnight: the last hours of 10/10 may still be missing.
    client = FakeClient(table_modified=datetime(2026, 10, 10, 23, 8, tzinfo=UTC))

    with pytest.raises(DayTableNotClosedError, match="2026-10-10T23:08"):
        GhArchiveSource(lambda: client).fetch(None)

    assert client.queries == []


def test_fetch_gives_nothing_and_creates_no_client_when_no_window_is_due(today):
    def create_client():
        raise AssertionError("no client expected")

    source = GhArchiveSource(create_client)

    assert source.fetch(datetime(2026, 10, 11, 0, 30, tzinfo=UTC)) is None


def test_fetch_lets_a_client_error_through(today):
    class MissingKeyError(Exception):
        pass

    def create_client():
        raise MissingKeyError

    with pytest.raises(MissingKeyError):
        GhArchiveSource(create_client).fetch(None)


def test_fetch_refuses_a_result_it_cannot_read(today):
    client = FakeClient()

    def query(query, job_config=None, timeout=None):
        job = FakeClient.query(client, query, job_config, timeout)
        if not job_config.dry_run:
            job.rows = [{**row, "stars_gained": -1} for row in job.rows]
        return job

    client.query = query

    with pytest.raises(TrendsExtractInvalidError):
        GhArchiveSource(lambda: client).fetch(None)


def test_fetch_closes_its_client(today):
    client = FakeClient()

    GhArchiveSource(lambda: client).fetch(None)

    assert client.closed


def test_fetch_closes_its_client_when_it_fails(today):
    client = FakeClient(dry_run_bytes=lambda window_count: None)

    with pytest.raises(DryRunVolumeUnknownError):
        GhArchiveSource(lambda: client).fetch(None)

    assert client.closed


def test_the_source_writes_json_into_the_watch():
    source = GhArchiveSource(lambda: FakeClient())

    assert (source.name, source.extension, source.acquisition_dir_name) == (
        "gh_archive",
        "json",
        "automated_watch",
    )


# No login of a GitHub user (actor).


def test_the_query_leaves_out_the_events_without_a_repository():
    assert "AND repo.id IS NOT NULL" in TRENDS_QUERY
    # Else the last name seen of a repository could be NULL, and the ranking refused.
    assert "AND repo.name IS NOT NULL" in TRENDS_QUERY


def test_the_query_reads_its_sizes_from_parameters_and_the_tables_of_the_prefix():
    assert "@top_size" in TRENDS_QUERY
    assert "@window_days" in TRENDS_QUERY
    assert "30" not in TRENDS_QUERY
    assert f"`{gh_archive.TABLE_PREFIX}20*`" in TRENDS_QUERY


def test_the_query_never_reads_the_actor():
    assert "actor" not in TRENDS_QUERY.lower()


def test_the_raw_result_holds_no_actor(today):
    raw = GhArchiveSource(lambda: FakeClient()).fetch(None)

    assert b"actor" not in raw


# The reading of a raw result.


def test_the_fixture_reads_as_a_ranking_of_one_window():
    extract = read_trends(FIXTURE_PATH.read_bytes())

    assert len(extract.window_ends) == 1
    assert extract.rows
    assert {row.window_end for row in extract.rows} == set(extract.window_ends)


def make_raw(rows, window_ends=None):
    fixture = read_fixture()
    return json.dumps(
        {
            "window_ends": fixture["window_ends"]
            if window_ends is None
            else window_ends,
            "rows": rows,
        }
    ).encode()


def first_row():
    return read_fixture()["rows"][0]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("repository_name", "no-slash"),
        ("repository_name", "owner/name/extra"),
        # SQLite stops its GLOB at a NUL: the base would refuse the name.
        ("repository_name", "own\0er/name"),
        ("stars_gained", -1),
        # SQLite cannot hold an integer over 64 bits.
        ("stars_gained", 2**63),
        ("forks_gained", True),
        ("forks_gained", 2**63),
        # Seen on 2026-10-10: 14 ForkEvent without repo.id nor repo.name.
        ("repository_id", None),
        ("repository_id", 0),
        ("repository_id", 2**63),
        ("repository_id", "123"),
        ("window_end", "2026-10-10T00:00:00Z"),
    ],
)
def test_a_ranking_with_an_invalid_row_is_refused_whole(field, value):
    rows = [first_row(), {**first_row(), "repository_id": 1, field: value}]

    with pytest.raises(TrendsExtractInvalidError, match=f"row 1: {field}"):
        read_trends(make_raw(rows))


def test_a_row_with_an_unknown_field_is_refused():
    with pytest.raises(TrendsExtractInvalidError, match="row 0: 'actor'"):
        read_trends(make_raw([{**first_row(), "actor": "someone"}]))


def test_the_message_quotes_an_unknown_field_name_truncated_and_escaped():
    # A key is a value of the raw: a line break in it would forge a log line.
    key = "some-person/repository\n2026-10-13 WARNING forged line" + "x" * 500
    with pytest.raises(TrendsExtractInvalidError) as raised:
        read_trends(make_raw([{**first_row(), key: 1}]))

    message = str(raised.value)
    assert "\n" not in message
    assert "row 0: 'some-person/repository\\n2026-10-13 WARNING forged line" in message
    assert "x" * 100 not in message
    assert "(extra_forbidden)" in message


def test_the_message_names_the_refusal_of_a_control_character_not_the_name():
    rows = [{**first_row(), "repository_name": "some-person/na\x1b[31mme"}]

    with pytest.raises(TrendsExtractInvalidError) as raised:
        read_trends(make_raw(rows))

    message = str(raised.value)
    assert "some-person" not in message
    assert (
        "row 0: repository_name (string_pattern_mismatch): str of length 21" in message
    )


def test_a_row_of_a_window_not_announced_is_refused():
    with pytest.raises(TrendsExtractInvalidError, match="window 2026-01-01"):
        read_trends(make_raw([{**first_row(), "window_end": "2026-01-01"}]))


def test_more_rows_than_two_tops_in_a_window_are_refused():
    rows = [{**first_row(), "repository_id": number} for number in range(1, 62)]

    with pytest.raises(TrendsExtractInvalidError, match="61 rows, at most 60"):
        read_trends(make_raw(rows))


def test_a_repository_twice_in_a_window_is_refused():
    with pytest.raises(TrendsExtractInvalidError, match="twice"):
        read_trends(make_raw([first_row(), first_row()]))


def test_a_result_without_windows_is_refused():
    with pytest.raises(TrendsExtractInvalidError, match="window_ends"):
        read_trends(make_raw([], window_ends=[]))


@pytest.mark.parametrize(
    "window_ends",
    [["2026-10-10", "2026-10-10"], ["2026-10-10", "2026-10-09"]],
)
def test_windows_not_strictly_in_order_are_refused(window_ends):
    rows = [{**first_row(), "window_end": "2026-10-10"}]

    with pytest.raises(TrendsExtractInvalidError, match="window_ends not in strictly"):
        read_trends(make_raw(rows, window_ends=window_ends))


def test_the_message_quotes_an_invalid_window_date():
    with pytest.raises(TrendsExtractInvalidError) as raised:
        read_trends(make_raw([], window_ends=["2026-13-01"]))

    assert "window_ends.0" in str(raised.value)
    assert "'2026-13-01'" in str(raised.value)


def test_a_result_that_is_not_json_is_refused():
    with pytest.raises(TrendsExtractInvalidError):
        read_trends(b"not json")


def test_the_message_quotes_a_value_truncated_and_escaped_and_never_a_name():
    long_value = "line one\nline two" + "x" * 500
    rows = [{**first_row(), "stars_gained": long_value}]

    with pytest.raises(TrendsExtractInvalidError) as raised:
        read_trends(make_raw(rows))

    message = str(raised.value)
    assert "\n" not in message
    assert "line one\\nline two" in message
    assert "x" * 200 not in message


def test_the_message_never_quotes_a_whole_row():
    # A missing field: Pydantic gives the whole row as the value, the name with it.
    row = {**first_row(), "repository_name": "some-person/repository"}
    del row["stars_gained"]

    with pytest.raises(TrendsExtractInvalidError) as raised:
        read_trends(make_raw([row]))

    message = str(raised.value)
    assert "row 0: stars_gained (missing)" in message
    assert "some-person" not in message
    assert "dict of 4" in message


def test_the_message_never_quotes_a_truncated_document():
    raw = b'{"window_ends": ["2026-10-10"], "rows": [{"repository_name": "some-person/repo'

    with pytest.raises(TrendsExtractInvalidError) as raised:
        read_trends(raw)

    message = str(raised.value)
    assert "some-person" not in message
    assert "(json_invalid)" in message


def test_the_message_never_quotes_a_row_that_is_a_string():
    with pytest.raises(TrendsExtractInvalidError) as raised:
        read_trends(make_raw(["some-person/repository"]))

    message = str(raised.value)
    assert "some-person" not in message
    assert message.startswith("row 0 (model_type): str of length 22")


def test_the_message_gives_the_length_of_a_repository_name_not_the_name():
    rows = [{**first_row(), "repository_name": "some-person\nfake log line"}]

    with pytest.raises(TrendsExtractInvalidError) as raised:
        read_trends(make_raw(rows))

    message = str(raised.value)
    assert "some-person" not in message
    assert "length 25" in message


def test_the_message_gives_the_length_and_the_maximum_of_a_name_too_long():
    rows = [{**first_row(), "repository_name": "some-person/" + "x" * 288}]

    with pytest.raises(TrendsExtractInvalidError) as raised:
        read_trends(make_raw(rows))

    message = str(raised.value)
    assert "some-person" not in message
    assert (
        "row 0: repository_name (string_too_long): str of length 300, maximum 255"
        in message
    )


def test_the_trend_reader_of_the_source_gives_the_rows_of_a_raw_result():
    """The trend reader the kind of the source carries, which etl/transform.py reads the raw
    files with."""
    assert gh_archive.TREND_READER.source_code == "gh_archive"
    assert (
        gh_archive.TREND_READER.read_rows(FIXTURE_PATH.read_bytes())
        == read_trends(FIXTURE_PATH.read_bytes()).rows
    )
