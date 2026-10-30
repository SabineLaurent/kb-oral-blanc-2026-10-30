"""The Zotero source: extract the documents of the user's library and their highlights (E), then
read an extract into Zotero entries (T).

zotero.sqlite is never opened: it is copied with its write-ahead log to a temporary folder,
checked, queried read-only, and deleted."""

import contextlib
import json
import logging
import os
import re
import shutil
import sqlite3
import stat
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, NamedTuple

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    ValidationError,
)

from k_b.core.quoting import without_values
from k_b.etl.raw_store import (
    SELECTED_LIBRARY_DIR_NAME,
    RawStore,
    make_source_folder_key,
)
from k_b.etl.source_contract import (
    EntryReading,
    ExpectedSourceError,
    SourceReader,
    check_not_all_invalid,
    check_not_empty,
    read_each_entry,
)
from k_b.items import ARXIV_ID, Highlight, Item, SourceCode

logger = logging.getLogger(__name__)

# The name of its raw folders, written once: its instances and its kind use it.
FOLDER_PREFIX = "zotero"

SOURCE_CODE: SourceCode = "zotero"


# === EXTRACT ===

# The folder of raw/ its extracts go to.
RAW_FOLDER_KEY = make_source_folder_key(SELECTED_LIBRARY_DIR_NAME, FOLDER_PREFIX)

DATABASE_FILE_NAME = "zotero.sqlite"
# Zotero's last writes are here while it runs: the main file alone would be an older library.
WRITE_AHEAD_LOG_FILE_NAME = "zotero.sqlite-wal"
# The temporary folder is recognisable: one left behind by a killed process is found and
# removed by the next extraction.
TEMPORARY_DIR_PREFIX = "kb-zotero-"
# How much of what integrity_check answers, or of the error it raises, goes into the error.
MAX_REPORTED_PROBLEM_LENGTH = 200
# The only messages of SQLite kept as they are: they quote the query, which is a constant.
# Another message can quote the content of the base (a text that is not UTF-8).
MESSAGE_ABOUT_THE_QUERY = re.compile(
    r'no such (table|column): [\w.]+|near "[^"]*": syntax error'
)

# One statement for the whole library. A document is defined once: an item of the user's
# library, that is not a file, a note or an annotation, out of the trash. Of the fields Zotero
# keeps as rows (itemData), only four are read. The lists come back as JSON, in a stable order.
# Not read: the abstracts, the notes, the annotations that are not highlights (type 1), the
# files outside Zotero's storage (a linked file keeps its own path), the group libraries.
EXTRACTION_QUERY = """
WITH document AS (
    SELECT items.itemID, items.key, itemTypes.typeName, items.dateAdded
    FROM items
    JOIN itemTypes ON itemTypes.itemTypeID = items.itemTypeID
    JOIN libraries ON libraries.libraryID = items.libraryID
    WHERE libraries.type = 'user'
        AND itemTypes.typeName NOT IN ('attachment', 'note', 'annotation')
        AND items.itemID NOT IN (SELECT itemID FROM deletedItems)
),
field_value AS (
    SELECT itemData.itemID, fields.fieldName, itemDataValues.value
    FROM itemData
    JOIN fields ON fields.fieldID = itemData.fieldID
    JOIN itemDataValues ON itemDataValues.valueID = itemData.valueID
    WHERE fields.fieldName IN ('title', 'date', 'url', 'archiveID')
)
SELECT
    document.key,
    document.typeName AS item_type,
    (SELECT value FROM field_value
        WHERE itemID = document.itemID AND fieldName = 'title') AS title,
    (SELECT value FROM field_value
        WHERE itemID = document.itemID AND fieldName = 'date') AS date,
    document.dateAdded AS date_added,
    (SELECT value FROM field_value
        WHERE itemID = document.itemID AND fieldName = 'url') AS url,
    (SELECT value FROM field_value
        WHERE itemID = document.itemID AND fieldName = 'archiveID') AS archive_id,
    (
        SELECT json_group_array(
            json_object('first_name', creators.firstName, 'last_name', creators.lastName)
            ORDER BY itemCreators.orderIndex
        )
        FROM itemCreators
        JOIN creators ON creators.creatorID = itemCreators.creatorID
        JOIN creatorTypes ON creatorTypes.creatorTypeID = itemCreators.creatorTypeID
        WHERE itemCreators.itemID = document.itemID
            AND creatorTypes.creatorType = 'author'
    ) AS authors,
    (
        SELECT json_group_array(
            json_object('name', tags.name, 'type', itemTags.type) ORDER BY tags.name
        )
        FROM itemTags
        JOIN tags ON tags.tagID = itemTags.tagID
        WHERE itemTags.itemID = document.itemID
    ) AS tags,
    (
        SELECT json_group_array(
            json_object('key', attachment.key, 'path', itemAttachments.path)
            ORDER BY attachment.itemID
        )
        FROM itemAttachments
        JOIN items AS attachment ON attachment.itemID = itemAttachments.itemID
        WHERE itemAttachments.parentItemID = document.itemID
            AND itemAttachments.contentType = 'application/pdf'
            AND itemAttachments.path LIKE 'storage:%'
            AND itemAttachments.itemID NOT IN (SELECT itemID FROM deletedItems)
    ) AS pdfs,
    (
        SELECT json_group_array(
            json_object(
                'key', annotation.key,
                'pdf_key', attachment.key,
                'text', itemAnnotations.text,
                'comment', itemAnnotations.comment,
                'page_label', itemAnnotations.pageLabel,
                'date_added', annotation.dateAdded
            )
            ORDER BY attachment.itemID, itemAnnotations.sortIndex, annotation.itemID
        )
        FROM itemAttachments
        JOIN items AS attachment ON attachment.itemID = itemAttachments.itemID
        JOIN itemAnnotations ON itemAnnotations.parentItemID = itemAttachments.itemID
        JOIN items AS annotation ON annotation.itemID = itemAnnotations.itemID
        WHERE itemAttachments.parentItemID = document.itemID
            AND itemAttachments.contentType = 'application/pdf'
            AND itemAttachments.path LIKE 'storage:%'
            AND itemAttachments.itemID NOT IN (SELECT itemID FROM deletedItems)
            AND itemAnnotations.type = 1
            AND itemAnnotations.itemID NOT IN (SELECT itemID FROM deletedItems)
    ) AS highlights
FROM document
ORDER BY document.dateAdded, document.itemID
"""
# The columns the query returns as a JSON list.
JSON_COLUMNS = ("authors", "tags", "pdfs", "highlights")


