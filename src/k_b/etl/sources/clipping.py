"""The Web Clipper source: copy the clips of the user's Obsidian vault as they are (E), then read
each clip into an item of the library (T).

K-B never changes a clip: it writes only veille/ of the vault."""

import codecs
import hashlib
import logging
import os
import re
import stat
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, NamedTuple

import yaml
from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    ValidationError,
)

from k_b.core.config import CLIP_CHANGE_MARGIN
from k_b.core.quoting import quote_value, without_values
from k_b.etl.normalize import remove_tracking_parameters
from k_b.etl.raw_store import SELECTED_LIBRARY_DIR_NAME
from k_b.etl.source_contract import (
    EntryReading,
    ExpectedSourceError,
    SourceReader,
    check_not_all_invalid,
)
from k_b.items import (
    CLIP_MAX_BYTES,
    Item,
    NonEmptyText,
    SourceCode,
    WebUrl,
    at_midnight_utc,
    normalize_tag,
)

logger = logging.getLogger(__name__)

# The name of its raw folders, written once: its instances and its kind use it.
FOLDER_PREFIX = "clipping"
SOURCE_CODE: SourceCode = "clipper"


# === EXTRACT ===

# The folder of the vault the default template of Web Clipper writes to (docs/specs-collecte.md).
CLIPPINGS_DIR_NAME = "Clippings"
CLIP_EXTENSION = ".md"
# The page name of a clip: the start of the SHA-256 of its file name, in hexadecimal. The same
# clip has the same name from one collection to the next, and its file name, which is its
# title, goes neither into the path of the raw nor into the import log.
PAGE_NAME_LENGTH = 16
# A file date is in nanoseconds since EPOCH; a datetime, in microseconds.
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
ONE_MICROSECOND = timedelta(microseconds=1)


class ClipTooLargeError(ExpectedSourceError):
    """A clip of the vault is larger than CLIP_MAX_BYTES: nothing is copied and each collection
    fails until it is shrunk or removed, so that the bound is never passed silently."""


class ClippingsFolderMissingError(ExpectedSourceError):
    """The vault, or its Clippings folder, is missing: not taken for a vault without clips, or
    the clips a mount brings later with their old dates would never be copied. No path in the
    message."""


class ClipFile(NamedTuple):
    path: Path
    # The newest of its modification and change dates, in nanoseconds since EPOCH.
    changed_at_ns: int


class ClippingSource:
    name = FOLDER_PREFIX
    extension = "md"
    acquisition_dir_name = SELECTED_LIBRARY_DIR_NAME

    def __init__(self, vault_dir: Path) -> None:
        self.vault_dir = vault_dir

    def fetch(self, last_collected_at: datetime | None) -> dict[str, bytes] | None:
        """Return each clip changed since last_collected_at (all, at the first collection), by
        page name; None when none was.

        The margin goes both ways (CLIP_CHANGE_MARGIN): a clip changed shortly before the last
        collection is copied again; one changed less than the margin before this one is left to
        the next, as it can still be written."""
        try:
            clip_files = list_clip_files(self.vault_dir / CLIPPINGS_DIR_NAME)
        except FileNotFoundError:
            raise ClippingsFolderMissingError(
                f"clippings_folder_missing: no {CLIPPINGS_DIR_NAME} folder in the vault of"
                " KB_VAULT_DIR; create it, or check KB_VAULT_DIR"
            ) from None
        changed = [
            clip_file
            for clip_file in clip_files
            if last_collected_at is None
            or clip_file.changed_at_ns > to_ns(last_collected_at - CLIP_CHANGE_MARGIN)
        ]
        settled_before_ns = to_ns(current_time() - CLIP_CHANGE_MARGIN)
        settled = [
            clip_file
            for clip_file in changed
            if clip_file.changed_at_ns <= settled_before_ns
        ]
        if len(settled) < len(changed):
            logger.info(
                "clipping: %d clips changed less than %d seconds ago,"
                " left to the next collection",
                len(changed) - len(settled),
                CLIP_CHANGE_MARGIN.total_seconds(),
            )
        clips = read_clip_files(settled)
        if not clips:
            return None
        # Read as the import will: clips all invalid fail the source (conventions.md, rule 1).
        reasons_by_page = {
            page_name: find_rejection_reason(page_name, clip)
            for page_name, clip in clips.items()
        }
        check_not_all_invalid(
            EntryReading(
                [page for page, reason in reasons_by_page.items() if reason is None],
                [reason for reason in reasons_by_page.values() if reason is not None],
            )
        )
        # Read, not copied: etl/extract.py writes the raw pages after.
        logger.info("clipping: %d clips read", len(clips))
        return clips


