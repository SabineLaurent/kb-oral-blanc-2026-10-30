"""kb collect: run the sources chosen and store their raw output in raw/."""

import logging
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic

from k_b.commands.batch_summary import describe_count, describe_duration
from k_b.commands.exit_codes import ExitCode, find_exit_code
from k_b.commands.run_log import LOGGER_NAME, value_or_log_refusal
from k_b.etl.collected_sources import (
    COLLECT_ONLY_VARIABLE,
    VARIABLE_BY_SOURCE_NAME,
    CollectOnly,
    list_collected_sources,
    read_collect_only_or_refusal,
)
from k_b.etl.pipeline import CollectionOutcome, collect
from k_b.etl.raw_store import InvalidCollectionStateError, RawStore
from k_b.etl.source_contract import Source, find_source_kind
from k_b.etl.source_kinds import SOURCE_KINDS
from k_b.etl.sources.github import advisories as github_advisories
from k_b.etl.sources.github import releases as github_releases
from k_b.etl.sources.github.api import TOKEN_VARIABLE

logger = logging.getLogger(LOGGER_NAME)

# The kinds whose sources call the GitHub REST API on its primary quota, the one KB_GITHUB_TOKEN
# raises from 60 to 5,000 requests per hour. Not github_most_starred nor github_most_forked: they
# call the search, which has its own quota, apart. Not gh_archive, which reads BigQuery.
GITHUB_CORE_QUOTA_FOLDER_PREFIXES = frozenset(
    {github_releases.FOLDER_PREFIX, github_advisories.FOLDER_PREFIX}
)


def run_collect(data_dir: Path, environ: Mapping[str, str]) -> int:
    # The check and the sources read the same mapping, given by cli.py; the BigQuery client reads
    # GOOGLE_APPLICATION_CREDENTIALS from the environment itself, as the GitHub API client reads its
    # token (sources/github/api.py, for now).
    collect_only = value_or_log_refusal(
        read_collect_only_or_refusal(environ, SOURCE_KINDS)
    )
    if collect_only is None:
        return ExitCode.NOT_STARTED
    sources = list_collected_sources(
        collect_only, environ, SOURCE_KINDS, RawStore(data_dir)
    )
    log_collection_start(sources, environ)
    started = monotonic()
    try:
        outcome = collect(sources, data_dir, datetime.now(UTC))
    except InvalidCollectionStateError as error:
        logger.error("%s", error)
        return ExitCode.NOT_STARTED
    logger.info("collection finished in %s", describe_duration(monotonic() - started))
    # A blank line before and after the result, apart from the log and from what follows (make's
    # line of an error): decisions of Sabine of 2026-10-29.
    print()
    for source_name, source_error in outcome.errors_by_source.items():
        print(f"{source_name}: {describe_outcome(source_error)}")
    print_skipped_sources(collect_only, environ)
    if outcome.state_error is not None:
        print(f"collection_state: {describe_outcome(outcome.state_error)}")
    print()
    return find_collection_exit_code(outcome)


def log_collection_start(sources: Sequence[Source], environ: Mapping[str, str]) -> None:
    """Log, before the first source, the sources run and, when one uses the GitHub API without
    KB_GITHUB_TOKEN, its quota; of the token, only whether it is set is read."""
    logger.info(
        "collection started: %s selected", describe_count(len(sources), "source")
    )
    if not environ.get(TOKEN_VARIABLE) and any(
        is_github_core_quota_source(source) for source in sources
    ):
        # GitHub Docs, "Rate limits for the REST API", read on 2026-10-19.
        logger.warning(
            "%s not set: the GitHub API allows 60 requests per hour instead of 5,000",
            TOKEN_VARIABLE,
        )


def is_github_core_quota_source(source: Source) -> bool:
    kind = find_source_kind(source.name, SOURCE_KINDS)
    return kind is not None and kind.folder_prefix in GITHUB_CORE_QUOTA_FOLDER_PREFIXES


def print_skipped_sources(
    collect_only: CollectOnly, environ: Mapping[str, str]
) -> None:
    """One line per source not run: not chosen, or its variable not set."""
    for kind in SOURCE_KINDS:
        if not collect_only.is_collected(kind.folder_prefix):
            print(f"{kind.folder_prefix}: skipped (not in {COLLECT_ONLY_VARIABLE})")
    for source_name, variable in VARIABLE_BY_SOURCE_NAME.items():
        if collect_only.is_collected(source_name) and not environ.get(variable):
            print(f"{source_name}: skipped ({variable} not set)")


def find_collection_exit_code(outcome: CollectionOutcome) -> ExitCode:
    """Partial when some sources failed and the others are kept; a state not written is a
    failure, as the next collection starts from the same dates."""
    errors = outcome.errors_by_source.values()
    if outcome.state_error is not None:
        return ExitCode.FAILED
    return find_exit_code(
        has_failed=any(error is not None for error in errors),
        has_done=any(error is None for error in errors),
    )


def describe_outcome(error: Exception | None) -> str:
    return "ok" if error is None else f"failed ({type(error).__name__})"
