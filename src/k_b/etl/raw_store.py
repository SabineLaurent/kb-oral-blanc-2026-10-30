"""The raw output of each collection, under raw/, and the date of the last collection per source."""

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath

from k_b.core.atomic_write import write_file_atomically
from k_b.core.quoting import quote_value
from k_b.items import parse_aware_datetime

# The folder of the data directory that holds the raw output of each collection.
RAW_DIR_NAME = "raw"
# The folder of raw/ that holds what the watch collects, one folder per source.
AUTOMATED_WATCH_DIR_NAME = "automated_watch"
# The folder of raw/ that holds what the user selected (ADR of 2026-10-09).
SELECTED_LIBRARY_DIR_NAME = "selected_library"
# The only folders of raw/ a source may write to: a misspelt one would hold raw files that
# no reader looks for.
RAW_DIR_NAMES = (AUTOMATED_WATCH_DIR_NAME, SELECTED_LIBRARY_DIR_NAME)
COLLECTION_STATE_FILENAME = "collection_state.json"


def find_stream(raw_file_path: str) -> str:
    """The stream of a raw file: the name of its source folder (automated_watch/<stream>/...)."""
    parts = PurePosixPath(raw_file_path).parts
    if len(parts) < 3:
        raise ValueError(f"raw file path without a source folder: {raw_file_path}")
    return parts[1]


def make_source_folder_key(acquisition_dir_name: str, source_name: str) -> str:
    return f"{RAW_DIR_NAME}/{acquisition_dir_name}/{source_name}"


@dataclass(frozen=True)
class RawStore:
    """The raw files of a data directory, each named by a POSIX key relative to it, never by a
    Path (.claude/rules/poc.md, rule 3)."""

    data_dir: Path

    def delete(self, key: str) -> bool:
        """Delete the file of key; False when it is already gone."""
        try:
            self.find_path(key).unlink()
        except FileNotFoundError:
            return False
        return True

    def has_file(self, key: str) -> bool:
        return self.find_path(key).is_file()

    def read_latest(self, folder_key: str) -> bytes | None:
        """The last raw file of a folder in collection order; None when it has none."""
        keys = self.list_file_keys(folder_key)
        if not keys:
            return None
        return self.read(keys[-1])

    def list_file_keys(self, folder_key: str) -> list[str]:
        """The keys of the files of a folder, in collection order, hidden files and subfolders
        left out."""
        folder = self.find_path(folder_key)
        if not folder.is_dir():
            return []
        return sorted(
            f"{folder_key}/{path.name}"
            for path in folder.iterdir()
            if path.is_file() and not path.name.startswith(".")
        )

    def read(self, key: str) -> bytes:
        return self.find_path(key).read_bytes()

    def find_path(self, key: str) -> Path:
        """The path of key in the data directory; a key empty, absolute, going up (..) or holding
        a backslash is refused, as the keys of the purge come from the base."""
        key_path = PurePosixPath(key)
        if (
            not key_path.parts
            or key_path.is_absolute()
            or ".." in key_path.parts
            or "\\" in key
        ):
            raise ValueError(
                f"not a key relative to the data directory: {quote_value(key)}"
            )
        return self.data_dir.joinpath(*key_path.parts)


class InvalidCollectionStateError(ValueError):
    """The collection state file cannot be trusted: collecting from it could skip data."""


def write_new_raw_file(path: Path, content: bytes) -> None:
    """Write a raw file once: raw/ is immutable, a raw file is never overwritten."""
    if path.exists():
        raise FileExistsError(f"raw file already exists: {path}")
    write_file_atomically(path, content)


def load_last_collection_dates(data_dir: Path) -> dict[str, datetime]:
    """The date of the last collection of each source; empty when the state file is absent.

    A state half read would make the next collection start from a date nobody wrote:
    InvalidCollectionStateError instead."""
    state_path = data_dir / COLLECTION_STATE_FILENAME
    if not state_path.exists():
        return {}
    try:
        state_content = state_path.read_bytes()
    except OSError as error:
        raise InvalidCollectionStateError(
            f"{state_path} cannot be read: {error.strerror}"
        ) from error
    try:
        stored_dates = json.loads(state_content.decode("utf-8"))
        if not isinstance(stored_dates, dict):
            raise ValueError("expected an object mapping each source name to a date")
        return {
            source_name: parse_aware_datetime(
                stored_date, f"date of {quote_value(source_name)}"
            )
            for source_name, stored_date in stored_dates.items()
        }
    except ValueError as error:
        raise InvalidCollectionStateError(
            f"{state_path} is invalid ({error}); fix it, or delete it to collect every source "
            "from scratch"
        ) from error


def save_last_collection_dates(
    data_dir: Path, last_collection_dates: dict[str, datetime]
) -> None:
    """Write the date of the last collection of each source, whole and atomically."""
    stored_dates = {
        source_name: collected_at.isoformat()
        for source_name, collected_at in last_collection_dates.items()
    }
    state_content = json.dumps(stored_dates, indent=2, sort_keys=True) + "\n"
    write_file_atomically(
        data_dir / COLLECTION_STATE_FILENAME, state_content.encode("utf-8")
    )
