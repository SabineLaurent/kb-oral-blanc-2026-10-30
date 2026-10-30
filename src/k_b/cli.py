"""The entry point of kb: it reads the command and the environment (commands/environment.py),
opens the log and hands the run to its module in commands/, the environment given as a parameter."""

import argparse
import logging
import sys
from collections.abc import Mapping
from contextlib import ExitStack
from pathlib import Path

from k_b.commands.collect_command import run_collect
from k_b.commands.create_db_command import run_create_db
from k_b.commands.digest_command import run_digest
from k_b.commands.environment import (
    read_data_dir,
    read_environment,
    read_log_to_file,
)
from k_b.commands.exit_codes import ExitCode
from k_b.commands.import_command import run_import
from k_b.commands.openapi_command import (
    AI_API_OPENAPI_PATH,
    DATA_API_OPENAPI_PATH,
    run_openapi,
)
from k_b.commands.run_log import (
    LOG_FILE_PATH,
    LOGGER_NAME,
    SERVE_AI_LOG_FILE_PATH,
    SERVE_DATA_LOG_FILE_PATH,
    logging_to,
)
from k_b.commands.serve_ai_command import run_serve_ai
from k_b.commands.serve_data_command import run_serve_data
from k_b.commands.sigterm import StoppedBySigtermError, stopping_on_sigterm
from k_b.commands.write_tops_command import run_write_tops
from k_b.core.config import AI_API_HOST, AI_API_PORT, DATA_API_HOST, DATA_API_PORT
from k_b.database.connection import DATABASE_FILE_NAME
from k_b.etl.collected_sources import COLLECT_ONLY_VARIABLE, VAULT_DIR_VARIABLE

logger = logging.getLogger(LOGGER_NAME)

# The long runs, each with its own log file: a rotation done by another command would leave it
# writing in the file of the day before (docs/specs-api.md, "Journal").
LOG_FILE_PATH_BY_COMMAND = {
    "serve-data": SERVE_DATA_LOG_FILE_PATH,
    "serve-ai": SERVE_AI_LOG_FILE_PATH,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="kb",
        epilog=describe_exit_codes(),
        # Keeps the lines of the epilog: the default formatter joins them.
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    command_parsers = parser.add_subparsers(dest="command", required=True)
    command_parsers.add_parser(
        "collect",
        help=f"run every source, or the ones of {COLLECT_ONLY_VARIABLE}, and store its raw"
        " output in raw/",
    )
    command_parsers.add_parser(
        "create-db", help=f"create the items database, {DATABASE_FILE_NAME}"
    )
    command_parsers.add_parser(
        "import",
        help="read the raw items and write them into the database, which removes the duplicates",
    )
    command_parsers.add_parser(
        "write-tops",
        help="write the sheet of each top ranking in the vault of KB_VAULT_DIR, then delete"
        " the rankings it supersedes",
    )
    command_parsers.add_parser(
        "serve-data",
        help="serve the database, read only, on"
        f" http://{DATA_API_HOST}:{DATA_API_PORT} by default",
    )
    command_parsers.add_parser(
        "serve-ai",
        help="serve the digests of the watch by the AI API, on"
        f" http://{AI_API_HOST}:{AI_API_PORT} by default",
    )
    command_parsers.add_parser(
        "digest",
        help="ask the AI API for the digests of the watch, then write the sheet of each"
        f" subject in the vault of {VAULT_DIR_VARIABLE}",
    )
    command_parsers.add_parser(
        "openapi",
        help=f"write the OpenAPI of the data API in {DATA_API_OPENAPI_PATH} and the one of"
        f" the AI API in {AI_API_OPENAPI_PATH}",
    )
    arguments = parser.parse_args(argv)

    # Neither data nor key: the document is the same on every machine.
    if arguments.command == "openapi":
        return run_openapi()
    # Read once, here: every command gets the same mapping.
    environ = read_environment()
    data_dir = read_data_dir(environ)
    log_to_file = read_log_to_file(environ)
    if data_dir is None or log_to_file is None:
        return ExitCode.NOT_STARTED
    log_file_path = None
    if log_to_file:
        log_file_path = LOG_FILE_PATH_BY_COMMAND.get(arguments.command, LOG_FILE_PATH)
    with ExitStack() as log_stack:
        try:
            log_stack.enter_context(logging_to(data_dir, log_file_path))
        except OSError as error:
            # A run without its log would lose the failures the log is kept for: none is run.
            print(
                f"error: log file not opened: {type(error).__name__}: {error}",
                file=sys.stderr,
            )
            return ExitCode.NOT_STARTED
        try:
            with stopping_on_sigterm(arguments.command):
                return run_command(arguments.command, data_dir, environ)
        except StoppedBySigtermError:
            # Caught once the import or the collection has cleaned up on its way out.
            logger.error("kb stopped by SIGTERM")
            return ExitCode.STOPPED_BY_SIGTERM
        except KeyboardInterrupt:
            # The same for SIGINT (Ctrl-C), which Python raises as KeyboardInterrupt, not an
            # Exception: uncaught, Python would print its whole traceback.
            logger.error("kb stopped by SIGINT")
            return ExitCode.STOPPED_BY_SIGINT
        except Exception:
            # Logged here, while the log file is open, and not raised again: Python would print
            # its traceback a second time. 1, as Python exits on an exception: kb was run.
            logger.exception("kb stopped by an exception it does not handle")
            return ExitCode.FAILED


def describe_exit_codes() -> str:
    lines = [f"  {code.value:>3}  {code.meaning}" for code in ExitCode]
    return "exit codes:\n" + "\n".join(lines)


def run_command(command: str, data_dir: Path, environ: Mapping[str, str]) -> int:
    if command == "create-db":
        return run_create_db(data_dir)
    if command == "import":
        return run_import(data_dir)
    if command == "write-tops":
        return run_write_tops(data_dir, environ)
    if command == "serve-data":
        return run_serve_data(data_dir, environ)
    if command == "serve-ai":
        return run_serve_ai(environ)
    if command == "digest":
        return run_digest(environ)
    return run_collect(data_dir, environ)
