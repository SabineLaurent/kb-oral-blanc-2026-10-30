"""The GitHub security advisories source: download the advisories of the tracked packages of one
ecosystem (E), then read each one into an item (T)."""

import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

from k_b.core.config import GITHUB_ECOSYSTEMS
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
from k_b.items import CVE_ID, GHSA_ID, Item, parse_aware_datetime

logger = logging.getLogger(__name__)


# The name of its raw folders, written once: its instances and its kind use it.
FOLDER_PREFIX = "github_advisories"


# === EXTRACT ===

# The API maximum. Only the first page is read: see the warning in fetch.
ADVISORIES_PER_PAGE = 100


class GithubAdvisoriesSource:
    extension = "json"
    acquisition_dir_name = AUTOMATED_WATCH_DIR_NAME

    def __init__(self, ecosystem: str, packages: tuple[str, ...]):
        if ecosystem not in GITHUB_ECOSYSTEMS:
            raise ValueError(f"not a tracked ecosystem: {ecosystem!r}")
        self.ecosystem = ecosystem
        self.packages = packages
        self.name = f"{FOLDER_PREFIX}_{ecosystem}"

    def fetch(self, last_collected_at: datetime | None) -> bytes | None:
        """Return the advisories of the packages; None if none was reviewed since
        last_collected_at.

        Collections follow the review date, as GitHub may review an advisory days after
        publishing it; the packages are filtered in the request, as the whole ecosystem would
        exceed a page. The API has no filter on the review date: the request filters and sorts on
        the update date, never before the review date, then the review dates are compared."""
        query = {
            "type": "reviewed",
            "ecosystem": self.ecosystem,
            "affects": ",".join(self.packages),
            "sort": "updated",
            "per_page": ADVISORIES_PER_PAGE,
        }
        if last_collected_at is not None:
            # The filter is checked to the day only: a day of margin, then the exact comparison.
            since = last_collected_at.astimezone(UTC).date() - timedelta(days=1)
            query["updated"] = f">={since.isoformat()}"
        raw_response = get_from_github_api(f"advisories?{urlencode(query)}")
        # A page whose advisories are all invalid raises.
        reading = read_advisories(raw_response)
        check_not_all_invalid(reading)
        review_dates = [advisory.reviewed_at for advisory in reading.entries]
        if not review_dates:
            return None
        if last_collected_at is None:
            return raw_response
        # A rejected advisory leaves the page as the window moves on: its page is returned now,
        # so that its rejection reaches the import log.
        if not reading.rejection_reasons and not any(
            reviewed_at > last_collected_at for reviewed_at in review_dates
        ):
            return None
        advisory_count = len(reading.entries) + len(reading.rejection_reasons)
        is_page_full = advisory_count >= ADVISORIES_PER_PAGE
        # The page is sorted on the update date: older updates are on the next pages.
        oldest = min(advisory.updated_at for advisory in reading.entries)
        if is_page_full and oldest > last_collected_at:
            logger.warning(
                "%s: %d advisories read, page size %d; oldest updated at %s, after the last "
                "collection at %s; next pages not read",
                self.name,
                advisory_count,
                ADVISORIES_PER_PAGE,
                oldest.isoformat(),
                last_collected_at.isoformat(),
            )
        return raw_response


# === TRANSFORM ===

ADVISORY_URL_PREFIX = "https://github.com/advisories/"
SEVERITIES = ("low", "medium", "high", "critical", "unknown")


@dataclass(frozen=True)
class Advisory:
    """An advisory, without the people it credits: not needed for the watch (GDPR minimisation)."""

    ghsa_id: str
    cve_id: str | None
    summary: str
    severity: str
    url: str
    published_at: datetime
    # GitHub may review an advisory days after publishing it: collections follow this date.
    reviewed_at: datetime
    # Never before the review date: collections filter and sort on it.
    updated_at: datetime
    packages: tuple[str, ...]


def read_advisories(raw_response: bytes) -> EntryReading[Advisory]:
    """One advisory per valid entry of a `GET /advisories` response, in its order; an invalid one
    is rejected with its position."""
    entries = json.loads(raw_response)
    if not isinstance(entries, list):
        raise PageRefusedError("expected a list of advisories")
    return read_each_entry(enumerate(entries), parse_advisory)


def parse_advisory(entry: object, position: int) -> Advisory:
    if not isinstance(entry, dict):
        raise ValueError(f"advisory {position}: not an object")
    ghsa_id = entry.get("ghsa_id")
    if not isinstance(ghsa_id, str) or not re.fullmatch(GHSA_ID, ghsa_id):
        raise ValueError(
            f"advisory {position}: not a GHSA identifier: {quote_value(ghsa_id)}"
        )
    # From here on, named by its GHSA identifier too: it is read.
    label = f"advisory {position} ({quote_value(ghsa_id)})"
    cve_id = entry.get("cve_id")
    if cve_id is not None and not (
        isinstance(cve_id, str) and re.fullmatch(CVE_ID, cve_id)
    ):
        raise ValueError(f"{label}: not a CVE identifier: {quote_value(cve_id)}")
    summary = entry.get("summary")
    if not isinstance(summary, str):
        raise ValueError(f"{label}: summary is not a string")
    severity = entry.get("severity")
    if severity not in SEVERITIES:
        raise ValueError(f"{label}: unknown severity: {quote_value(severity)}")
    url = entry.get("html_url")
    if not isinstance(url, str) or not url.startswith(ADVISORY_URL_PREFIX):
        raise ValueError(f"{label}: html_url is not a {ADVISORY_URL_PREFIX} URL")
    return Advisory(
        ghsa_id=ghsa_id,
        cve_id=cve_id,
        summary=summary,
        severity=severity,
        url=url,
        published_at=parse_aware_datetime(
            entry.get("published_at"), f"{label}: published_at"
        ),
        reviewed_at=parse_aware_datetime(
            entry.get("github_reviewed_at"), f"{label}: github_reviewed_at"
        ),
        updated_at=parse_aware_datetime(
            entry.get("updated_at"), f"{label}: updated_at"
        ),
        packages=parse_package_names(entry.get("vulnerabilities"), label),
    )


def parse_package_names(vulnerabilities: object, label: str) -> tuple[str, ...]:
    """The affected packages, each once: an advisory lists one package per vulnerable range."""
    if not isinstance(vulnerabilities, list):
        raise ValueError(f"{label}: vulnerabilities is not a list")
    package_names: list[str] = []
    for vulnerability in vulnerabilities:
        package = (
            vulnerability.get("package") if isinstance(vulnerability, dict) else None
        )
        name = package.get("name") if isinstance(package, dict) else None
        if not isinstance(name, str):
            raise ValueError(f"{label}: a vulnerability has no package name")
        if name not in package_names:
            package_names.append(name)
    return tuple(package_names)


def convert_advisory(
    advisory: Advisory, collected_at: datetime, raw_file_path: str
) -> Item:
    return Item(
        source_code=SOURCE_CODE,
        source_item_id=advisory.ghsa_id,
        kind="advisory",
        title=advisory.summary,
        url=advisory.url,
        published_at=advisory.published_at,
        collected_at=collected_at,
        raw_file_path=raw_file_path,
        cve=advisory.cve_id,
        package_names=advisory.packages,
    )


READER: SourceReader[Advisory] = SourceReader(
    SOURCE_CODE, read_advisories, convert_advisory
)