class ZoteroCopyInconsistentError(ExpectedSourceError):
    """PRAGMA integrity_check did not answer ok on the copy, or could not read it."""


class ZoteroChangedDuringCopyError(ExpectedSourceError):
    """The main file or its write-ahead log changed while they were copied: the two copies may
    not go together."""


class ZoteroQueryFailedError(ExpectedSourceError):
    """SQLite refused the extraction query: the message names the missing table or column, or the
    syntax SQLite does not know (ORDER BY in an aggregate needs SQLite 3.44)."""


class FileState(NamedTuple):
    size: int
    modified_at_ns: int
    inode: int


class ZoteroSource:
    name = FOLDER_PREFIX
    extension = "json"
    acquisition_dir_name = SELECTED_LIBRARY_DIR_NAME

    def __init__(self, zotero_dir: Path, store: RawStore) -> None:
        self.zotero_dir = zotero_dir
        self.store = store

    def fetch(self, last_collected_at: datetime | None) -> bytes | None:
        """Return the documents of the library and their highlights as JSON, or None when the
        extract is the same, byte for byte, as the last one in raw/.

        The library is extracted at each collection, whatever last_collected_at: only the content
        is compared, as file dates do not tell a change."""
        remove_left_copies()
        # Deleted on a success, an error or Ctrl-C; a killed process leaves it, and the next
        # extraction removes it. In the system's temporary folder, not in the data directory:
        # a whole library has no place in what gets backed up.
        with tempfile.TemporaryDirectory(prefix=TEMPORARY_DIR_PREFIX) as copy_dir:
            copy_path = copy_library(self.zotero_dir, Path(copy_dir))
            documents = extract_documents(copy_path)
        checked_documents = read_each_entry(enumerate(documents, 1), check_document)
        check_not_empty(checked_documents)
        check_not_all_invalid(checked_documents)
        logger.info(
            "zotero: %d documents, %d highlights extracted",
            len(documents),
            sum(len(document["highlights"]) for document in documents),
        )
        raw_extract = (
            json.dumps(documents, ensure_ascii=False, indent=2) + "\n"
        ).encode()
        if raw_extract == self.store.read_latest(RAW_FOLDER_KEY):
            logger.info("zotero: extract identical to the latest raw file, not written")
            return None
        return raw_extract


