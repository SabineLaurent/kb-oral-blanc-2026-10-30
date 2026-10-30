"""The sheets of the top rankings in the vault, one per top and per day of collection, then the
rankings they supersede deleted from raw/.

A sheet is written once, never purged nor rewritten, with the limit of post/vault_files.py; its
path is made of constants and the day of its ranking, never of a value read from GitHub."""

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import NamedTuple

from k_b.core.config import TOPS_DIR_NAME, WATCH_DIR_NAME
from k_b.core.dates import format_date
from k_b.core.quoting import quote_value
from k_b.etl.raw_files import COLLECTION_NAME_FORMAT, parse_collection_name
from k_b.etl.raw_store import RawStore
from k_b.etl.tops import Top, TopEntry
from k_b.post.vault_files import (
    MARKDOWN_SYNTAX_CHARACTERS,
    VaultEntryRefusedError,
    has_sheet,
    neutralize_text,
    refuse_linked_folders,
    write_new_sheet,
)

logger = logging.getLogger(__name__)

# The folder of the sheets of the tops, in the vault: one folder per top under it.
SHEETS_VAULT_PATH = PurePosixPath(WATCH_DIR_NAME, TOPS_DIR_NAME)


@dataclass(frozen=True)
class TopSheetsOutcome:
    written_count: int
    # The rankings not written: the sheet of their day exists, written before or by an earlier
    # ranking of the same day.
    skipped_count: int
    deleted_count: int
    unreadable_count: int


class Ranking(NamedTuple):
    collected_at: datetime
    entries: list[TopEntry]


def write_top_sheets(store: RawStore, vault_dir: Path, top: Top) -> TopSheetsOutcome:
    """Write the sheet of each day of collection of top that has none, then delete the rankings
    read before the last one read.

    The folder is listed once: a ranking collected meanwhile is neither read nor deleted. An
    unreadable ranking is kept; an exception (OSError, SIGTERM) raised by a read or a write, a
    symbolic link, or an entry replaced meanwhile, stops the top before any deletion; the
    deletions already made when one fails stay."""
    sheets_dir = find_sheets_dir(vault_dir, top)
    keys = store.list_file_keys(top.folder_key)
    refuse_linked_folders(vault_dir, sheets_dir)
    refuse_linked_sheets(vault_dir, sheets_dir, keys)
    readable_keys = []
    read_times = []
    written_count = 0
    for key in keys:
        ranking = read_ranking(store, top, key)
        if ranking is None:
            continue
        readable_keys.append(key)
        read_times.append(ranking.collected_at)
        written_count += write_sheet(vault_dir, sheets_dir, top, ranking)
    refuse_linked_folders(vault_dir, sheets_dir)
    refuse_missing_sheets(vault_dir, sheets_dir, read_times)
    deleted_count = sum(store.delete(key) for key in readable_keys[:-1])
    logger.info("%s: %d superseded rankings deleted", top.name, deleted_count)
    return TopSheetsOutcome(
        written_count=written_count,
        skipped_count=len(readable_keys) - written_count,
        deleted_count=deleted_count,
        unreadable_count=len(keys) - len(readable_keys),
    )


def read_ranking(store: RawStore, top: Top, key: str) -> Ranking | None:
    """The ranking of key, or None after logging why it is kept unread, without naming its file."""
    collected_at = find_collection_time(key)
    if collected_at is None:
        logger.error(
            "%s: ranking_name_invalid, a file of its raw folder kept", top.name
        )
        return None
    try:
        entries = top.read_entries(store.read(key))
    except (ValueError, RecursionError) as error:
        # The messages of the readers give a position or a measure, never a value (#264).
        logger.error(
            "%s ranking of %s: ranking_unreadable, kept: %s %s",
            top.name,
            collected_at.date(),
            type(error).__name__,
            quote_value(str(error)),
        )
        return None
    return Ranking(collected_at, entries)


def find_collection_time(key: str) -> datetime | None:
    """The time of the collection that named the file of key, None for another name; strptime
    alone would take 2026-10-5, which breaks the order of the names."""
    collection_name = PurePosixPath(key).stem
    try:
        collected_at = parse_collection_name(collection_name)
    except ValueError:
        return None
    if collected_at.strftime(COLLECTION_NAME_FORMAT) != collection_name:
        return None
    return collected_at


def find_sheets_dir(vault_dir: Path, top: Top) -> Path:
    return vault_dir / SHEETS_VAULT_PATH / top.name


def find_sheet_path(sheets_dir: Path, collected_at: datetime) -> Path:
    return sheets_dir / f"{collected_at.date().isoformat()}.md"


def refuse_linked_sheets(vault_dir: Path, sheets_dir: Path, keys: list[str]) -> None:
    """Refuse the sheet of a ranking of keys that is a link or not a regular file, its day read
    from the name of its ranking."""
    for key in keys:
        collected_at = find_collection_time(key)
        # Only its refusals count here: a sheet not written yet is expected.
        if collected_at is not None:
            has_sheet(find_sheet_path(sheets_dir, collected_at), vault_dir)


def refuse_missing_sheets(
    vault_dir: Path, sheets_dir: Path, read_times: list[datetime]
) -> None:
    """Refuse, before the deletion, a sheet of a ranking read that is missing or not a regular
    file: its ranking would be deleted without a sheet."""
    for collected_at in read_times:
        sheet_path = find_sheet_path(sheets_dir, collected_at)
        if not has_sheet(sheet_path, vault_dir):
            raise VaultEntryRefusedError(
                f"sheet_missing: {sheet_path.relative_to(vault_dir).as_posix()} is not there"
            )


def write_sheet(vault_dir: Path, sheets_dir: Path, top: Top, ranking: Ranking) -> bool:
    """Write the sheet of the day of ranking, unless it exists; return whether it was written."""
    day = ranking.collected_at.date()
    sheet_path = find_sheet_path(sheets_dir, ranking.collected_at)
    if not write_new_sheet(
        vault_dir, sheets_dir, sheet_path, render_sheet(top, ranking)
    ):
        logger.info("%s sheet of %s: sheet_exists, not rewritten", top.name, day)
        return False
    logger.info("%s sheet of %s: sheet_written", top.name, day)
    return True


def render_sheet(top: Top, ranking: Ranking) -> str:
    """The Markdown of a sheet: a front matter, then the repositories by rank."""
    day = ranking.collected_at.date().isoformat()
    lines = [
        "---",
        f"ranking: {top.name}",
        f"collected_at: {format_date(ranking.collected_at)}",
        f"repositories: {len(ranking.entries)}",
        "---",
        "",
        f"# Top {len(ranking.entries)} des {top.subject}, relevé du {day}",
        "",
        f"| Rang | Dépôt | {top.count_heading} | Description |",
        "|---|---|---|---|",
    ]
    lines += [
        f"| {entry.rank} | [{entry.full_name}]({entry.url}) | {entry.count} |"
        f" {neutralize_description(entry.description)} |"
        for entry in ranking.entries
    ]
    return "\n".join(lines) + "\n"


def neutralize_description(description: str | None) -> str:
    """The description on one line, as text, in its cell, | escaped; a bare URL is left as it is."""
    if description is None:
        return ""
    return neutralize_text(description, MARKDOWN_SYNTAX_CHARACTERS)
