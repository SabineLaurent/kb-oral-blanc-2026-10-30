"""SOURCE_KINDS: the only declaration of the sources, in collection order, imported by the
commands only; Zotero, clipping and GH Archive get their instance in etl/collected_sources.py."""

from k_b.core.config import (
    GITHUB_TRACKED_REPOSITORIES,
    TLDR_TRACKED_EDITIONS,
    TrackedRepository,
)
from k_b.etl.raw_store import AUTOMATED_WATCH_DIR_NAME, make_source_folder_key
from k_b.etl.source_contract import SourceKind
from k_b.etl.sources import (
    arxiv,
    cisa_kev,
    clipping,
    gh_archive,
    tldr,
    zotero,
)
from k_b.etl.sources.arxiv import ArxivSource
from k_b.etl.sources.cisa_kev import CisaKevSource
from k_b.etl.sources.github import advisories as github_advisories
from k_b.etl.sources.github import most_forked as github_most_forked
from k_b.etl.sources.github import most_starred as github_most_starred
from k_b.etl.sources.github import releases as github_releases
from k_b.etl.sources.github.advisories import GithubAdvisoriesSource
from k_b.etl.sources.github.most_forked import GithubMostForkedSource
from k_b.etl.sources.github.most_starred import GithubMostStarredSource
from k_b.etl.sources.github.releases import GithubReleasesSource
from k_b.etl.sources.tldr import TldrSource
from k_b.etl.tops import Top


def list_packages_by_ecosystem(
    repositories: tuple[TrackedRepository, ...],
) -> dict[str, tuple[str, ...]]:
    packages_by_ecosystem: dict[str, tuple[str, ...]] = {}
    for repository in repositories:
        known_packages = packages_by_ecosystem.get(repository.ecosystem, ())
        packages_by_ecosystem[repository.ecosystem] = (
            known_packages + repository.packages
        )
    return packages_by_ecosystem


SOURCE_KINDS = (
    SourceKind(cisa_kev.FOLDER_PREFIX, (CisaKevSource(),), cisa_kev.READER),
    SourceKind(
        github_releases.FOLDER_PREFIX,
        tuple(
            GithubReleasesSource(repository.name)
            for repository in GITHUB_TRACKED_REPOSITORIES
        ),
        github_releases.READER,
    ),
    SourceKind(
        github_advisories.FOLDER_PREFIX,
        tuple(
            GithubAdvisoriesSource(ecosystem, packages)
            for ecosystem, packages in list_packages_by_ecosystem(
                GITHUB_TRACKED_REPOSITORIES
            ).items()
        ),
        github_advisories.READER,
    ),
    SourceKind(
        github_most_starred.FOLDER_PREFIX,
        (GithubMostStarredSource(),),
        # No reader: the ranking does not enter the base (decision "Tops", 2026-10-07).
        None,
    ),
    SourceKind(
        github_most_forked.FOLDER_PREFIX,
        (GithubMostForkedSource(),),
        # No reader: the ranking does not enter the base (decision "Tops", 2026-10-07).
        None,
    ),
    SourceKind(arxiv.FOLDER_PREFIX, (ArxivSource(),), arxiv.READER),
    SourceKind(
        tldr.FOLDER_PREFIX,
        tuple(TldrSource(edition) for edition in TLDR_TRACKED_EDITIONS),
        tldr.READER,
    ),
    # No instance: etl/collected_sources.py adds ZoteroSource when KB_ZOTERO_DIR is set, unless
    # KB_COLLECT_ONLY leaves it out. Its extracts are under raw/selected_library/.
    SourceKind(zotero.FOLDER_PREFIX, (), zotero.READER),
    # No instance: etl/collected_sources.py adds ClippingSource when KB_VAULT_DIR is set, unless
    # KB_COLLECT_ONLY leaves it out. Its copies are under raw/selected_library/.
    SourceKind(clipping.FOLDER_PREFIX, (), clipping.READER),
    # No instance: etl/collected_sources.py adds GhArchiveSource when
    # GOOGLE_APPLICATION_CREDENTIALS is set, unless KB_COLLECT_ONLY leaves it out. No reader of
    # items: its raw files hold trends, not items; etl/transform.py reads them with its
    # trend reader.
    SourceKind(gh_archive.FOLDER_PREFIX, (), None, gh_archive.TREND_READER),
)

# The sheets of the top rankings, in the order of SOURCE_KINDS: kb write-tops writes them, and
# kb top (#266) reads the same rankings. Each name is the folder of its sheets under veille/tops/.
TOPS = (
    Top(
        "most_starred",
        make_source_folder_key(
            AUTOMATED_WATCH_DIR_NAME, github_most_starred.FOLDER_PREFIX
        ),
        github_most_starred.read_top_entries,
        "Étoiles",
        "dépôts GitHub les plus étoilés",
    ),
    Top(
        "most_forked",
        make_source_folder_key(
            AUTOMATED_WATCH_DIR_NAME, github_most_forked.FOLDER_PREFIX
        ),
        github_most_forked.read_top_entries,
        "Forks",
        "dépôts GitHub les plus forkés",
    ),
)
