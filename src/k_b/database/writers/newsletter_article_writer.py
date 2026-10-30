"""Write a TLDR article: a news feed, the first version seen is kept."""

import sqlite3

from k_b.core.dates import format_date
from k_b.database.writers import shared_rows
from k_b.database.writers.discard import Discard
from k_b.items import Item

# News feed, as INSERT_ITEM_OF_NEW_RELEASE (release_writer.py). A TLDR article is known by its
# natural key: its edition, the day of the edition, its rank.
INSERT_ITEM_OF_NEW_NEWSLETTER_ARTICLE = """
INSERT INTO item (kind, raw_data_id)
SELECT 'newsletter_article', :raw_data_id WHERE NOT EXISTS (
    SELECT 1 FROM newsletter_article
    WHERE edition = :edition AND edition_date = :edition_date AND rank = :rank
)
RETURNING id
"""

SELECT_NEWSLETTER_ARTICLE_ITEM = """
SELECT item_id FROM newsletter_article
WHERE edition = :edition AND edition_date = :edition_date AND rank = :rank
"""

# publication_id stays empty: the paper an article cites is not looked for yet.
INSERT_NEWSLETTER_ARTICLE = """
INSERT INTO newsletter_article (
    item_id, kind, edition, edition_date, rank, title, summary, url
) VALUES (
    :item_id, 'newsletter_article', :edition, :edition_date, :rank, :title, :summary, :url
)
"""


def write(
    connection: sqlite3.Connection, item: Item, raw_data_id: int
) -> Discard | None:
    """The TLDR item and its article, unless the article is known: the first seen is kept."""
    natural_key = {
        "edition": item.edition,
        "edition_date": format_date(item.published_at),
        "rank": item.rank,
    }
    row = connection.execute(
        INSERT_ITEM_OF_NEW_NEWSLETTER_ARTICLE,
        {"raw_data_id": raw_data_id} | natural_key,
    ).fetchone()
    if row is None:
        (known_item_id,) = connection.execute(
            SELECT_NEWSLETTER_ARTICLE_ITEM, natural_key
        ).fetchone()
        return Discard(
            "already_seen",
            item,
            shared_rows.read_item_collected_at(connection, known_item_id),
        )
    connection.execute(
        INSERT_NEWSLETTER_ARTICLE,
        {
            "item_id": row[0],
            "title": item.title,
            "summary": item.summary,
            "url": item.url,
        }
        | natural_key,
    )
    return None
