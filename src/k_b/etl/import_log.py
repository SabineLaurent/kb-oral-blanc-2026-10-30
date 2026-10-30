"""The log of an import: one JSON line per entry rejected, per item left out and per window of
trends left out."""

import json
from datetime import date, datetime
from pathlib import Path
from typing import NamedTuple

from k_b.core.atomic_write import write_file_atomically
from k_b.core.dates import format_date
from k_b.database.writing import TREND_DISCARD_RULE, Discard, DiscardRule
from k_b.etl.raw_files import RawFile, Rejection

# The date each rule compared the item with, named by where that date comes from.
COMPARED_FIELD_BY_RULE: dict[DiscardRule, str] = {
    # The collection of the version kept.
    "already_seen": "compared_with_collected_at",
    # The collection of the version kept, the only one compared with the content.
    "unchanged": "compared_with_collected_at",
    # The collection of the latest catalog of the source already imported; for a clip, the
    # collection of the version kept.
    "no_newer_version": "compared_with_collected_at",
    # The arXiv revision of the version kept.
    "revision_not_newer": "compared_with_revised_at",
}


class TrendDiscard(NamedTuple):
    """A window of trends left out: another raw file brought it first."""

    raw_file: RawFile
    window_end: date
    # The collection of the file of trends kept, in the format of the base.
    compared_with: str


def write_import_log(
    log_path: Path,
    imported_at: datetime,
    rejections: list[Rejection],
    discards: list[Discard],
    trend_discards: list[TrendDiscard],
) -> None:
    """One JSON line per rejection, then per item left out, then per window of trends left out,
    each dated by its import; no repository is named, its name holds its owner's login."""
    logged_at = format_date(imported_at)
    lines = (
        [
            {
                "imported_at": logged_at,
                "kind": "rejection",
                "raw_file_path": rejection.raw_file_path,
                "reason": rejection.reason,
            }
            for rejection in rejections
        ]
        + [
            {
                "imported_at": logged_at,
                "kind": "discard",
                "rule": discard.rule,
                "source_code": discard.item.source_code,
                "source_item_id": discard.item.source_item_id,
                "raw_file_path": discard.item.raw_file_path,
                COMPARED_FIELD_BY_RULE[discard.rule]: discard.compared_with,
            }
            for discard in discards
        ]
        + [
            {
                "imported_at": logged_at,
                "kind": "discard",
                "rule": TREND_DISCARD_RULE,
                "source_code": discard.raw_file.source_code,
                "source_item_id": discard.window_end.isoformat(),
                "raw_file_path": discard.raw_file.raw_file_path,
                COMPARED_FIELD_BY_RULE[TREND_DISCARD_RULE]: discard.compared_with,
            }
            for discard in trend_discards
        ]
    )
    write_file_atomically(
        log_path,
        "".join(json.dumps(line, ensure_ascii=False) + "\n" for line in lines).encode(),
    )