def remove_left_copies() -> None:
    """Remove the copies of the library a killed process left in the temporary folder, this
    user's only.

    Two runs of kb collect at the same time can remove each other's copy: K-B has no lock against
    it."""
    removed_count = 0
    for path in Path(tempfile.gettempdir()).glob(TEMPORARY_DIR_PREFIX + "*"):
        # Another run may remove it first: a copy already gone is passed over.
        try:
            status = path.lstat()
            if stat.S_ISDIR(status.st_mode) and status.st_uid == os.getuid():
                shutil.rmtree(path)
                removed_count += 1
        except FileNotFoundError:
            continue
    if removed_count:
        logger.info("zotero: %d copies left by a stopped run removed", removed_count)


def read_file_states(zotero_dir: Path) -> tuple[FileState, FileState | None]:
    """The state of the main file, which must exist, and of its write-ahead log, None when there
    is none."""
    database_state = read_file_state(zotero_dir / DATABASE_FILE_NAME)
    try:
        log_state = read_file_state(zotero_dir / WRITE_AHEAD_LOG_FILE_NAME)
    except FileNotFoundError:
        log_state = None
    return database_state, log_state


def read_file_state(path: Path) -> FileState:
    status = path.stat()
    return FileState(status.st_size, status.st_mtime_ns, status.st_ino)


def copy_library(zotero_dir: Path, copy_dir: Path) -> Path:
    """Copy the main file, then its write-ahead log; return the copy of the main file.

    A pair that does not go together passes integrity_check: their states are compared before and
    after the copy."""
    states_before = read_file_states(zotero_dir)
    copy_path = copy_dir / DATABASE_FILE_NAME
    shutil.copyfile(zotero_dir / DATABASE_FILE_NAME, copy_path)
    with contextlib.suppress(FileNotFoundError):
        shutil.copyfile(
            zotero_dir / WRITE_AHEAD_LOG_FILE_NAME, copy_dir / WRITE_AHEAD_LOG_FILE_NAME
        )
    states_after = read_file_states(zotero_dir)
    if states_after != states_before:
        raise ZoteroChangedDuringCopyError(
            f"{DATABASE_FILE_NAME} and {WRITE_AHEAD_LOG_FILE_NAME}:"
            f" before the copy {states_before!r}, after {states_after!r}"
        )
    return copy_path


def extract_documents(copy_path: Path) -> list[dict[str, Any]]:
    """Check the copy, then run the extraction query on it, read-only."""
    # as_uri: a ?, a # or a % in the path of the temporary folder would break the URI.
    with contextlib.closing(
        sqlite3.connect(f"{copy_path.as_uri()}?mode=ro", uri=True)
    ) as connection:
        connection.row_factory = sqlite3.Row
        check_integrity(connection)
        try:
            rows = connection.execute(EXTRACTION_QUERY).fetchall()
        except sqlite3.DatabaseError as error:
            # from None: the traceback logged by the collection would show SQLite's message.
            raise ZoteroQueryFailedError(describe_query_error(error)) from None
    return [
        {
            column: json.loads(row[column]) if column in JSON_COLUMNS else row[column]
            for column in row.keys()  # noqa: SIM118 — a Row iterates over its values
        }
        for row in rows
    ]


def check_integrity(connection: sqlite3.Connection) -> None:
    try:
        problems = [row[0] for row in connection.execute("PRAGMA integrity_check")]
    except sqlite3.DatabaseError as error:
        raise ZoteroCopyInconsistentError(
            f"integrity_check raised {type(error).__name__}:"
            f" {str(error)[:MAX_REPORTED_PROBLEM_LENGTH]!r}"
        ) from error
    if problems != ["ok"]:
        raise ZoteroCopyInconsistentError(
            f"integrity_check reported {len(problems)} problems, the first:"
            f" {str(problems[0])[:MAX_REPORTED_PROBLEM_LENGTH]!r}"
        )


def describe_query_error(error: sqlite3.DatabaseError) -> str:
    """SQLite's message when it only quotes the query; else the type of the error and SQLite's
    name for it (SQLITE_CORRUPT...), which quote nothing."""
    message = str(error)
    if MESSAGE_ABOUT_THE_QUERY.fullmatch(message):
        return message
    # An error raised by the sqlite3 module itself, not by SQLite, has no such name.
    sqlite_error_name = getattr(error, "sqlite_errorname", None)
    if sqlite_error_name is None:
        return type(error).__name__
    return f"{type(error).__name__} ({sqlite_error_name})"


