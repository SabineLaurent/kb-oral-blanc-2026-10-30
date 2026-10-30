"""kb write-tops: write the sheets of the top rankings in the vault, then delete the rankings
they supersede from raw/; it runs after kb import."""

import logging
from collections.abc import Mapping
from pathlib import Path
from time import monotonic

from k_b.commands.batch_summary import describe_count, describe_duration
from k_b.commands.exit_codes import ExitCode, find_exit_code
from k_b.commands.run_log import LOGGER_NAME, describe_os_error
from k_b.etl.collected_sources import VAULT_DIR_VARIABLE, read_vault_dir
from k_b.etl.raw_store import RawStore
from k_b.etl.source_kinds import TOPS
from k_b.post.top_sheets import SHEETS_VAULT_PATH, TopSheetsOutcome, write_top_sheets
from k_b.post.vault_files import VaultEntryRefusedError

logger = logging.getLogger(LOGGER_NAME)


def run_write_tops(data_dir: Path, environ: Mapping[str, str]) -> int:
    """Each top in turn, a top that fails not stopping the other; without a vault, nothing is
    read nor deleted, and nothing created, as write_file_atomically would create a vault
    misspelt."""
    vault_dir = read_vault_dir(environ)
    if vault_dir is None:
        logger.error(
            "%s not set; the sheets of the tops are written in the vault",
            VAULT_DIR_VARIABLE,
        )
        return ExitCode.NOT_STARTED
    if not vault_dir.is_dir():
        logger.error(
            "%s is not an existing directory; no folder is created", VAULT_DIR_VARIABLE
        )
        return ExitCode.NOT_STARTED
    # Every top is written: none is selected (wording of Sabine of 2026-10-29).
    logger.info("write-tops started: %s expected", describe_count(len(TOPS), "top"))
    started = monotonic()
    store = RawStore(data_dir)
    has_failed = False
    has_done = False
    # The line of each top, printed once all are done; no count of the rankings deleted once a
    # top stopped, as its error may have come amid its deletions, its last step.
    lines: list[str] = []
    deleted_count: int | None = 0
    for top in TOPS:
        try:
            outcome = write_top_sheets(store, vault_dir, top)
        except (OSError, VaultEntryRefusedError) as error:
            logger.error("top sheets failed: %s: %s", top.name, describe_error(error))
            # The type alone, never the message, which names the vault or a raw file.
            lines.append(f"{top.name}: failed ({type(error).__name__})")
            has_failed = True
            deleted_count = None
            continue
        lines.append(f"{top.name}: {describe_outcome(outcome)}")
        has_failed = has_failed or outcome.unreadable_count > 0
        has_done = (
            has_done or outcome.written_count > 0 or outcome.unreadable_count == 0
        )
        if deleted_count is not None:
            deleted_count += outcome.deleted_count
    logger.info("write-tops finished in %s", describe_duration(monotonic() - started))
    # A blank line before and after the result, apart from the log and from what follows (make's
    # line of an error): decisions of Sabine of 2026-10-29.
    print()
    for line in lines:
        print(line)
    if deleted_count is not None:
        print(f"Older raw files deleted: {deleted_count}")
    print(f"Sheets vault path: {SHEETS_VAULT_PATH.as_posix()}/")
    print()
    return find_exit_code(has_failed=has_failed, has_done=has_done)


def describe_error(error: OSError | VaultEntryRefusedError) -> str:
    """A refused entry of the vault by its state and its path in the vault; an OSError by its
    type and reason, never a path, which names the vault or a raw file."""
    if isinstance(error, VaultEntryRefusedError):
        return str(error)
    return f"{describe_os_error(error)}; run kb write-tops again"


def describe_outcome(outcome: TopSheetsOutcome) -> str:
    """A ranking kept unread first, then the sheets written, then the sheet of the day already
    there."""
    if outcome.unreadable_count > 0:
        return "failed (ranking_unreadable)"
    if outcome.written_count > 0:
        return f"ok, {describe_count(outcome.written_count, 'sheet')} written"
    if outcome.skipped_count > 0:
        return "ok, 0 sheets written, the sheet of the day already exists"
    return "no ranking"
