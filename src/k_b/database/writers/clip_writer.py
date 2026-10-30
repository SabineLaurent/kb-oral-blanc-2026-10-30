"""Write a clip: the latest version of a URL is kept, a clip whose content changed is written
again, revised_at dated."""

import sqlite3
from typing import NamedTuple

from k_b.core.dates import format_date
from k_b.database.writers import shared_rows
from k_b.database.writers.discard import Discard
from k_b.items import Item

# News feed, as INSERT_ITEM_OF_NEW_RELEASE (release_writer.py). A clip is known by its natural
# key: its URL, without its utm_* parameters.
INSERT_ITEM_OF_NEW_CLIP = """
INSERT INTO item (kind, raw_data_id)
SELECT 'clip', :raw_data_id WHERE NOT EXISTS (SELECT 1 FROM clip WHERE url = :url)
RETURNING id
"""

INSERT_CLIP = """
INSERT INTO clip (item_id, kind, url, title, body, saved_at, published_at)
VALUES (:item_id, 'clip', :url, :title, :body, :saved_at, :published_at)
"""

# The clip of the URL, its content, and whether the file being imported was collected before
# the raw file its item points to. The dates are compared as text (database/dates.py).
SELECT_KNOWN_CLIP = """
SELECT clip.item_id, clip.title, clip.body, clip.saved_at, clip.published_at,
    raw_data.collected_at, :collected_at < raw_data.collected_at
FROM clip
JOIN item ON item.id = clip.item_id
JOIN raw_data ON raw_data.id = item.raw_data_id
WHERE clip.url = :url
"""

# A tag shared by every item that carries it: created by the first.
INSERT_TAG = "INSERT INTO tag (name) VALUES (:name) ON CONFLICT (name) DO NOTHING"

SELECT_TAG_ID = "SELECT id FROM tag WHERE name = ?"

INSERT_ITEM_HAS_TAG = """
INSERT INTO item_has_tag (item_id, tag_id) VALUES (:item_id, :tag_id)
"""

SELECT_ITEM_TAGS = """
SELECT tag.name, tag.id FROM item_has_tag JOIN tag ON tag.id = item_has_tag.tag_id
WHERE item_has_tag.item_id = ?
"""

# A link of the version before to a tag the new version no longer carries: a link is a value of
# a version, as a column, not an item. One of the two exceptions to removing by the purge only
# (conventions.md, rule 5; decision of Sabine of 2026-10-19).
DELETE_ITEM_HAS_TAG = """
DELETE FROM item_has_tag WHERE item_id = :item_id AND tag_id = :tag_id
"""

# A tag no item carries any more, once its last link removed: a tag removed no longer exists in
# the base. The other exception to removing by the purge only (conventions.md, rule 5; decision
# of Sabine of 2026-10-19).
DELETE_TAG_WITHOUT_LINK = """
DELETE FROM tag
WHERE id = :tag_id
  AND NOT EXISTS (SELECT 1 FROM item_has_tag WHERE item_has_tag.tag_id = tag.id)
"""

# A patch, not a put (decision of Sabine of 2026-10-19): the SET names only the columns that
# changed, taken from the fields of ClipContent, never from a value read.
UPDATE_CLIP = "UPDATE clip SET {assignments} WHERE item_id = :item_id"


class ClipContent(NamedTuple):
    """What the base keeps of a clip, in its format: a change of it is a new version."""

    title: str
    # Required of a clip by the item (REQUIRED_FIELDS_BY_KIND, items.py), typed as the item.
    body: str | None
    saved_at: str | None
    published_at: str | None
    # A set: the tags compare without their order.
    tags: frozenset[str]

    def to_columns(self) -> dict[str, str | None]:
        """The columns of clip, every field but the tags."""
        columns = self._asdict()
        del columns["tags"]
        return columns