# === TRANSFORM ===

# A date of Zotero: UTC, written without its time zone (2026-10-01 08:15:00).
ZOTERO_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}")
ARXIV_REFERENCE_PREFIX = "arXiv:"
# archiveID of an arXiv paper, its version left out: the publication is known without it.
ARXIV_REFERENCE = re.compile(rf"{ARXIV_REFERENCE_PREFIX}({ARXIV_ID})(?:v[0-9]+)?")


def mark_as_utc(zotero_date: object) -> object:
    """Give a date of Zotero its time zone, UTC, for pydantic to read."""
    if not isinstance(zotero_date, str) or not ZOTERO_DATE.fullmatch(zotero_date):
        raise ValueError("not a date of Zotero (YYYY-MM-DD HH:MM:SS)")
    return f"{zotero_date}Z"


def read_arxiv_id(archive_id: str | None) -> str | None:
    """The arXiv identifier of an archiveID; None when the document has no arXiv reference."""
    if not archive_id or not archive_id.startswith(ARXIV_REFERENCE_PREFIX):
        return None
    reference = ARXIV_REFERENCE.fullmatch(archive_id)
    if reference is None:
        raise ValueError(
            f"archive_id not an arXiv reference: {len(archive_id)} characters"
        )
    return reference.group(1)


ZoteroDate = Annotated[AwareDatetime, BeforeValidator(mark_as_utc)]


class ZoteroHighlight(BaseModel):
    """A highlight of the extract, without its PDF and its page."""

    model_config = ConfigDict(frozen=True, extra="ignore", hide_input_in_errors=True)

    key: str
    text: str | None
    comment: str | None
    date_added: ZoteroDate


class ZoteroDocument(BaseModel):
    """A document of the extract, without its date, authors, tags and PDF: validating them would
    refuse a document for a field the base does not keep."""

    model_config = ConfigDict(frozen=True, extra="ignore", hide_input_in_errors=True)

    key: str
    item_type: str
    title: str
    date_added: ZoteroDate
    url: str | None
    arxiv_id: Annotated[str | None, AfterValidator(read_arxiv_id)] = Field(
        validation_alias="archive_id"
    )
    highlights: list[ZoteroHighlight]


def read_documents(raw_extract: bytes) -> EntryReading[object]:
    """The entries of an extract, refused if not a JSON list; each entry is validated when
    converted."""
    try:
        documents = json.loads(raw_extract)
    except ValueError:
        documents = None
    if not isinstance(documents, list):
        raise ValueError("extract not a JSON list")
    return EntryReading(documents, [])


def check_document(entry: object, number: int) -> ZoteroDocument:
    """A document of the extract, validated at the collection; refused, the error never quotes
    its value."""
    try:
        return ZoteroDocument.model_validate(entry)
    except ValidationError as error:
        raise ValueError(f"document {number}: {without_values(error)}") from None


def convert_document(entry: object, collected_at: datetime, raw_file_path: str) -> Item:
    """A document of the extract as a Zotero entry, with its highlights; refused, the error never
    quotes a value, as a title or a highlight is a personal data."""
    try:
        document = ZoteroDocument.model_validate(entry)
        return Item(
            source_code=SOURCE_CODE,
            source_item_id=document.key,
            kind="zotero_entry",
            title=document.title,
            url=document.url,
            saved_at=document.date_added,
            collected_at=collected_at,
            raw_file_path=raw_file_path,
            item_type=document.item_type,
            arxiv_id=document.arxiv_id,
            highlights=tuple(
                Highlight(
                    zotero_key=highlight.key,
                    # An empty text or comment is an absent one: stored NULL alike, the next
                    # extract giving the other form is not a revision.
                    text=highlight.text or None,
                    comment=highlight.comment or None,
                    highlighted_at=highlight.date_added,
                )
                for highlight in document.highlights
                # Without text or comment, a highlight says nothing: left out, its document
                # is not refused (decision of 2026-10-13).
                if highlight.text or highlight.comment
            ),
        )
    except ValidationError as error:
        raise without_values(error) from None


READER: SourceReader[object] = SourceReader(
    SOURCE_CODE, read_documents, convert_document
)
