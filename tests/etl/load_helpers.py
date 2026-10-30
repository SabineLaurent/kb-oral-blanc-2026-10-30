"""What the tests of the load share: a data directory with a base, the import run, the base and the import log read."""

import csv
import io
import json
from datetime import UTC, datetime, timedelta

from test_transform import FIXTURES_DIR

from k_b.core.config import GITHUB_TRACKED_REPOSITORIES
from k_b.database.connection import connect_database
from k_b.database.creation import create_database
from k_b.etl import transform as read_raw_items_module
from k_b.etl.load import (
    import_items,
    read_imported_paths,
)
from k_b.etl.normalize import normalize_item
from k_b.etl.source_kinds import SOURCE_KINDS
from k_b.etl.transform import read_raw_files

FIRST_PAPER_ID = "2609.31282"


FIRST_CVE_ID = "CVE-2026-86950"


# The CVE of the recorded catalog, in its order; the last one is the one withdrawn.
CVE_IDS = (FIRST_CVE_ID, "CVE-2026-88772", "CVE-2026-88771", "CVE-2020-29583")


WITHDRAWN_CVE_ID = "CVE-2020-29583"


# A CVE the recorded advisories cite, absent from the recorded catalog.
ADVISORY_CVE_ID = "CVE-2026-48775"


FIRST_RELEASE_ID = 401174143


AUTOMATED_WATCH = "automated_watch"


CISA_KEV_DIR = "cisa_kev"


RELEASES_DIR = "github_releases_anthropics_claude_code"


ADVISORIES_DIR = "github_advisories_pip"


ARXIV_DIR = "arxiv_cscr_x_csai"


FIRST_COLLECTION = "2026-10-05T093000Z"


FIRST_IMPORT_AT = datetime(2026, 10, 12, 15, 49, 3, tzinfo=UTC)


PAPER_QUERY = (
    "SELECT item.id, item.kind, raw_file_path, title, authors, submitted_at, revised_at"
    " FROM publication JOIN item ON item.id = publication.item_id"
    " JOIN raw_data ON raw_data.id = item.raw_data_id WHERE arxiv_id = ?"
)


def log_path_at(data_dir, imported_at):
    return data_dir / f"import-log-{imported_at:%Y-%m-%dT%H%M%SZ}.jsonl"


def list_logs(data_dir):
    return sorted(data_dir.glob("import-log-*.jsonl"))


def run_import(
    data_dir,
    tracked_repositories=GITHUB_TRACKED_REPOSITORIES,
    kinds=SOURCE_KINDS,
    read_again=False,
):
    """kb import, one second after the one before, so that each writes its own log; read_again
    reads every raw file, as a base without registry."""
    imported_at = FIRST_IMPORT_AT + timedelta(seconds=len(list_logs(data_dir)))
    database_path = data_dir / "k-b.sqlite"
    imported_paths = frozenset() if read_again else read_imported_paths(database_path)
    readings = read_raw_files(
        data_dir / "raw", kinds, imported_paths, tracked_repositories
    )
    return import_items(
        readings.items,
        readings.trends,
        database_path,
        log_path_at(data_dir, imported_at),
        imported_at,
        tracked_repositories,
    )


def dump_database(data_dir):
    """Every table, row by row, as SQL text: two equal dumps are two equal bases."""
    connection = connect_database(data_dir / "k-b.sqlite")
    try:
        return list(connection.iterdump())
    finally:
        connection.close()


def fetch_rows(data_dir, query, parameters=()):
    connection = connect_database(data_dir / "k-b.sqlite")
    try:
        return connection.execute(query, parameters).fetchall()
    finally:
        connection.close()


def count_rows(data_dir, query):
    return fetch_rows(data_dir, query)[0][0]


def read_log_lines(data_dir):
    """The lines of the log of the latest import."""
    return [
        json.loads(line) for line in list_logs(data_dir)[-1].read_text().splitlines()
    ]


def read_log(data_dir):
    """The lines of the latest log, without their time, which a test of its own checks."""
    lines = read_log_lines(data_dir)
    for line in lines:
        del line["imported_at"]
    return lines


def read_discards(data_dir, source_item_id=None):
    return [
        line
        for line in read_log(data_dir)
        if line["kind"] == "discard"
        and source_item_id in (None, line["source_item_id"])
    ]


def write_collection(data_dir, folder, collection, content):
    """A raw file of a later collection, written as its source would."""
    extension = {
        CISA_KEV_DIR: "csv",
        RELEASES_DIR: "json",
        ADVISORIES_DIR: "json",
        ARXIV_DIR: "xml",
    }[folder]
    (data_dir / "raw" / AUTOMATED_WATCH / folder).joinpath(
        f"{collection}.{extension}"
    ).write_text(content)
    return f"{AUTOMATED_WATCH}/{folder}/{collection}.{extension}"


def kev_catalog(ransomware_use_of_first_cve="Unknown", without=(), renamed=None):
    """The recorded catalog, knownRansomwareCampaignUse of its first CVE as given, the CVE of
    renamed renamed, those of without left out."""
    rows = list(
        csv.DictReader(io.StringIO((FIXTURES_DIR / "cisa_kev_extract.csv").read_text()))
    )
    columns = list(rows[0])
    rows[0]["knownRansomwareCampaignUse"] = ransomware_use_of_first_cve
    for row in rows:
        row["cveID"] = (renamed or {}).get(row["cveID"], row["cveID"])
    rows = [row for row in rows if row["cveID"] not in without]
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=columns, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()


KEV_QUERY = (
    "SELECT vulnerability.item_id, ransomware_use, revised_at, raw_file_path"
    " FROM vulnerability JOIN item ON item.id = vulnerability.item_id"
    " JOIN raw_data ON raw_data.id = item.raw_data_id WHERE cve_id = ?"
)


def refuse_in_the_base(monkeypatch, source_item_id, changes):
    """The item of source_item_id reaches its writer with changes made after its validation: the
    base is the safety net behind the item (conventions.md, rule 4)."""

    def normalize_then_change(item):
        normalized_item = normalize_item(item)
        if normalized_item.source_item_id != source_item_id:
            return normalized_item
        return normalized_item.model_copy(update=changes)

    monkeypatch.setattr(read_raw_items_module, "normalize_item", normalize_then_change)


RELEASES_PATH = f"{AUTOMATED_WATCH}/{RELEASES_DIR}/{FIRST_COLLECTION}.json"


CISA_KEV_PATH = f"{AUTOMATED_WATCH}/{CISA_KEV_DIR}/{FIRST_COLLECTION}.csv"


# CVE-2026-86950 in Arabic-Indic digits: Python's \d accepts them, the GLOB of the base does not.
CVE_ID_IN_ARABIC_INDIC_DIGITS = "CVE-2026-٨٦٩٥٠"


def registered_paths(data_dir):
    return {
        path for (path,) in fetch_rows(data_dir, "SELECT raw_file_path FROM raw_data")
    }


def rebuild_database(data_dir):
    """Delete the base, create it, and import every raw file: kb create-db, kb import."""
    for suffix in ("", "-wal", "-shm"):
        (data_dir / f"k-b.sqlite{suffix}").unlink(missing_ok=True)
    create_database(data_dir / "k-b.sqlite")
    run_import(data_dir)
