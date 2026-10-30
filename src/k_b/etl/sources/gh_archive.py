"""The GH Archive source: the stars and forks each GitHub repository gains in each window of 3
complete UTC days, counted in one BigQuery query (E), and their reader (T).

The query runs in a sandbox without billing (docs/bigquery.md): a dry run first, then the query
capped by maximum_bytes_billed."""

import json
import logging
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, date, datetime, time, timedelta
from typing import Annotated, Any

from google.cloud import bigquery
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from k_b.core.config import (
    GH_ARCHIVE_MAX_CATCH_UP_WINDOWS,
    GH_ARCHIVE_TOP_SIZE,
    MILLISECONDS_PER_SECOND,
)
from k_b.core.quoting import quote_value
from k_b.etl.raw_store import AUTOMATED_WATCH_DIR_NAME
from k_b.etl.source_contract import ExpectedSourceError, PageRefusedError, TrendReader
from k_b.items import SourceCode, TrendRow

logger = logging.getLogger(__name__)

SOURCE_CODE: SourceCode = "gh_archive"
# The name of its raw folders, written once: its instances and its kind use it.
FOLDER_PREFIX = "gh_archive"


# === EXTRACT ===

WINDOW_DAYS = 3
# The cap grows with the windows, up to a bound (decision of 2026-10-11): the heaviest window
# alone read 433.5 MB, x 1.5; 15 windows read 1.58 GB (dry runs of 2026-10-11, measures and
# reckoning in docs/bigquery.md).
MAX_BYTES_PER_WINDOW = 650_000_000
MAX_BYTES_BILLED = 2_000_000_000
QUERY_TIMEOUT_SECONDS = 300

TABLE_PREFIX = "githubarchive.day."
# With the wildcard day.20*, _TABLE_SUFFIX is what follows "20": YYMMDD (261008 for 2026-10-08).
TABLE_SUFFIX_FORMAT = "%y%m%d"

# Reads 4 columns of the events: never actor, payload or org. An event without repo.id or
# repo.name is left out: on 2026-10-10, 14 ForkEvent had neither (seen on 2026-10-11). A
# WatchEvent is a star given (GH Archive does not record a star removed): events are counted,
# not people. Ties are broken by repository_id, and the last name seen by the name, so that the
# same tables give the same result. The table is written in the query (a table cannot be a
# parameter): a test keeps it equal to TABLE_PREFIX.
TRENDS_QUERY = """
WITH events AS (
  SELECT repo.id AS repository_id, repo.name AS repository_name, type, created_at,
         PARSE_DATE('%Y%m%d', CONCAT('20', _TABLE_SUFFIX)) AS event_day
  FROM `githubarchive.day.20*`
  WHERE _TABLE_SUFFIX BETWEEN @first_suffix AND @last_suffix
    AND type IN ('WatchEvent', 'ForkEvent')
    AND repo.id IS NOT NULL
    AND repo.name IS NOT NULL
),
counts AS (
  SELECT window_end, repository_id,
    ARRAY_AGG(repository_name ORDER BY created_at DESC, repository_name DESC LIMIT 1)[OFFSET(0)]
      AS repository_name,
    COUNTIF(type = 'WatchEvent') AS stars_gained,
    COUNTIF(type = 'ForkEvent') AS forks_gained
  FROM UNNEST(@window_ends) AS window_end
  JOIN events
    ON event_day BETWEEN DATE_SUB(window_end, INTERVAL @window_days - 1 DAY) AND window_end
  GROUP BY window_end, repository_id
),
ranked AS (
  SELECT *,
    ROW_NUMBER() OVER (PARTITION BY window_end ORDER BY stars_gained DESC, repository_id)
      AS star_rank,
    ROW_NUMBER() OVER (PARTITION BY window_end ORDER BY forks_gained DESC, repository_id)
      AS fork_rank
  FROM counts
)
SELECT window_end, repository_id, repository_name, stars_gained, forks_gained
FROM ranked
WHERE star_rank <= @top_size OR fork_rank <= @top_size
ORDER BY window_end, stars_gained DESC, repository_id
"""


class DayTableNotClosedError(ExpectedSourceError):
    """The table of the last day of a window was last modified before that day ended."""


class DryRunOverLimitError(ExpectedSourceError):
    """The dry run of a single window announces more bytes than its cap."""


class DryRunVolumeUnknownError(ExpectedSourceError):
    """The dry run gives no volume: the query is not run (fail closed)."""


