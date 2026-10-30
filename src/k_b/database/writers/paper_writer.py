"""Write an arXiv paper: a feed with revisions, the newest revision is kept."""

import json
import sqlite3

from k_b.core.dates import format_date
from k_b.database.writers import shared_rows
from k_b.database.writers.discard import Discard
from k_b.items import Item

# Feed with revisions (arXiv): a paper seen again rewrites its arXiv columns, under the same
# item, only with a newer revision. A paper cited before arXiv gave it takes its first version.
UPSERT_PUBLICATION = """
INSERT INTO publication (arxiv_id, item_id, title, summary, authors, submitted_at, revised_at)
VALUES (?, ?, ?, ?, ?, ?, ?)
ON CONFLICT (arxiv_id) DO UPDATE SET
    item_id = excluded.item_id,
    title = excluded.title,
    summary = excluded.summary,
    authors = excluded.authors,
    submitted_at = excluded.submitted_at,
    revised_at = excluded.revised_at
WHERE publication.item_id IS NULL OR excluded.revised_at > publication.revised_at
RETURNING id
"""


def write(
    connection: sqlite3.Connection, item: Item, raw_data_id: int
) -> Discard | None:
    """The arXiv item and its publication, found by its arXiv identifier."""
    row = connection.execute(
        "SELECT item_id, revised_at FROM publication WHERE arxiv_id = ?",
        (item.source_item_id,),
    ).fetchone()
    known_item_id, known_revised_at = row if row else (None, None)
    item_id = (
        shared_rows.insert_item(connection, "paper", raw_data_id)
        if known_item_id is None
        else known_item_id
    )
    written = connection.execute(
        UPSERT_PUBLICATION,
        (
            item.source_item_id,
            item_id,
            item.title,
            item.summary,
            json.dumps(list(item.authors), ensure_ascii=False),
            format_date(item.published_at),
            format_date(item.revised_at),
        ),
    ).fetchone()
    if written is None:
        # Left out only for a known arXiv item, whose revised_at the CHECK fills.
        return Discard("revision_not_newer", item, str(known_revised_at))
    if known_item_id is not None:
        shared_rows.point_item_at_raw_data(connection, known_item_id, raw_data_id)
    return None
