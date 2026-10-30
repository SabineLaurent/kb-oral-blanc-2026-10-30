"""The vocabulary the stages of the ETL share: a raw file read, a file of trends read, a
rejection."""

from datetime import UTC, datetime
from typing import NamedTuple

from k_b.items import Item, SourceCode, TrendRow

# The name etl/extract.py gives every raw file of a collection.
COLLECTION_NAME_FORMAT = "%Y-%m-%dT%H%M%SZ"


def parse_collection_name(collection_name: str) -> datetime:
    """The collection time a raw file or a folder of pages is named by, in UTC; ValueError for
    another name."""
    return datetime.strptime(collection_name, COLLECTION_NAME_FORMAT).replace(
        tzinfo=UTC
    )


class Rejection(NamedTuple):
    raw_file_path: str
    reason: str


class RawFile(NamedTuple):
    """A raw file read: a page, for a collection that is a folder of pages."""

    raw_file_path: str
    source_code: SourceCode
    collected_at: datetime
    items: list[Item]
    # Its entries rejected by its reader or refused as items: a catalog with one of them
    # withdraws nothing (#106).
    rejection_count: int


class RawReading(NamedTuple):
    files: list[RawFile]
    rejections: list[Rejection]
    # The pages opened, readable or not.
    read_count: int


def join_readings(readings: list[RawReading]) -> RawReading:
    return RawReading(
        [raw_file for reading in readings for raw_file in reading.files],
        [rejection for reading in readings for rejection in reading.rejections],
        sum(reading.read_count for reading in readings),
    )


class TrendFile(NamedTuple):
    """A raw file of trends of GH Archive read: the file to register, and its rows, which are not
    items."""

    raw_file: RawFile
    rows: list[TrendRow]


class TrendReading(NamedTuple):
    files: list[TrendFile]
    rejections: list[Rejection]
    # The files of trends opened, readable or not.
    read_count: int


def join_trend_readings(readings: list[TrendReading]) -> TrendReading:
    return TrendReading(
        [trend_file for reading in readings for trend_file in reading.files],
        [rejection for reading in readings for rejection in reading.rejections],
        sum(reading.read_count for reading in readings),
    )
