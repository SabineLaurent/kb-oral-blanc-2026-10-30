"""The most forked GitHub repositories source: download the ranking (E), and read it; a ranking
gives no item, it does not enter the base.

Its checks are copied from the most starred, not shared, until #269 rewrites both."""

import json
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlencode

from k_b.core.config import GITHUB_MOST_FORKED_SEARCH_QUERY, GITHUB_RANKING_SIZE
from k_b.etl.raw_store import AUTOMATED_WATCH_DIR_NAME
from k_b.etl.source_contract import PageRefusedError
from k_b.etl.sources.github.api import get_from_github_api
from k_b.etl.sources.github.most_starred import REPOSITORY_URL_PREFIX
from k_b.etl.tops import TopEntry
from k_b.items import is_repository_name

# The name of its raw folders, written once: its instance and its kind use it.
FOLDER_PREFIX = "github_most_forked"


# === EXTRACT ===


class GithubMostForkedSource:
    name = FOLDER_PREFIX
    extension = "json"
    acquisition_dir_name = AUTOMATED_WATCH_DIR_NAME

    def fetch(self, last_collected_at: datetime | None) -> bytes | None:
        """Return a snapshot of the ranking at every collection: fork counts always change."""
        query = {
            "q": GITHUB_MOST_FORKED_SEARCH_QUERY,
            "sort": "forks",
            "order": "desc",
            "per_page": GITHUB_RANKING_SIZE,
        }
        raw_response = get_from_github_api(f"search/repositories?{urlencode(query)}")
        read_ranking(raw_response)
        return raw_response


# === TRANSFORM ===


@dataclass(frozen=True)
class ForkedRepository:
    rank: int
    # owner/name: the owner's login is kept, as it identifies the repository (see the register).
    full_name: str
    description: str | None
    forks: int


def read_most_forked(raw_response: bytes, expected_size: int) -> list[ForkedRepository]:
    """One repository per item of a `GET /search/repositories` response, by rank; a ranking
    shorter than asked for, or marked incomplete, is refused."""
    response = json.loads(raw_response)
    items = response.get("items") if isinstance(response, dict) else None
    if not isinstance(items, list):
        raise PageRefusedError("expected a search response with a list of items")
    if response.get("incomplete_results") is not False:
        raise PageRefusedError("incomplete search results, refused")
    if len(items) != expected_size:
        raise PageRefusedError(
            f"{len(items)} repositories, expected {expected_size}, refused"
        )
    return [parse_repository(item, rank) for rank, item in enumerate(items, start=1)]


def parse_repository(item: object, rank: int) -> ForkedRepository:
    if not isinstance(item, dict):
        raise PageRefusedError(f"repository {rank}: not an object")
    full_name = item.get("full_name")
    if not isinstance(full_name, str):
        raise PageRefusedError(f"repository {rank}: full_name is not a string")
    # Its length only: the name carries the owner's login.
    if not is_repository_name(full_name):
        raise PageRefusedError(
            f"repository {rank}: not an owner/name, length {len(full_name)}"
        )
    # Checked, not kept: the URL of a repository is built from its name.
    url = item.get("html_url")
    if not isinstance(url, str) or not url.startswith(REPOSITORY_URL_PREFIX):
        raise PageRefusedError(
            f"repository {rank}: html_url is not a {REPOSITORY_URL_PREFIX} URL"
        )
    description = item.get("description")
    if not isinstance(description, str | None):
        raise PageRefusedError(f"repository {rank}: description is not a string")
    forks = item.get("forks_count")
    # bool is a subclass of int in Python: refuse it explicitly.
    if not isinstance(forks, int) or isinstance(forks, bool):
        raise PageRefusedError(f"repository {rank}: forks_count is not an integer")
    return ForkedRepository(
        rank=rank, full_name=full_name, description=description, forks=forks
    )


def read_ranking(raw_response: bytes) -> list[ForkedRepository]:
    return read_most_forked(raw_response, expected_size=GITHUB_RANKING_SIZE)


def read_top_entries(
    raw_response: bytes, expected_size: int = GITHUB_RANKING_SIZE
) -> list[TopEntry]:
    """The ranking as its sheet shows it, its URL built from the name."""
    return [
        TopEntry(
            rank=repository.rank,
            full_name=repository.full_name,
            url=f"{REPOSITORY_URL_PREFIX}{repository.full_name}",
            description=repository.description,
            count=repository.forks,
        )
        for repository in read_most_forked(raw_response, expected_size)
    ]
