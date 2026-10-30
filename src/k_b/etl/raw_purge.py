"""Delete the raw files of a state catalog superseded by a newer one imported: of each folder,
only the last raw file imported is kept."""

import logging
from collections.abc import Collection
from pathlib import PurePosixPath

from k_b.etl.raw_store import RAW_DIR_NAME, RawStore

logger = logging.getLogger(__name__)


def delete_superseded_raw_files(
    store: RawStore, imported_paths: frozenset[str], folder_keys: Collection[str]
) -> int:
    """Of each folder, delete the raw files imported before the last one imported; return how
    many.

    The files come from the registry of the base, never from the folder: a raw file not imported
    is never deleted, and a folder whose last imported file is gone keeps the others, the only
    raw of its state. The first error stops the purge."""
    deleted_count = 0
    for folder_key in folder_keys:
        imported_keys = sorted(
            key
            for key in (f"{RAW_DIR_NAME}/{path}" for path in imported_paths)
            # Directly in the folder: the pages of a collection are in a subfolder.
            if PurePosixPath(key).parent == PurePosixPath(folder_key)
        )
        # Collection names are times: the last in name order is the last collected.
        if not imported_keys or not store.has_file(imported_keys[-1]):
            continue
        deleted_count += sum(store.delete(key) for key in imported_keys[:-1])
    logger.info("raw purge: %d superseded files deleted", deleted_count)
    return deleted_count
