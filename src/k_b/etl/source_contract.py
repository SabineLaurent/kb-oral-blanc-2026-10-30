"""What a source gives the generic steps: how to collect it, how to read its raw files."""

from collections.abc import Callable, Iterable, Sequence
from datetime import datetime
from typing import Any, NamedTuple, Protocol

from k_b.items import Item, SourceCode, TrendRow


class Source(Protocol):
    name: str
    extension: str
    # The folder of raw/ its raw files go to: the watch, or what the user selected.
    acquisition_dir_name: str

    def fetch(
        self, last_collected_at: datetime | None
    ) -> bytes | dict[str, bytes] | None:
        """Return the raw content published since last_collected_at, by page name for a source of
        several pages; None when the source says nothing changed."""
        ...


class ExpectedSourceError(Exception):
    """A failure a source meets in normal operation, whose message says all: logged without its
    traceback (etl/extract.py)."""


class PageRefusedError(ExpectedSourceError, ValueError):
    """A page refused on what it holds (conventions.md, rule 1); a ValueError still, which the
    import rejects as a page it cannot read."""


class EntryReading[EntryT](NamedTuple):
    """The entries of a raw file read, and the reason each invalid one was rejected."""

    entries: list[EntryT]
    # Each one names its entry (position or line, identifier once read) and its field.
    rejection_reasons: list[str]


def read_each_entry[RawEntryT, EntryT](
    numbered_entries: Iterable[tuple[int, RawEntryT]],
    parse: Callable[[RawEntryT, int], EntryT],
) -> EntryReading[EntryT]:
    """Parse each entry with its number; one that parse refuses (ValueError) is rejected with its
    reason, the others still read (conventions.md, rule 1). Only parse is guarded: an error while
    listing the entries (csv.Error) leaves the whole page unreadable."""
    entries = []
    rejection_reasons = []
    for number, raw_entry in numbered_entries:
        try:
            entry = parse(raw_entry, number)
        except ValueError as error:
            rejection_reasons.append(str(error))
            continue
        entries.append(entry)
    return EntryReading(entries, rejection_reasons)


def check_not_all_invalid(reading: EntryReading[Any]) -> None:
    """Refuse, at the collection, a page whose entries are all invalid (all_items_invalid), so
    that its last collection date stays; a page without entry is not refused."""
    if reading.rejection_reasons and not reading.entries:
        raise PageRefusedError(
            f"all_items_invalid: {len(reading.rejection_reasons)} entries, all rejected; "
            f"first: {reading.rejection_reasons[0]}"
        )


def check_not_empty(reading: EntryReading[Any]) -> None:
    """Refuse, at the collection, a state catalog without entry (empty_catalog): imported, it
    would supersede the last catalog, which the purge would then delete."""
    if not reading.entries and not reading.rejection_reasons:
        raise PageRefusedError("empty_catalog: 0 entries")


class SourceReader[EntryT](NamedTuple):
    """How to read a raw file of a source: its entries, then each entry as an item.

    Declared in a constant annotated with its entry type, so that mypy refuses a converter of
    another source."""

    # The source_code of its items, and of its raw files in the base, even one without item.
    source_code: SourceCode
    read_entries: Callable[[bytes], EntryReading[EntryT]]
    convert: Callable[[EntryT, datetime, str], Item]


class TrendReader(NamedTuple):
    """How to read a raw file of trends: its rows, refused whole, never a row alone."""

    # The source_code of its raw files in the base.
    source_code: SourceCode
    read_rows: Callable[[bytes], list[TrendRow]]


class SourceKind(NamedTuple):
    """A kind of source, declared once: its raw folders, its instances, its readers.

    Pass the annotated READER constant of the reader module, never a SourceReader built here, so
    that mypy refuses a converter of another source."""

    # The name of its raw folders, alone or followed by _ (github_releases_<repository>).
    folder_prefix: str
    sources: tuple[Source, ...]
    # None: its raw files are not read into items (no reader yet, none planned, or raw files
    # that are not items: the rankings of the two GitHub tops, read by no one, and the trends
    # of GH Archive, read by trend_reader).
    reader: SourceReader[Any] | None
    # How to read its raw files when they hold trends written to the trend table, not items
    # (GH Archive). A kind has one reader or the other, never both
    # (test_no_kind_has_both_a_reader_of_items_and_a_trend_reader).
    trend_reader: TrendReader | None = None


def list_sources(kinds: Sequence[SourceKind]) -> list[Source]:
    return [source for kind in kinds for source in kind.sources]


def find_source_kind(
    folder_name: str, kinds: Sequence[SourceKind]
) -> SourceKind | None:
    """The kind a raw folder belongs to, tracked or not any more (cisa_kev_old is CISA's)."""
    for kind in kinds:
        if folder_name == kind.folder_prefix or folder_name.startswith(
            kind.folder_prefix + "_"
        ):
            return kind
    return None
