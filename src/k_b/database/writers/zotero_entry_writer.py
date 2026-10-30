"""Write a Zotero entry, its highlights and the publication it cites: a state catalog
(catalog.py), at the level of the entry and of the highlight."""

import json
import sqlite3
from collections.abc import Sequence
from datetime import datetime

from k_b.core.dates import format_date
from k_b.database.writers import catalog, shared_rows
from k_b.database.writers.discard import Discard
from k_b.items import Item

# B1 of 2026-10-13: the paper a Zotero entry is, created with its arXiv reference alone when
# arXiv has not given it, as a CVE an advisory cites (advisory_writer.py).
INSERT_CITED_PUBLICATION = """
INSERT INTO publication (arxiv_id) VALUES (?) ON CONFLICT (arxiv_id) DO NOTHING
"""

# State catalog (Zotero), as UPSERT_VULNERABILITY (vulnerability_writer.py): a known entry takes
# the version of an extract collected after the latest one imported, if its content differs or
# if it was withdrawn; revised_at is then the date of that collection, and withdrawn_at is
# emptied. An entry only an extract older than the latest one brings is absent from the latest
# one: :withdrawn_at is then the collection of the latest one.
UPSERT_ZOTERO_ENTRY = """
INSERT INTO zotero_entry (
    item_id, kind, zotero_key, item_type, title, url, saved_at, publication_id, withdrawn_at
) VALUES (
    :item_id, 'zotero_entry', :zotero_key, :item_type, :title, :url, :saved_at,
    :publication_id, :withdrawn_at
)
ON CONFLICT (zotero_key) DO UPDATE SET
    item_type = excluded.item_type,
    title = excluded.title,
    url = excluded.url,
    saved_at = excluded.saved_at,
    publication_id = excluded.publication_id,
    revised_at = :collected_at,
    withdrawn_at = NULL
WHERE :is_after_latest_catalog
    AND (zotero_entry.withdrawn_at IS NOT NULL
        OR (zotero_entry.item_type, zotero_entry.title, zotero_entry.url,
            zotero_entry.saved_at, zotero_entry.publication_id)
        IS NOT (excluded.item_type, excluded.title, excluded.url, excluded.saved_at,
            excluded.publication_id))
RETURNING id
"""

# State catalog (Zotero), the same rule for each highlight of an entry.
UPSERT_HIGHLIGHT = """
INSERT INTO highlight (zotero_key, text, comment, highlighted_at, item_id, withdrawn_at)
VALUES (:zotero_key, :text, :comment, :highlighted_at, :item_id, :withdrawn_at)
ON CONFLICT (zotero_key) DO UPDATE SET
    text = excluded.text,
    comment = excluded.comment,
    highlighted_at = excluded.highlighted_at,
    item_id = excluded.item_id,
    revised_at = :collected_at,
    withdrawn_at = NULL
WHERE :is_after_latest_catalog
    AND (highlight.withdrawn_at IS NOT NULL
        OR (highlight.text, highlight.comment, highlight.highlighted_at, highlight.item_id)
        IS NOT (excluded.text, excluded.comment, excluded.highlighted_at,
            excluded.item_id))
RETURNING highlight_id
"""

# State catalog (Zotero): an entry or a highlight absent from an extract collected after the
# latest one imported is withdrawn at that collection, never removed; the first absence keeps
# its date.
MARK_WITHDRAWN_ZOTERO_ENTRIES = """
UPDATE zotero_entry SET withdrawn_at = :collected_at
WHERE withdrawn_at IS NULL
    AND zotero_key NOT IN (SELECT value FROM json_each(:present_keys))
"""

MARK_WITHDRAWN_HIGHLIGHTS = """
UPDATE highlight SET withdrawn_at = :collected_at
WHERE withdrawn_at IS NULL
    AND zotero_key NOT IN (SELECT value FROM json_each(:present_keys))
"""


