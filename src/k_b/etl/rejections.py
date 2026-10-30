"""The one path of every rejection, from the stage that finds it to the log of the import
(conventions.md, rule 1): logged once, here, and returned to the stage, which goes on."""

import logging

from k_b.etl.raw_files import Rejection

logger = logging.getLogger(__name__)


def reject(raw_file_path: str, reason: str) -> Rejection:
    logger.warning("rejected from %s: %s", raw_file_path, reason)
    return Rejection(raw_file_path, reason)