def write(
    connection: sqlite3.Connection, item: Item, raw_data_id: int
) -> Discard | None:
    """The item and its clip; a clip of a URL already known is written again if it changed."""
    content = ClipContent(
        item.title,
        item.summary,
        format_date(item.saved_at),
        format_date(item.published_at),
        frozenset(item.tags),
    )
    row = connection.execute(
        INSERT_ITEM_OF_NEW_CLIP, {"raw_data_id": raw_data_id, "url": item.url}
    ).fetchone()
    if row is None:
        return write_known_clip(connection, item, raw_data_id, content)
    connection.execute(
        INSERT_CLIP, {"item_id": row[0], "url": item.url} | content.to_columns()
    )
    write_tags(connection, row[0], item.tags)
    return None


def write_known_clip(
    connection: sqlite3.Connection, item: Item, raw_data_id: int, content: ClipContent
) -> Discard | None:
    """The known clip of the URL takes content, unless it is the same or the file was collected
    before the raw file of the clip; of two files of a URL in one collection, the second in page
    name order wins."""
    collected_at = format_date(item.collected_at)
    (
        known_item_id,
        known_title,
        known_body,
        known_saved_at,
        known_published_at,
        known_collected_at,
        is_file_before,
    ) = connection.execute(
        SELECT_KNOWN_CLIP, {"url": item.url, "collected_at": collected_at}
    ).fetchone()
    known_tag_ids = read_tag_ids(connection, known_item_id)
    known_content = ClipContent(
        title=known_title,
        body=known_body,
        saved_at=known_saved_at,
        published_at=known_published_at,
        tags=frozenset(known_tag_ids),
    )
    if known_content == content:
        if not is_file_before:
            shared_rows.point_item_at_raw_data(connection, known_item_id, raw_data_id)
        return Discard("already_seen", item, known_collected_at)
    if is_file_before:
        return Discard("no_newer_version", item, known_collected_at)
    write_changed_columns(
        connection, known_item_id, known_content, content, collected_at
    )
    write_changed_tags(connection, known_item_id, known_tag_ids, content.tags)
    shared_rows.point_item_at_raw_data(connection, known_item_id, raw_data_id)
    return None


def write_changed_columns(
    connection: sqlite3.Connection,
    item_id: int,
    known: ClipContent,
    content: ClipContent,
    revised_at: str | None,
) -> None:
    """Write the columns of clip that changed, and revised_at, dated also if a tag alone changed."""
    known_columns = known.to_columns()
    changed = {
        column: value
        for column, value in content.to_columns().items()
        if value != known_columns[column]
    }
    assignments = [f"{column} = :{column}" for column in [*changed, "revised_at"]]
    connection.execute(
        # The names are the keys of ClipContent.to_columns, written in this module.
        UPDATE_CLIP.format(assignments=", ".join(assignments)),
        changed | {"item_id": item_id, "revised_at": revised_at},
    )


def write_changed_tags(
    connection: sqlite3.Connection,
    item_id: int,
    known_tag_ids: dict[str, int],
    tags: frozenset[str],
) -> None:
    """Unlink the tags removed, deleting one no item carries any more, then link the tags added."""
    for removed in sorted(known_tag_ids.keys() - tags):
        link = {"item_id": item_id, "tag_id": known_tag_ids[removed]}
        connection.execute(DELETE_ITEM_HAS_TAG, link)
        connection.execute(DELETE_TAG_WITHOUT_LINK, link)
    write_tags(connection, item_id, tuple(sorted(tags - known_tag_ids.keys())))


def read_tag_ids(connection: sqlite3.Connection, item_id: int) -> dict[str, int]:
    return dict(connection.execute(SELECT_ITEM_TAGS, (item_id,)).fetchall())


def write_tags(
    connection: sqlite3.Connection, item_id: int, tags: tuple[str, ...]
) -> None:
    """Link the item to each of its tags, created if new, in the order given; the id is read, so
    that a missing tag stops the import instead of dropping its link silently."""
    for tag in tags:
        connection.execute(INSERT_TAG, {"name": tag})
        (tag_id,) = connection.execute(SELECT_TAG_ID, (tag,)).fetchone()
        connection.execute(INSERT_ITEM_HAS_TAG, {"item_id": item_id, "tag_id": tag_id})
