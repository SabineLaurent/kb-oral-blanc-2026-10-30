"""The CISA KEV source: download the catalog of known exploited vulnerabilities (E), then read
each vulnerability of a raw catalog into an item (T)."""

import csv
import io
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from email.utils import format_datetime
from typing import TypeIs, get_args
from urllib.error import HTTPError

from k_b.core.quoting import quote_value
from k_b.etl.http_download import download
from k_b.etl.raw_store import AUTOMATED_WATCH_DIR_NAME
from k_b.etl.source_contract import (
    EntryReading,
    PageRefusedError,
    SourceReader,
    check_not_all_invalid,
    check_not_empty,
    read_each_entry,
)
from k_b.items import (
    CVE_ID,
    NVD_PAGE_URL,
    Item,
    RansomwareUse,
    SourceCode,
    at_midnight_utc,
)

# The name of its raw folders, written once: its instances and its kind use it.
FOLDER_PREFIX = "cisa_kev"
# The source of its items, in the source table of the base.
SOURCE_CODE: SourceCode = "cisa_kev"


# === EXTRACT ===

CATALOG_URL = (
    "https://www.cisa.gov/sites/default/files/csv/known_exploited_vulnerabilities.csv"
)
# The catalog weighs about 1 MB in 2026: ten times that is not a catalog any more.
MAX_CATALOG_BYTES = 10 * 1024 * 1024
# Redirections are followed, even to plain HTTP: only a catalog served from here is trusted.
TRUSTED_URL_PREFIX = "https://www.cisa.gov/"
HTTP_NOT_MODIFIED = 304


class CisaKevSource:
    name = FOLDER_PREFIX
    extension = "csv"
    acquisition_dir_name = AUTOMATED_WATCH_DIR_NAME

    def fetch(self, last_collected_at: datetime | None) -> bytes | None:
        """Return the whole catalog, or None if it has not changed since last_collected_at.

        The catalog is read first, so that a page that is not a catalog (maintenance, captcha)
        raises instead of being stored; a row invalid is rejected at the import."""
        headers = {}
        if last_collected_at is not None:
            headers["If-Modified-Since"] = format_datetime(
                last_collected_at, usegmt=True
            )
        try:
            raw_catalog = download(
                CATALOG_URL, TRUSTED_URL_PREFIX, MAX_CATALOG_BYTES, headers
            )
        except HTTPError as error:
            if error.code == HTTP_NOT_MODIFIED:
                error.close()
                return None
            raise
        catalog = read_catalog(raw_catalog)
        check_not_empty(catalog)
        check_not_all_invalid(catalog)
        return raw_catalog


# === TRANSFORM ===

REQUIRED_COLUMNS = (
    "cveID",
    "vendorProject",
    "product",
    "vulnerabilityName",
    "dateAdded",
    "shortDescription",
    "knownRansomwareCampaignUse",
)


@dataclass(frozen=True)
class Vulnerability:
    cve_id: str
    vendor_project: str
    product: str
    vulnerability_name: str
    date_added: date
    short_description: str
    known_ransomware_campaign_use: RansomwareUse


def read_catalog(raw_catalog: bytes) -> EntryReading[Vulnerability]:
    """The whole catalog; the base removes the duplicates between collections."""
    return read_new_vulnerabilities(raw_catalog, last_collected_at=None)


def read_new_vulnerabilities(
    raw_catalog: bytes, last_collected_at: datetime | None
) -> EntryReading[Vulnerability]:
    """One vulnerability per valid row added since the day before the last collection (None:
    all); every invalid row is rejected with its line.

    dateAdded is a day without a time zone: compared with a day of margin, not converted, so that
    no row is lost whatever its zone."""
    # utf-8-sig: a byte order mark would otherwise stick to the first column name.
    rows = csv.DictReader(io.StringIO(raw_catalog.decode("utf-8-sig"), newline=""))
    columns = rows.fieldnames or []
    missing_columns = [column for column in REQUIRED_COLUMNS if column not in columns]
    if missing_columns:
        raise PageRefusedError(
            f"catalog without the columns {', '.join(missing_columns)}"
        )
    # line_num is read once its row is: the line the row ends on.
    reading = read_each_entry(
        ((rows.line_num, row) for row in rows), parse_vulnerability
    )
    if last_collected_at is None:
        return reading
    since = last_collected_at.date() - timedelta(days=1)
    return reading._replace(
        entries=[
            vulnerability
            for vulnerability in reading.entries
            if vulnerability.date_added >= since
        ]
    )


def parse_vulnerability(row: dict[str, str], line_number: int) -> Vulnerability:
    missing_columns = [column for column in REQUIRED_COLUMNS if row[column] is None]
    if missing_columns:
        raise ValueError(
            f"line {line_number}: missing fields {', '.join(missing_columns)}"
        )
    cve_id = row["cveID"]
    if not re.fullmatch(CVE_ID, cve_id):
        raise ValueError(
            f"line {line_number}: not a CVE identifier: {quote_value(cve_id)}"
        )
    # From here on, named by its CVE identifier too: it is read.
    label = f"line {line_number} ({quote_value(cve_id)})"
    try:
        date_added = date.fromisoformat(row["dateAdded"])
    except ValueError as error:
        raise ValueError(
            f"{label}: invalid dateAdded: {quote_value(row['dateAdded'])}"
        ) from error
    ransomware_use = row["knownRansomwareCampaignUse"]
    if not is_ransomware_use(ransomware_use):
        raise ValueError(
            f"{label}: knownRansomwareCampaignUse is not one of "
            f"{get_args(RansomwareUse)}: {quote_value(ransomware_use)}"
        )
    return Vulnerability(
        cve_id=cve_id,
        vendor_project=row["vendorProject"],
        product=row["product"],
        vulnerability_name=row["vulnerabilityName"],
        date_added=date_added,
        short_description=row["shortDescription"],
        known_ransomware_campaign_use=ransomware_use,
    )


def is_ransomware_use(value: str) -> TypeIs[RansomwareUse]:
    return value in get_args(RansomwareUse)


def convert_vulnerability(
    vulnerability: Vulnerability, collected_at: datetime, raw_file_path: str
) -> Item:
    return Item(
        source_code=SOURCE_CODE,
        source_item_id=vulnerability.cve_id,
        kind="vulnerability",
        title=vulnerability.vulnerability_name,
        url=NVD_PAGE_URL + vulnerability.cve_id,
        # dateAdded is a day: stored at midnight UTC, a writing convention (docs/stockage.md).
        published_at=at_midnight_utc(vulnerability.date_added),
        summary=vulnerability.short_description,
        collected_at=collected_at,
        raw_file_path=raw_file_path,
        cve=vulnerability.cve_id,
        vendor=vulnerability.vendor_project,
        product=vulnerability.product,
        ransomware_use=vulnerability.known_ransomware_campaign_use,
    )


READER: SourceReader[Vulnerability] = SourceReader(
    SOURCE_CODE, read_catalog, convert_vulnerability
)