class GhArchiveSource:
    name = FOLDER_PREFIX
    extension = "json"
    acquisition_dir_name = AUTOMATED_WATCH_DIR_NAME

    def __init__(self, create_client: Callable[[], bigquery.Client]) -> None:
        # Created at the collection: a missing or invalid key fails this source alone.
        self.create_client = create_client

    def fetch(self, last_collected_at: datetime | None) -> bytes | None:
        """Return the trends of every window due since last_collected_at; None if none is due.

        The day is read when the source runs, not when the collection started: a run past
        midnight UTC collects the next window twice, never loses one."""
        window_ends = list_window_ends(last_collected_at, current_day())
        if not window_ends:
            return None
        with self.create_client() as client:
            check_day_table_closed(client, window_ends[-1])
            window_ends = drop_windows_over_the_cap(client, window_ends)
            job = client.query(
                TRENDS_QUERY,
                job_config=configure_query(window_ends, dry_run=False),
                timeout=QUERY_TIMEOUT_SECONDS,
            )
            rows = job.result(timeout=QUERY_TIMEOUT_SECONDS)
            raw_result = encode_result(window_ends, (dict(row.items()) for row in rows))
        extract = read_trends(raw_result)
        logger.info(
            "gh_archive: %d window(s) from %s to %s, %s bytes processed, %d rows",
            len(window_ends),
            window_ends[0],
            window_ends[-1],
            job.total_bytes_processed,
            len(extract.rows),
        )
        return raw_result


def create_bigquery_client() -> bigquery.Client:
    return bigquery.Client()


def compute_bytes_cap(window_count: int) -> int:
    return min(window_count * MAX_BYTES_PER_WINDOW, MAX_BYTES_BILLED)


def current_day() -> date:
    return datetime.now(UTC).date()


def list_window_ends(last_collected_at: datetime | None, today: date) -> list[date]:
    """The last day of each window due, oldest first, the most recent ending yesterday."""
    yesterday = today - timedelta(days=1)
    if last_collected_at is None:
        return [yesterday]
    first_end = last_collected_at.astimezone(UTC).date()
    if first_end > today:
        raise ValueError(f"last collection day {first_end} is after today {today}")
    due_count = (yesterday - first_end).days + 1
    if due_count > GH_ARCHIVE_MAX_CATCH_UP_WINDOWS:
        logger.info(
            "gh_archive: %d windows due since %s, the %d most recent are collected",
            due_count,
            first_end,
            GH_ARCHIVE_MAX_CATCH_UP_WINDOWS,
        )
        due_count = GH_ARCHIVE_MAX_CATCH_UP_WINDOWS
    return [yesterday - timedelta(days=offset) for offset in reversed(range(due_count))]


def check_day_table_closed(client: bigquery.Client, day: date) -> None:
    """Refuse a day whose table was last modified before the day ended: an hour may still be
    missing (the last one arrived around 00:08 UTC the next day, seen on 2026-10-11)."""
    table = client.get_table(
        f"{TABLE_PREFIX}{day:%Y%m%d}", timeout=QUERY_TIMEOUT_SECONDS
    )
    day_end = datetime.combine(day + timedelta(days=1), time(), tzinfo=UTC)
    if table.modified is None or table.modified < day_end:
        raise DayTableNotClosedError(
            f"table of {day} last modified at {describe_time(table.modified)},"
            f" before {day_end.isoformat()}"
        )


def describe_time(moment: datetime | None) -> str:
    return "an unknown time" if moment is None else moment.isoformat()


def drop_windows_over_the_cap(
    client: bigquery.Client, window_ends: list[date]
) -> list[date]:
    """Drop the oldest windows while the bound caps them, until the dry run fits; else refuse.

    Under the bound, the cap grows by a whole window and the volume by one day of events: a
    window dropped lowers the cap more than the volume, so dropping cannot fit; refusing at the
    bound without dropping would fail every collection of a long catch-up."""
    while True:
        dry_run = client.query(
            TRENDS_QUERY,
            job_config=configure_query(window_ends, dry_run=True),
            timeout=QUERY_TIMEOUT_SECONDS,
        )
        bytes_processed = dry_run.total_bytes_processed
        if bytes_processed is None:
            raise DryRunVolumeUnknownError(
                f"dry run of {len(window_ends)} window(s): total_bytes_processed is None"
            )
        cap = compute_bytes_cap(len(window_ends))
        if bytes_processed <= cap:
            return window_ends
        if cap < MAX_BYTES_BILLED or len(window_ends) == 1:
            raise DryRunOverLimitError(
                f"dry run of {len(window_ends)} window(s) ending {window_ends[0]} to"
                f" {window_ends[-1]}: {bytes_processed} bytes, maximum {cap}"
            )
        logger.info(
            "gh_archive: dry run of %d windows: %d bytes, maximum %d; window ending %s dropped",
            len(window_ends),
            bytes_processed,
            cap,
            window_ends[0],
        )
        window_ends = window_ends[1:]


def configure_query(window_ends: list[date], dry_run: bool) -> bigquery.QueryJobConfig:
    first_day = window_ends[0] - timedelta(days=WINDOW_DAYS - 1)
    parameters = [
        bigquery.ScalarQueryParameter(
            "first_suffix", "STRING", f"{first_day:{TABLE_SUFFIX_FORMAT}}"
        ),
        bigquery.ScalarQueryParameter(
            "last_suffix", "STRING", f"{window_ends[-1]:{TABLE_SUFFIX_FORMAT}}"
        ),
        bigquery.ArrayQueryParameter("window_ends", "DATE", window_ends),
        bigquery.ScalarQueryParameter("window_days", "INT64", WINDOW_DAYS),
        bigquery.ScalarQueryParameter("top_size", "INT64", GH_ARCHIVE_TOP_SIZE),
    ]
    if dry_run:
        return bigquery.QueryJobConfig(
            dry_run=True, use_query_cache=False, query_parameters=parameters
        )
    return bigquery.QueryJobConfig(
        maximum_bytes_billed=compute_bytes_cap(len(window_ends)),
        # BigQuery stops the job itself: a client timeout alone leaves it running, and billed.
        job_timeout_ms=QUERY_TIMEOUT_SECONDS * MILLISECONDS_PER_SECOND,
        query_parameters=parameters,
    )