def write(
    connection: sqlite3.Connection, item: Item, raw_data_id: int
) -> Discard | None:
    """The Zotero item, its entry and its highlights, found by their Zotero keys; a highlight
    changed is a new version of the entry."""
    row = connection.execute(
        "SELECT item_id FROM zotero_entry WHERE zotero_key = ?", (item.source_item_id,)
    ).fetchone()
    known_item_id = row[0] if row else None
    latest = catalog.read_latest(
        connection, item.source_code, item.collected_at, raw_data_id, known_item_id
    )
    item_id = (
        shared_rows.insert_item(connection, "zotero_entry", raw_data_id)
        if known_item_id is None
        else known_item_id
    )
    catalog_values = {
        "collected_at": format_date(item.collected_at),
        "is_after_latest_catalog": latest.is_file_after,
        "withdrawn_at": catalog.find_withdrawn_at_of_new_row(latest),
    }
    # A known entry of an extract imported late is not rewritten (the WHERE of the upsert),
    # and its publication_id is not read: a publication created for it would be one no entry
    # points to, if its reference has changed in Zotero since.
    publication_id = (
        write_cited_publication(connection, item.arxiv_id)
        if known_item_id is None or latest.is_file_after
        else None
    )
    entry_written = connection.execute(
        UPSERT_ZOTERO_ENTRY,
        {
            "item_id": item_id,
            "zotero_key": item.source_item_id,
            "item_type": item.item_type,
            "title": item.title,
            "url": item.url,
            "saved_at": format_date(item.saved_at),
            "publication_id": publication_id,
        }
        | catalog_values,
    ).fetchone()
    # A list, not a generator: every highlight is written, not only up to the first written.
    highlights_written = [
        connection.execute(
            UPSERT_HIGHLIGHT,
            {
                "zotero_key": highlight.zotero_key,
                "text": highlight.text,
                "comment": highlight.comment,
                "highlighted_at": format_date(highlight.highlighted_at),
                "item_id": item_id,
            }
            | catalog_values,
        ).fetchone()
        for highlight in item.highlights
    ]
    if entry_written is None and not any(highlights_written):
        return catalog.build_discard(connection, item, item_id, latest)
    # An extract imported late writes only what it alone brings, withdrawn: not a version.
    if known_item_id is not None and latest.is_file_after:
        shared_rows.point_item_at_raw_data(connection, known_item_id, raw_data_id)
    return None


def write_cited_publication(
    connection: sqlite3.Connection, arxiv_id: str | None
) -> int | None:
    """The publication of arxiv_id, created without its arXiv columns if absent."""
    if arxiv_id is None:
        return None
    connection.execute(INSERT_CITED_PUBLICATION, (arxiv_id,))
    (publication_id,) = connection.execute(
        "SELECT id FROM publication WHERE arxiv_id = ?", (arxiv_id,)
    ).fetchone()
    return int(publication_id)


def mark_withdrawn(
    connection: sqlite3.Connection,
    source_code: str,
    collected_at: datetime,
    raw_data_id: int,
    entries: Sequence[Item],
) -> int:
    """Mark as withdrawn the entries and highlights an extract collected after the latest one
    imported no longer lists; return how many.

    Without an entry, nothing is withdrawn: an empty list would withdraw every entry and
    highlight of the base."""
    if not entries:
        return 0
    # No item: only the other files of the source are compared.
    latest = catalog.read_latest(
        connection, source_code, collected_at, raw_data_id, None
    )
    if not latest.is_file_after:
        return 0
    formatted_collected_at = format_date(collected_at)
    entry_keys = [entry.source_item_id for entry in entries]
    highlight_keys = [
        highlight.zotero_key for entry in entries for highlight in entry.highlights
    ]
    withdrawn_entries = connection.execute(
        MARK_WITHDRAWN_ZOTERO_ENTRIES,
        {
            "collected_at": formatted_collected_at,
            "present_keys": json.dumps(entry_keys),
        },
    ).rowcount
    withdrawn_highlights = connection.execute(
        MARK_WITHDRAWN_HIGHLIGHTS,
        {
            "collected_at": formatted_collected_at,
            "present_keys": json.dumps(highlight_keys),
        },
    ).rowcount
    return withdrawn_entries + withdrawn_highlights
