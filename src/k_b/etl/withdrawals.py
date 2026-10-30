"""Mark as withdrawn what a state catalog (CISA KEV, a Zotero extract) no longer lists, while its
raw file is written into the base; the SQL is in the writer of the kind."""

import sqlite3

from k_b.database.writing import (
    mark_withdrawn_vulnerabilities_not_in,
    mark_withdrawn_zotero_rows_not_in,
)
from k_b.etl.raw_files import RawFile


def mark_withdrawn_vulnerabilities(
    connection: sqlite3.Connection, raw_file: RawFile, raw_data_id: int
) -> int:
    """Mark as withdrawn the CVE a catalog no longer lists; return how many.

    Only for a file whose entries were all read: a rejected row is in the catalog, not absent
    from it."""
    present_cve_ids = [
        item.cve
        for item in raw_file.items
        if item.kind == "vulnerability" and item.cve is not None
    ]
    if not present_cve_ids or raw_file.rejection_count:
        return 0
    return mark_withdrawn_vulnerabilities_not_in(
        connection,
        raw_file.source_code,
        raw_file.collected_at,
        raw_data_id,
        present_cve_ids,
    )


def mark_withdrawn_zotero_rows(
    connection: sqlite3.Connection, raw_file: RawFile, raw_data_id: int
) -> int:
    """Mark as withdrawn the entries and highlights an extract no longer lists; return how many.
    An extract with a rejected entry, or without entry, withdraws nothing."""
    entries = [item for item in raw_file.items if item.kind == "zotero_entry"]
    if not entries or raw_file.rejection_count:
        return 0
    return mark_withdrawn_zotero_rows_not_in(
        connection, raw_file.source_code, raw_file.collected_at, raw_data_id, entries
    )
