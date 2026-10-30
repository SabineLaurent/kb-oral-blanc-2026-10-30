"""Write a GitHub release, and the tracked repositories it belongs to: a news feed, the first
version seen is kept."""

import json
import sqlite3
from collections.abc import Sequence

from k_b.core.config import TrackedRepository
from k_b.core.dates import format_date
from k_b.database.writers import shared_rows
from k_b.database.writers.discard import Discard
from k_b.items import Item

# News feed (releases, advisories, TLDR articles): the item is created only if its subtype is
# not known yet, and the first version seen is kept. The subtype needs its item first: no ON
# CONFLICT can come before the item; its UNIQUE constraint stays the safety net.
INSERT_ITEM_OF_NEW_RELEASE = """
INSERT INTO item (kind, raw_data_id)
SELECT 'release', ? WHERE NOT EXISTS (SELECT 1 FROM release WHERE release_id = ?)
RETURNING id
"""

# The list of the code is the source of truth: a repository seen again takes its values.
UPSERT_TRACKED_REPOSITORY = """
INSERT INTO tracked_repository (repository_name, ecosystem, packages) VALUES (?, ?, ?)
ON CONFLICT (repository_name) DO UPDATE SET
    repository_name = excluded.repository_name,
    ecosystem = excluded.ecosystem,
    packages = excluded.packages
"""

INSERT_RELEASE = """
INSERT INTO release (
    item_id, kind, tracked_repository_id, release_id, tag_name, name, body, published_at, url
) VALUES (?, 'release', ?, ?, ?, ?, ?, ?, ?)
"""


def write_tracked_repositories(
    connection: sqlite3.Connection, tracked_repositories: Sequence[TrackedRepository]
) -> None:
    connection.executemany(
        UPSERT_TRACKED_REPOSITORY,
        [
            (
                repository.name,
                repository.ecosystem,
                json.dumps(repository.packages, ensure_ascii=False),
            )
            for repository in tracked_repositories
        ],
    )


def write(
    connection: sqlite3.Connection, item: Item, raw_data_id: int
) -> Discard | None:
    """The GitHub item and its release, unless the release is known: the first seen is kept."""
    release_id = int(item.source_item_id)
    row = connection.execute(
        INSERT_ITEM_OF_NEW_RELEASE, (raw_data_id, release_id)
    ).fetchone()
    if row is None:
        (known_item_id,) = connection.execute(
            "SELECT item_id FROM release WHERE release_id = ?", (release_id,)
        ).fetchone()
        return Discard(
            "already_seen",
            item,
            shared_rows.read_item_collected_at(connection, known_item_id),
        )
    # The releases of a repository out of the list were rejected before the import.
    (tracked_repository_id,) = connection.execute(
        "SELECT id FROM tracked_repository WHERE repository_name = ?",
        (item.repository_name,),
    ).fetchone()
    connection.execute(
        INSERT_RELEASE,
        (
            row[0],
            tracked_repository_id,
            release_id,
            item.tag_name,
            item.release_name,
            item.summary,
            format_date(item.published_at),
            item.url,
        ),
    )
    return None