def current_time() -> datetime:
    return datetime.now(UTC)


def to_ns(moment: datetime) -> int:
    """A moment in whole nanoseconds since EPOCH: a float would round a write of the same
    microsecond onto it."""
    return (moment - EPOCH) // ONE_MICROSECOND * 1000


def list_clip_files(clippings_dir: Path) -> list[ClipFile]:
    """The clips of the folder in name order: its regular, visible Markdown files, not its
    subfolders nor a symbolic link, which could point out of the vault."""
    clip_files = []
    for path in sorted(clippings_dir.iterdir()):
        if path.name.startswith(".") or not path.name.endswith(CLIP_EXTENSION):
            continue
        try:
            status = path.lstat()
        except FileNotFoundError:
            # Renamed or removed since the folder was listed: its new name is listed next time.
            continue
        if stat.S_ISREG(status.st_mode):
            clip_files.append(ClipFile(path, read_changed_at_ns(status)))
    return clip_files


def read_changed_at_ns(status: os.stat_result) -> int:
    """The change date of a clip: the newest of its modification and change dates, as a copy (cp
    -p, a sync) keeps the modification date."""
    return max(status.st_mtime_ns, status.st_ctime_ns)


def read_clip_files(clip_files: list[ClipFile]) -> dict[str, bytes]:
    """The bytes of each clip by page name; a clip gone or changed since it was listed is left
    out, counted, and read by the next collection."""
    clips = {}
    gone_count = changed_count = 0
    for clip_file in clip_files:
        try:
            clip_read = read_clip_file(clip_file.path)
        except FileNotFoundError:
            gone_count += 1
            continue
        # Its date before its size: a clip rewritten while read is not measured.
        if clip_read.changed_at_ns != clip_file.changed_at_ns:
            changed_count += 1
            continue
        check_clip_size(clip_file.path, clip_read.size)
        clips[make_page_name(clip_file.path)] = clip_read.content
    if gone_count:
        logger.info(
            "clipping: %d clips gone between the list and the reading", gone_count
        )
    if changed_count:
        logger.info(
            "clipping: %d clips changed while read, left to the next collection",
            changed_count,
        )
    return clips


def make_page_name(path: Path) -> str:
    return hashlib.sha256(os.fsencode(path.name)).hexdigest()[:PAGE_NAME_LENGTH]


class ClipRead(NamedTuple):
    # At most one byte more than the maximum.
    content: bytes
    # The size of the file once read, or of the content if it grew since.
    size: int
    changed_at_ns: int


def read_clip_file(path: Path) -> ClipRead:
    """The bytes of the clip, at most one more than the maximum, with its size and change date
    once read; O_NOFOLLOW, so that a file replaced by a link is not followed."""
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as clip_file:
        content = clip_file.read(CLIP_MAX_BYTES + 1)
        status = os.fstat(clip_file.fileno())
    return ClipRead(
        content, max(len(content), status.st_size), read_changed_at_ns(status)
    )


def check_clip_size(path: Path, size: int) -> None:
    if size > CLIP_MAX_BYTES:
        raise ClipTooLargeError(
            f"clip_too_large: {quote_value(path.name)}: {size} bytes,"
            f" maximum {CLIP_MAX_BYTES} bytes"
        )


def find_rejection_reason(page_name: str, raw_clip: bytes) -> str | None:
    """Why the import would reject the clip, or None, converted as the import will."""
    try:
        for entry in read_clip(raw_clip).entries:
            convert_clip(entry, EPOCH, page_name)
    except ValidationError as refusal:
        # convert_clip gives it without its values already.
        return f"ValidationError: {refusal}"
    # RecursionError: a frontmatter nested too deep, rejected at the import as well (#103).
    except (ValueError, RecursionError) as unreadable:
        return f"{type(unreadable).__name__}: {unreadable}"
    return None


# === TRANSFORM ===

FRONTMATTER_FENCE = "---"
# A day as Web Clipper writes a property of type date: YYYY-MM-DD, the year on four digits from
# 1000, so that the base writes it as it reads it. Another form (a time, a time zone, a number)
# is refused, not guessed: pydantic alone would read a number as a Unix time.
DAY = re.compile(r"[1-9][0-9]{3}-[0-9]{2}-[0-9]{2}")
# What YAML writes for an absent value; BaseLoader reads all three as text.
ABSENT_VALUES = ("", "null", "~")
# The tag the default template of Web Clipper puts on every clip: the kind and the source of an
# item say it is a clip already (decision of Sabine of 2026-10-19).
TEMPLATE_TAG = "clippings"


class ClipEntry(NamedTuple):
    """A clip read: the properties of its frontmatter, and its text."""

    properties: dict[str, Any]
    text: str


