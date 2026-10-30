import importlib
import pkgutil
from itertools import permutations

from k_b.commands import collect_command
from k_b.etl import sources
from k_b.etl.source_contract import find_source_kind, list_sources
from k_b.etl.source_kinds import SOURCE_KINDS
from k_b.etl.sources import (
    clipping,
    gh_archive,
)
from k_b.etl.sources.github import api as github_api
from k_b.etl.sources.github import most_forked as github_most_forked
from k_b.etl.sources.github import most_starred as github_most_starred
from k_b.etl.sources.zotero import READER


def test_the_sources_are_collected_in_the_order_of_2026_10_06():
    assert [source.name for source in list_sources(SOURCE_KINDS)] == [
        "cisa_kev",
        "github_releases_langchain_ai_langchain",
        "github_releases_langchain_ai_langgraph",
        "github_releases_run_llama_llama_index",
        "github_releases_anthropics_claude_code",
        "github_advisories_pip",
        "github_advisories_npm",
        "github_most_starred",
        "github_most_forked",
        "arxiv_cscr_x_csai",
        "tldr_ai",
        "tldr_infosec",
        "tldr_dev",
    ]


def test_each_source_belongs_to_its_own_kind():
    for kind in SOURCE_KINDS:
        for source in kind.sources:
            assert find_source_kind(source.name, SOURCE_KINDS) is kind


def test_no_folder_prefix_covers_another():
    # By position: two equal strings may be the same object, so identity cannot tell them apart.
    prefixes = [kind.folder_prefix for kind in SOURCE_KINDS]
    for prefix, other in permutations(prefixes, 2):
        assert other != prefix
        assert not other.startswith(prefix + "_")


def test_zotero_is_declared_with_its_reader_and_without_an_instance():
    # Its instance needs the Zotero data directory: etl/collected_sources.py creates it from
    # KB_ZOTERO_DIR.
    kind = find_source_kind("zotero", SOURCE_KINDS)

    assert kind is not None
    assert (kind.folder_prefix, kind.sources, kind.reader) == ("zotero", (), READER)


def test_the_clips_are_declared_with_their_reader_and_without_an_instance():
    # Its instance needs the vault: etl/collected_sources.py creates it from KB_VAULT_DIR.
    kind = find_source_kind("clipping", SOURCE_KINDS)

    assert kind is not None
    assert (kind.folder_prefix, kind.sources, kind.reader) == (
        "clipping",
        (),
        clipping.READER,
    )


def test_gh_archive_is_declared_with_its_trend_reader_and_without_an_instance():
    # Its instance needs the BigQuery key: etl/collected_sources.py creates it from
    # GOOGLE_APPLICATION_CREDENTIALS. Its raw files are rankings, not items (ADR of 2026-10-08).
    kind = find_source_kind("gh_archive", SOURCE_KINDS)

    assert kind is not None
    assert (kind.folder_prefix, kind.sources, kind.reader, kind.trend_reader) == (
        "gh_archive",
        (),
        None,
        gh_archive.TREND_READER,
    )


def test_no_kind_has_both_a_reader_of_items_and_a_trend_reader():
    """A raw folder is read one way or the other (etl/source_contract.py)."""
    assert [
        kind.folder_prefix
        for kind in SOURCE_KINDS
        if kind.reader is not None and kind.trend_reader is not None
    ] == []


def test_the_github_core_quota_kinds_are_those_that_call_the_github_api_but_the_search():
    # Sub-packages included: the GitHub sources are in sources/github/.
    modules = [
        importlib.import_module(module_info.name)
        for module_info in pkgutil.walk_packages(sources.__path__, "k_b.etl.sources.")
        if not module_info.ispkg
    ]
    # Imported, not named: a module that only names the function in a comment is not counted.
    calling_modules = [
        module
        for module in modules
        if module is not github_api
        and getattr(module, "get_from_github_api", None)
        is github_api.get_from_github_api
    ]

    # The rankings call the search, which has its own quota, apart.
    assert {module.FOLDER_PREFIX for module in calling_modules} == (
        collect_command.GITHUB_CORE_QUOTA_FOLDER_PREFIXES
        | {github_most_starred.FOLDER_PREFIX, github_most_forked.FOLDER_PREFIX}
    )
