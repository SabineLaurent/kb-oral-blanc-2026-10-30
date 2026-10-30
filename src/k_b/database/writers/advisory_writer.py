"""Write a GitHub advisory: a news feed, the first version seen is kept."""

import json
import sqlite3

from k_b.core.dates import format_date
from k_b.database.writers import shared_rows
from k_b.database.writers.discard import Discard
from k_b.items import Item

# News feed, as INSERT_ITEM_OF_NEW_RELEASE (release_writer.py).
INSERT_ITEM_OF_NEW_ADVISORY = """
INSERT INTO item (kind, raw_data_id)
SELECT 'advisory', ? WHERE NOT EXISTS (SELECT 1 FROM advisory WHERE ghsa_id = ?)
RETURNING id
"""

INSERT_ADVISORY = """
INSERT INTO advisory (
    item_id, kind, ghsa_id, vulnerability_id, summary, published_at, url, packages
) VALUES (?, 'advisory', ?, ?, ?, ?, ?, ?)
"""


def write(
    connection: sqlite3.Connection, item: Item, raw_data_id: int
) -> Discard | None:
    """The GitHub item and its advisory, unless the advisory is known; the CVE it cites is
    created without its KEV columns if new."""
    row = connection.execute(
        INSERT_ITEM_OF_NEW_ADVISORY, (raw_data_id, item.source_item_id)
    ).fetchone()
    if row is None:
        (known_item_id,) = connection.execute(
            "SELECT item_id FROM advisory WHERE ghsa_id = ?", (item.source_item_id,)
        ).fetchone()
        return Discard(
            "already_seen",
            item,
            shared_rows.read_item_collected_at(connection, known_item_id),
        )
    vulnerability_id = None
    if item.cve is not None:
        connection.execute(
            "INSERT INTO vulnerability (cve_id) VALUES (?) ON CONFLICT (cve_id) DO NOTHING",
            (item.cve,),
        )
        (vulnerability_id,) = connection.execute(
            "SELECT id FROM vulnerability WHERE cve_id = ?", (item.cve,)
        ).fetchone()
    connection.execute(
        INSERT_ADVISORY,
        (
            row[0],
            item.source_item_id,
            vulnerability_id,
            item.title,
            format_date(item.published_at),
            item.url,
            json.dumps(list(item.package_names), ensure_ascii=False),
        ),
    )
    return None