def read_clip(raw_clip: bytes) -> EntryReading[ClipEntry]:
    """A clip as its only entry, refused if not UTF-8 or without a YAML mapping as frontmatter;
    the error says where, never what, as PyYAML quotes the text around."""
    # A byte order mark an editor adds is not part of the text, as for Obsidian; the position
    # of a byte not UTF-8 is counted from the start of the file, the mark included.
    without_mark = raw_clip.removeprefix(codecs.BOM_UTF8)
    try:
        text = without_mark.decode("utf-8")
    except UnicodeDecodeError as error:
        position = error.start + len(raw_clip) - len(without_mark)
        raise ValueError(f"clip not UTF-8: byte {position}") from None
    frontmatter, body = split_frontmatter(text)
    try:
        # BaseLoader resolves no type and builds no object: only str, list and dict. A tag such
        # as !!python/object/apply is not applied: its value is read as text or list. ruff takes
        # it for an unsafe loader (S506).
        properties = yaml.load(frontmatter, Loader=yaml.BaseLoader)  # noqa: S506
    except yaml.YAMLError as error:
        raise ValueError(describe_yaml_error(error)) from None
    if not isinstance(properties, dict):
        raise ValueError("frontmatter not a mapping")
    return EntryReading([ClipEntry(properties, body)], [])


def split_frontmatter(text: str) -> tuple[str, str]:
    """The frontmatter, between the first line and the next ---, and the text after it; a ---
    further down is a rule of the text."""
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != FRONTMATTER_FENCE:
        raise ValueError("clip without frontmatter")
    for position, line in enumerate(lines[1:], start=1):
        if line.rstrip("\r\n") == FRONTMATTER_FENCE:
            return "".join(lines[1:position]), "".join(lines[position + 1 :])
    raise ValueError("clip without frontmatter: no closing line")


def describe_yaml_error(error: yaml.YAMLError) -> str:
    mark = getattr(error, "problem_mark", None)
    if mark is None:
        return "frontmatter not YAML"
    # The line in the clip: the frontmatter starts on its second line.
    return f"frontmatter not YAML: line {mark.line + 2}, column {mark.column + 1}"


def read_absent(value: object) -> object:
    return None if value in ABSENT_VALUES else value


def check_day_form(value: object) -> object:
    """Let pydantic read the day only in the form of Web Clipper."""
    if not isinstance(value, str) or not DAY.fullmatch(value):
        raise ValueError("not a day of Web Clipper (YYYY-MM-DD)")
    return value


ClipDay = Annotated[date, BeforeValidator(check_day_form)]


def read_tags(value: object) -> object:
    """The tags as a list: a text alone is one tag, an absent value none; any other value is left
    to pydantic, which refuses it."""
    if read_absent(value) is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [element for element in value if read_absent(element) is not None]
    return value


def normalize_tags(tags: tuple[str, ...]) -> tuple[str, ...]:
    """Each tag normalized, once, sorted by name, the empty ones and the tag of the template left
    out."""
    return tuple(sorted({normalize_tag(tag) for tag in tags} - {"", TEMPLATE_TAG}))


class ClipFrontmatter(BaseModel):
    """The properties of a clip read; author (a personal data) and description are not read
    (docs/registre-traitements.md)."""

    model_config = ConfigDict(frozen=True, extra="ignore", hide_input_in_errors=True)

    title: NonEmptyText
    source: WebUrl
    created: ClipDay
    published: Annotated[ClipDay | None, BeforeValidator(read_absent)] = None
    tags: Annotated[
        tuple[str, ...], BeforeValidator(read_tags), AfterValidator(normalize_tags)
    ] = ()


def convert_clip(entry: ClipEntry, collected_at: datetime, raw_file_path: str) -> Item:
    """A clip as an item of the library, its text as written; refused, the error never quotes a
    value, as a title or a text goes into the import log."""
    try:
        frontmatter = ClipFrontmatter.model_validate(entry.properties)
        return Item(
            source_code=SOURCE_CODE,
            # The key of the base: the URL normalize_item will give the item.
            source_item_id=remove_tracking_parameters(frontmatter.source),
            kind="clip",
            title=frontmatter.title,
            url=frontmatter.source,
            published_at=at_midnight_utc(frontmatter.published)
            if frontmatter.published is not None
            else None,
            saved_at=at_midnight_utc(frontmatter.created),
            summary=entry.text,
            tags=frontmatter.tags,
            collected_at=collected_at,
            raw_file_path=raw_file_path,
        )
    except ValidationError as error:
        raise without_values(error) from None


READER: SourceReader[ClipEntry] = SourceReader(SOURCE_CODE, read_clip, convert_clip)
