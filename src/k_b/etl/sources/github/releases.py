"""The GitHub releases source: download the latest releases of a tracked repository (E), then
read each one into an item (T)."""

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import unquote

from k_b.core.quoting import quote_value
from k_b.etl.raw_store import AUTOMATED_WATCH_DIR_NAME
from k_b.etl.source_contract import (
    EntryReading,
    PageRefusedError,
    SourceReader,
    check_not_all_invalid,
    read_each_entry,
)
from k_b.etl.sources.github.api import SOURCE_CODE, get_from_github_api
from k_b.items import REPOSITORY_NAME, Item, is_repository_name, parse_aware_datetime

logger = logging.getLogger(__name__)


# The name of its raw folders, written once: its instances and its kind use it.
FOLDER_PREFIX = "github_releases"


# === EXTRACT ===

# The API maximum. Only the first page is read: see the warning in fetch.
RELEASES_PER_PAGE = 100


class GithubReleasesSource:
    extension = "json"
    acquisition_dir_name = AUTOMATED_WATCH_DIR_NAME

    def __init__(self, repository: str):
        if not is_repository_name(repository):
            raise ValueError(f"not an owner/name repository: {repository!r}")
        self.repository = repository
        self.name = f"{FOLDER_PREFIX}_" + re.sub(r"[^a-z0-9]+", "_", repository.lower())

    def fetch(self, last_collected_at: datetime | None) -> bytes | None:
        """Return the first page of releases; None if none is newer than last_collected_at."""
        raw_response = get_from_github_api(
            f"repos/{self.repository}/releases?per_page={RELEASES_PER_PAGE}"
        )
        # Read even at the first collection: a page that is not a list of releases, or whose
        # releases are all invalid, raises.
        reading = read_releases(raw_response)
        check_not_all_invalid(reading)
        # Defence in depth: the page must be of the tracked repository, whose case GitHub may
        # write differently.
        for release in reading.entries:
            if release.repository.lower() != self.repository.lower():
                raise PageRefusedError(
                    f"{self.repository}: release {release.release_id} is of the repository "
                    f"{quote_value(release.repository)}"
                )
        if last_collected_at is None:
            return raw_response
        # A page whose only new release is invalid returns nothing: its rejection reaches the
        # import log with the next page written, while it is among the RELEASES_PER_PAGE latest.
        publication_dates = [release.published_at for release in reading.entries]
        if not any(
            published_at > last_collected_at for published_at in publication_dates
        ):
            return None
        release_count = len(reading.entries) + len(reading.rejection_reasons)
        is_page_full = release_count >= RELEASES_PER_PAGE
        oldest = min(publication_dates)
        if is_page_full and oldest > last_collected_at:
            logger.warning(
                "%s: %d releases read, page size %d; oldest published at %s, after the last "
                "collection at %s; next pages not read",
                self.repository,
                release_count,
                RELEASES_PER_PAGE,
                oldest.isoformat(),
                last_collected_at.isoformat(),
            )
        return raw_response


# === TRANSFORM ===

# The repository is read in the release URL: the raw file can be read without its source.
# The whole URL is checked, so that the link opens the repository of the title: the tag part
# takes only the path characters of RFC 3986 and /, so no query, fragment, space or non-ASCII.
RELEASE_URL = re.compile(
    rf"https://github\.com/({REPOSITORY_NAME})/releases/tag/"
    r"(?:[A-Za-z0-9._~!$&'()*+,;=:@/-]|%[0-9A-Fa-f]{2})+"
)


@dataclass(frozen=True)
class Release:
    """A release, without its author's login: not needed for the watch (GDPR minimisation)."""

    repository: str
    release_id: int
    tag_name: str
    name: str | None
    url: str
    published_at: datetime
    is_prerelease: bool
    body: str | None


def read_releases(raw_response: bytes) -> EntryReading[Release]:
    """One release per valid entry of a `GET /repos/{owner}/{repo}/releases` response, newest
    first; an invalid one is rejected with its position."""
    entries = json.loads(raw_response)
    if not isinstance(entries, list):
        raise PageRefusedError("expected a list of releases")
    return read_each_entry(enumerate(entries), parse_release)


def parse_release(entry: object, position: int) -> Release:
    if not isinstance(entry, dict):
        raise ValueError(f"release {position}: not an object")
    release_id = entry.get("id")
    # bool is a subclass of int: refused explicitly.
    if not isinstance(release_id, int) or isinstance(release_id, bool):
        raise ValueError(f"release {position}: id is not an integer")
    # Named by its id from here on: it is read.
    label = f"release {position} (id {quote_value(release_id)})"
    tag_name = entry.get("tag_name")
    if not isinstance(tag_name, str):
        raise ValueError(f"{label}: tag_name is not a string")
    url = entry.get("html_url")
    url_match = RELEASE_URL.fullmatch(url) if isinstance(url, str) else None
    if (
        url_match is None
        or not is_repository_name(url_match.group(1))
        or has_dot_segment(url_match.string)
    ):
        raise ValueError(
            f"{label}: html_url is not a release URL of an owner/name "
            f"repository: {quote_value(url)}"
        )
    is_prerelease = entry.get("prerelease")
    if not isinstance(is_prerelease, bool):
        raise ValueError(f"{label}: prerelease is not a boolean")
    name = entry.get("name")
    body = entry.get("body")
    if not isinstance(name, str | None) or not isinstance(body, str | None):
        raise ValueError(f"{label}: name or body is not a string")
    return Release(
        repository=url_match.group(1),
        release_id=release_id,
        tag_name=tag_name,
        name=name,
        url=url_match.string,
        published_at=parse_aware_datetime(
            entry.get("published_at"), f"{label}: published_at"
        ),
        is_prerelease=is_prerelease,
        body=body,
    )


def has_dot_segment(url: str) -> bool:
    """A `.` or `..` path segment, even percent-encoded: a browser would resolve it."""
    return any(segment in (".", "..") for segment in unquote(url).split("/"))


def convert_release(
    release: Release, collected_at: datetime, raw_file_path: str
) -> Item:
    return Item(
        source_code=SOURCE_CODE,
        source_item_id=str(release.release_id),
        kind="release",
        title=f"{release.repository} {release.name or release.tag_name}",
        url=release.url,
        published_at=release.published_at,
        summary=release.body,
        collected_at=collected_at,
        raw_file_path=raw_file_path,
        repository_name=release.repository,
        tag_name=release.tag_name,
        release_name=release.name,
    )


READER: SourceReader[Release] = SourceReader(
    SOURCE_CODE, read_releases, convert_release
)