def encode_result(window_ends: list[date], rows: Iterable[Mapping[str, Any]]) -> bytes:
    result = {
        "window_ends": [window_end.isoformat() for window_end in window_ends],
        "rows": [
            {
                key: value.isoformat() if isinstance(value, date) else value
                for key, value in row.items()
            }
            for row in rows
        ],
    }
    return (json.dumps(result, ensure_ascii=False, indent=2) + "\n").encode()


# === TRANSFORM ===

MAX_ROWS_PER_WINDOW = 2 * GH_ARCHIVE_TOP_SIZE
# The only fields whose value is quoted: a date or a number. Any other value (a repository name,
# that holds the owner's login when the owner is a person, a whole row, the raw document) is
# described by its type and its length.
FIELDS_QUOTED = frozenset(
    {"window_ends", "window_end", "repository_id", "stars_gained", "forks_gained"}
)


class TrendsExtractInvalidError(PageRefusedError):
    """The raw result does not read as trends: it is refused whole."""


class TrendsExtract(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    window_ends: Annotated[list[date], Field(min_length=1)]
    rows: list[TrendRow]


# The names of the fields a raw result may hold: any other key is a value of the raw.
KNOWN_FIELDS = frozenset(TrendsExtract.model_fields) | frozenset(TrendRow.model_fields)


def read_trends(raw_result: bytes) -> TrendsExtract:
    """Read a raw result: every row valid, of an announced window, at most two tops per window."""
    try:
        extract = TrendsExtract.model_validate_json(raw_result)
    except ValidationError as error:
        raise TrendsExtractInvalidError(describe_validation_error(error)) from None
    if any(
        later <= earlier
        for earlier, later in zip(
            extract.window_ends, extract.window_ends[1:], strict=False
        )
    ):
        raise TrendsExtractInvalidError(
            f"window_ends not in strictly increasing order: {len(extract.window_ends)} dates"
        )
    announced = set(extract.window_ends)
    for position, row in enumerate(extract.rows):
        if row.window_end not in announced:
            raise TrendsExtractInvalidError(
                f"row {position}: window {row.window_end} not in window_ends"
            )
    keys = Counter((row.window_end, row.repository_id) for row in extract.rows)
    for (window_end, repository_id), count in keys.items():
        if count > 1:
            raise TrendsExtractInvalidError(
                f"window {window_end}: repository_id {repository_id} twice"
            )
    rows_per_window = Counter(row.window_end for row in extract.rows)
    for window_end, count in rows_per_window.items():
        if count > MAX_ROWS_PER_WINDOW:
            raise TrendsExtractInvalidError(
                f"window {window_end}: {count} rows, at most {MAX_ROWS_PER_WINDOW}"
            )
    return extract


def describe_validation_error(error: ValidationError) -> str:
    """The first error: its place, its type, and its value, quoted only for a date or a number."""
    first = error.errors()[0]
    location = first["loc"]
    if location[:1] == ("rows",) and len(location) >= 2:
        field = ".".join(describe_location_part(part) for part in location[2:])
        place = f"row {location[1]}: {field}" if field else f"row {location[1]}"
    else:
        place = ".".join(describe_location_part(part) for part in location) or "result"
    # The field is the last name of the place: a list index (window_ends.0) is not one.
    names = [part for part in location if isinstance(part, str)]
    described = describe_value(first.get("input"), names[-1] if names else None)
    maximum = first.get("ctx", {}).get("max_length")
    if maximum is not None:
        described = f"{described}, maximum {maximum}"
    return f"{place} ({first['type']}): {described}; {error.error_count()} error(s)"


def describe_location_part(part: int | str) -> str:
    """A position or a known field as it is; an unknown key, which the raw gives, quoted like a
    value."""
    if isinstance(part, str) and part not in KNOWN_FIELDS:
        return quote_value(part)
    return str(part)


def describe_value(value: object, field: object) -> str:
    # A missing field gives the whole row as its value, even for a field quoted otherwise.
    if isinstance(value, dict | list):
        return f"{type(value).__name__} of {len(value)}"
    if field in FIELDS_QUOTED:
        return quote_value(value)
    if isinstance(value, str | bytes):
        return f"{type(value).__name__} of length {len(value)}"
    return type(value).__name__


def read_trend_rows(raw_result: bytes) -> list[TrendRow]:
    return read_trends(raw_result).rows


TREND_READER = TrendReader(SOURCE_CODE, read_trend_rows)
