"""The clipping source: the clips of the Obsidian vault copied as they are (E), then each read
into an item (T); the fixtures are real clips cut down, their author replaced."""

import hashlib
import logging
import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from k_b.core.config import CLIP_CHANGE_MARGIN
from k_b.etl.source_contract import PageRefusedError, check_not_all_invalid
from k_b.etl.sources import clipping
from k_b.etl.sources.clipping import (
    CLIPPINGS_DIR_NAME,
    READER,
    ClipFrontmatter,
    ClippingsFolderMissingError,
    ClippingSource,
    ClipTooLargeError,
    convert_clip,
    read_clip,
    read_clip_file,
)
from k_b.items import CLIP_MAX_BYTES

FIXTURES_DIR = Path(__file__).parents[2] / "fixtures" / "clipping"
ARTICLE = "article_with_author.md"
PAGE_WITHOUT_AUTHOR = "page_without_author_or_date.md"
PAGE_WITH_A_RULE = "page_with_a_rule_in_its_text.md"
COLLECTED_AT = datetime(2026, 10, 17, 20, 0, tzinfo=UTC)
CLIP_PATH = "selected_library/clipping/2026-10-17T200000Z/0123456789abcdef.md"
# Before every clip written by a test, and after none.
LONG_AGO = datetime(2000, 1, 1, tzinfo=UTC)
LATER = datetime(2100, 1, 1, tzinfo=UTC)


def fixture_bytes(name):
    return (FIXTURES_DIR / name).read_bytes()


def page_name_of(file_name):
    return hashlib.sha256(os.fsencode(file_name)).hexdigest()[:16]


@pytest.fixture(autouse=True)
def collection_after_the_margin(monkeypatch):
    """A collection later than the margin after the clips the tests write, unless a test says
    otherwise."""
    monkeypatch.setattr(
        clipping, "current_time", lambda: datetime.now(UTC) + CLIP_CHANGE_MARGIN * 2
    )


@pytest.fixture
def vault_dir(tmp_path):
    """A vault holding the three fixtures in its Clippings folder."""
    clippings_dir = tmp_path / "vault" / CLIPPINGS_DIR_NAME
    clippings_dir.mkdir(parents=True)
    for name in (ARTICLE, PAGE_WITHOUT_AUTHOR, PAGE_WITH_A_RULE):
        (clippings_dir / name).write_bytes(fixture_bytes(name))
    return tmp_path / "vault"


def clippings_of(vault_dir):
    return vault_dir / CLIPPINGS_DIR_NAME


def convert(raw_clip):
    return convert_clip(read_clip(raw_clip).entries[0], COLLECTED_AT, CLIP_PATH)


def with_property(raw_clip, key, value):
    """The clip with the line of the property key replaced by key: value."""
    lines = raw_clip.decode().split("\n")
    changed = [
        f"{key}: {value}" if line.startswith(f"{key}:") else line for line in lines
    ]
    return "\n".join(changed).encode()


# Collection (E).


def test_each_clip_is_copied_as_it_is_under_a_name_made_from_its_file_name(vault_dir):
    pages = ClippingSource(vault_dir).fetch(None)

    assert pages == {
        page_name_of(name): fixture_bytes(name)
        for name in (ARTICLE, PAGE_WITHOUT_AUTHOR, PAGE_WITH_A_RULE)
    }


def test_the_page_name_is_safe_and_the_same_from_one_collection_to_the_next(vault_dir):
    first = ClippingSource(vault_dir).fetch(None)
    second = ClippingSource(vault_dir).fetch(None)

    assert first.keys() == second.keys()
    assert all(len(name) == 16 and name.isalnum() for name in first)


def test_clips_are_stored_in_the_library_the_user_selected():
    source = ClippingSource(Path("anywhere"))

    assert (source.acquisition_dir_name, source.name, source.extension) == (
        "selected_library",
        "clipping",
        "md",
    )


def test_nothing_modified_since_the_last_collection_gives_nothing(vault_dir):
    assert ClippingSource(vault_dir).fetch(LATER) is None


def last_change_of(paths):
    """The collection right after the last change of the files, in whole microseconds, rounded
    up."""
    newest_ns = max(
        max(path.stat().st_mtime_ns, path.stat().st_ctime_ns) for path in paths
    )
    return datetime(1970, 1, 1, tzinfo=UTC) + timedelta(
        microseconds=newest_ns // 1000 + 1
    )


def test_only_the_clips_modified_since_the_last_collection_are_copied(vault_dir):
    unchanged = [clippings_of(vault_dir) / name for name in (ARTICLE, PAGE_WITH_A_RULE)]
    # A collection later than their last change by the margin exactly.
    last_collected_at = last_change_of(unchanged) + CLIP_CHANGE_MARGIN
    # The clock of a file system may be coarser than the one of datetime: a change made in the
    # same tick would not be seen as later.
    time.sleep(0.05)
    (clippings_of(vault_dir) / PAGE_WITHOUT_AUTHOR).write_bytes(
        fixture_bytes(PAGE_WITHOUT_AUTHOR)
    )

    pages = ClippingSource(vault_dir).fetch(last_collected_at)

    assert set(pages) == {page_name_of(PAGE_WITHOUT_AUTHOR)}


def test_a_clip_changed_within_the_margin_before_the_last_collection_is_copied_again(
    vault_dir,
):
    """The clock of the file system may lag behind the collection's: a clip written just after a
    collection began would be dated before it, and never copied."""
    clips = [clippings_of(vault_dir) / name for name in (ARTICLE,)]
    within_the_margin = last_change_of(clips) + CLIP_CHANGE_MARGIN / 2
    beyond_the_margin = last_change_of(clips) + CLIP_CHANGE_MARGIN * 2

    assert page_name_of(ARTICLE) in ClippingSource(vault_dir).fetch(within_the_margin)
    assert ClippingSource(vault_dir).fetch(beyond_the_margin) is None


def test_a_clip_copied_into_the_vault_with_an_old_modification_date_is_collected(
    vault_dir,
):
    """cp -p, the Finder or a sync keep the modification date; the change date is new."""
    old = LONG_AGO.timestamp()
    os.utime(clippings_of(vault_dir) / ARTICLE, (old, old))

    pages = ClippingSource(vault_dir).fetch(LONG_AGO + timedelta(days=1))

    assert page_name_of(ARTICLE) in pages


def test_hidden_files_other_files_folders_and_links_are_not_copied(vault_dir, tmp_path):
    clippings_dir = clippings_of(vault_dir)
    (clippings_dir / ".hidden.md").write_bytes(fixture_bytes(ARTICLE))
    (clippings_dir / "image.png").write_bytes(b"not a clip")
    (clippings_dir / "folder.md").mkdir()
    (clippings_dir / "sub").mkdir()
    (clippings_dir / "sub" / "deeper.md").write_bytes(fixture_bytes(ARTICLE))
    outside = tmp_path / "outside.md"
    outside.write_bytes(fixture_bytes(ARTICLE))
    (clippings_dir / "link.md").symlink_to(outside)

    pages = ClippingSource(vault_dir).fetch(None)

    assert set(pages) == {
        page_name_of(name) for name in (ARTICLE, PAGE_WITHOUT_AUTHOR, PAGE_WITH_A_RULE)
    }


def test_a_clip_larger_than_the_maximum_fails_the_source_naming_it(vault_dir):
    (clippings_of(vault_dir) / "Too large.md").write_bytes(
        fixture_bytes(ARTICLE) + b"x" * CLIP_MAX_BYTES
    )

    with pytest.raises(ClipTooLargeError) as failure:
        ClippingSource(vault_dir).fetch(None)

    message = str(failure.value)
    size = len(fixture_bytes(ARTICLE)) + CLIP_MAX_BYTES
    assert message == (
        f"clip_too_large: 'Too large.md': {size} bytes, maximum {CLIP_MAX_BYTES} bytes"
    )


def test_a_clip_too_large_but_changed_while_read_is_left_to_the_next_collection(
    vault_dir, monkeypatch
):
    """A sync may be rewriting it: its size is not measured, its date is."""
    (clippings_of(vault_dir) / "Too large.md").write_bytes(b"x" * (CLIP_MAX_BYTES + 1))
    listed = listed_one_nanosecond_before(vault_dir, "Too large.md")
    monkeypatch.setattr(clipping, "list_clip_files", lambda clippings_dir: listed)

    pages = ClippingSource(vault_dir).fetch(None)

    assert page_name_of("Too large.md") not in pages


def test_a_clip_of_the_maximum_size_is_copied(vault_dir):
    raw_clip = fixture_bytes(ARTICLE)
    padded = raw_clip + b"x" * (CLIP_MAX_BYTES - len(raw_clip))
    (clippings_of(vault_dir) / "Largest.md").write_bytes(padded)

    pages = ClippingSource(vault_dir).fetch(None)

    assert pages[page_name_of("Largest.md")] == padded


def test_clips_all_unreadable_fail_the_source_as_all_items_invalid(tmp_path):
    clippings_dir = tmp_path / "vault" / CLIPPINGS_DIR_NAME
    clippings_dir.mkdir(parents=True)
    (clippings_dir / "a.md").write_bytes(b"no frontmatter")
    (clippings_dir / "b.md").write_bytes(b"\xff not UTF-8")

    with pytest.raises(PageRefusedError, match="all_items_invalid: 2 entries"):
        ClippingSource(tmp_path / "vault").fetch(None)


def test_a_file_replaced_by_a_link_after_it_was_listed_is_not_followed(tmp_path):
    outside = tmp_path / "outside.md"
    outside.write_bytes(fixture_bytes(ARTICLE))
    (tmp_path / "link.md").symlink_to(outside)

    with pytest.raises(OSError):
        read_clip_file(tmp_path / "link.md")


@pytest.mark.parametrize(
    "raw_clip",
    [
        # Readable, but its title too long for the item.
        with_property(fixture_bytes(ARTICLE), "title", '"' + "x" * 1_001 + '"'),
        # Nested too deep for PyYAML: a RecursionError, as at the import (#103).
        b"---\ntitle: " + b"[" * 100_000 + b"\n---\n",
    ],
)
def test_clips_all_refused_as_items_fail_the_source_as_all_items_invalid(
    tmp_path, raw_clip
):
    clippings_dir = tmp_path / "vault" / CLIPPINGS_DIR_NAME
    clippings_dir.mkdir(parents=True)
    (clippings_dir / "a.md").write_bytes(raw_clip)

    with pytest.raises(PageRefusedError, match="all_items_invalid: 1 entries"):
        ClippingSource(tmp_path / "vault").fetch(None)


def test_an_unreadable_clip_among_valid_ones_is_copied(vault_dir):
    (clippings_of(vault_dir) / "broken.md").write_bytes(b"no frontmatter")

    pages = ClippingSource(vault_dir).fetch(None)

    assert pages[page_name_of("broken.md")] == b"no frontmatter"


@pytest.mark.parametrize("vault", ["vault without Clippings", "missing vault"])
def test_a_vault_without_its_clippings_folder_fails_without_its_path(tmp_path, vault):
    """Not taken for a vault without clips: the clips a mount brings later, with their old dates,
    would never be copied."""
    (tmp_path / "vault without Clippings").mkdir()

    with pytest.raises(ClippingsFolderMissingError) as failure:
        ClippingSource(tmp_path / vault).fetch(None)

    assert str(failure.value) == (
        "clippings_folder_missing: no Clippings folder in the vault of KB_VAULT_DIR;"
        " create it, or check KB_VAULT_DIR"
    )


def listed_one_nanosecond_before(vault_dir, name):
    """The clips as listed, the one named changed since: dated one nanosecond earlier."""
    return [
        clip_file._replace(changed_at_ns=clip_file.changed_at_ns - 1)
        if clip_file.path.name == name
        else clip_file
        for clip_file in clipping.list_clip_files(clippings_of(vault_dir))
    ]


def test_a_clip_rewritten_between_the_list_and_the_reading_is_left_to_the_next_collection(
    vault_dir, monkeypatch, caplog
):
    """A sync or an edit may be writing it: copied now, it could go cut into the base."""
    listed = listed_one_nanosecond_before(vault_dir, ARTICLE)
    monkeypatch.setattr(clipping, "list_clip_files", lambda clippings_dir: listed)

    with caplog.at_level(logging.INFO, logger="k_b.etl.sources.clipping"):
        pages = ClippingSource(vault_dir).fetch(None)

    assert set(pages) == {
        page_name_of(PAGE_WITHOUT_AUTHOR),
        page_name_of(PAGE_WITH_A_RULE),
    }
    assert (
        "clipping: 1 clips changed while read, left to the next collection"
        in caplog.messages
    )


def test_the_log_gives_a_count_and_no_title_or_file_name(vault_dir, caplog):
    with caplog.at_level(logging.INFO, logger="k_b.etl.sources.clipping"):
        ClippingSource(vault_dir).fetch(None)

    # Read, not copied: etl/extract.py writes the raw pages after.
    assert caplog.messages == ["clipping: 3 clips read"]


def make_changed_now(vault_dir, monkeypatch):
    """A collection that runs within the margin after the last change of every clip."""
    clips = list(clippings_of(vault_dir).iterdir())
    monkeypatch.setattr(
        clipping, "current_time", lambda: last_change_of(clips) + CLIP_CHANGE_MARGIN / 2
    )


def test_a_clip_changed_less_than_the_margin_ago_is_left_to_the_next_collection(
    vault_dir, monkeypatch, caplog
):
    """It may still be written: copied now, it would go cut into the base."""
    make_changed_now(vault_dir, monkeypatch)

    with caplog.at_level(logging.INFO, logger="k_b.etl.sources.clipping"):
        assert ClippingSource(vault_dir).fetch(None) is None

    assert caplog.messages == [
        "clipping: 3 clips changed less than 60 seconds ago, left to the next collection"
    ]


def test_a_clip_left_to_the_next_collection_is_copied_by_it(vault_dir, monkeypatch):
    clips = list(clippings_of(vault_dir).iterdir())
    collection = last_change_of(clips) + CLIP_CHANGE_MARGIN / 2
    monkeypatch.setattr(clipping, "current_time", lambda: collection)
    assert ClippingSource(vault_dir).fetch(None) is None

    monkeypatch.setattr(
        clipping, "current_time", lambda: collection + CLIP_CHANGE_MARGIN
    )
    pages = ClippingSource(vault_dir).fetch(collection)

    assert len(pages) == 3


def test_a_clip_gone_between_the_list_and_the_reading_is_skipped(
    vault_dir, monkeypatch, caplog
):
    listed = clipping.list_clip_files(clippings_of(vault_dir))
    (clippings_of(vault_dir) / ARTICLE).unlink()
    monkeypatch.setattr(clipping, "list_clip_files", lambda clippings_dir: listed)

    with caplog.at_level(logging.INFO, logger="k_b.etl.sources.clipping"):
        pages = ClippingSource(vault_dir).fetch(None)

    assert set(pages) == {
        page_name_of(PAGE_WITHOUT_AUTHOR),
        page_name_of(PAGE_WITH_A_RULE),
    }
    assert "clipping: 1 clips gone between the list and the reading" in caplog.messages


# Reading (T).


def test_a_clip_becomes_a_clip_item_with_its_text():
    item = convert(fixture_bytes(ARTICLE))

    assert item.model_dump(exclude_defaults=True) == {
        "source_code": "clipper",
        "source_item_id": (
            "https://medium.com/skillstuff/"
            "7-coding-patterns-i-stole-from-senior-engineers-c95f757e52a6"
        ),
        "kind": "clip",
        "title": "7 Coding Patterns I Stole From Senior Engineers",
        "url": (
            "https://medium.com/skillstuff/"
            "7-coding-patterns-i-stole-from-senior-engineers-c95f757e52a6"
        ),
        "published_at": datetime(2026, 6, 8, tzinfo=UTC),
        "saved_at": datetime(2026, 9, 17, tzinfo=UTC),
        "summary": (
            "## Most developers do not become better simply by learning more syntax.\n\n"
            "Most junior developers think senior engineers know more tricks.\n\n"
            "They usually do not.\n"
        ),
        "collected_at": COLLECTED_AT,
        "raw_file_path": CLIP_PATH,
    }
    assert READER.source_code == "clipper"


def test_an_empty_property_is_read_absent_and_the_other_parameters_of_the_url_kept():
    item = convert(fixture_bytes(PAGE_WITHOUT_AUTHOR))

    assert item.published_at is None
    assert item.authors == ()
    assert item.url == (
        "https://owasp.github.io/www-project-agentic-skills-top-10/videos/?video=ast01"
    )


@pytest.mark.parametrize("empty", ["null", "~", '""'])
def test_a_publication_date_written_null_is_read_absent(empty):
    raw_clip = with_property(fixture_bytes(ARTICLE), "published", empty)

    assert convert(raw_clip).published_at is None


def test_a_rule_in_the_text_is_kept_in_the_text():
    item = convert(fixture_bytes(PAGE_WITH_A_RULE))

    assert "\n---\n## Abstract\n" in item.summary
    assert item.title == "Web Content Accessibility Guidelines (WCAG) 2.2"


def test_the_tracking_parameters_are_removed_from_the_key():
    """A variant: no clip of the fixtures has a utm_* parameter."""
    raw_clip = with_property(
        fixture_bytes(ARTICLE),
        "source",
        '"https://www.w3.org/TR/WCAG22/?utm_source=x&lang=en"',
    )

    item = convert(raw_clip)

    assert item.source_item_id == "https://www.w3.org/TR/WCAG22/?lang=en"


def test_a_clip_that_starts_with_a_byte_order_mark_is_read():
    """Obsidian reads it; an editor may add one."""
    item = convert(b"\xef\xbb\xbf" + fixture_bytes(ARTICLE))

    assert item.title == "7 Coding Patterns I Stole From Senior Engineers"


def test_the_byte_not_utf8_is_counted_from_the_start_of_the_file_byte_order_mark_included():
    with pytest.raises(ValueError, match="clip not UTF-8: byte 5$"):
        read_clip(b"\xef\xbb\xbfab\xff")


def test_a_clip_with_crlf_line_ends_is_read():
    raw_clip = fixture_bytes(ARTICLE).replace(b"\n", b"\r\n")

    item = convert(raw_clip)

    assert item.saved_at == datetime(2026, 9, 17, tzinfo=UTC)
    assert item.summary.startswith("## Most developers")


@pytest.mark.parametrize(
    "date",
    [
        "2026-10-05T00:00:00",
        "2026-10-05T00:00:00Z",
        "1696464000",
        "20261005",
        "2026-W41-1",
        "2026-02-30",
        "0999-10-05",
        '"5 October 2026"',
        "[2026-10-05]",
    ],
)
@pytest.mark.parametrize("key", ["created", "published"])
def test_a_date_of_another_form_is_refused(key, date):
    raw_clip = with_property(fixture_bytes(ARTICLE), key, date)

    with pytest.raises(ValidationError) as refusal:
        convert(raw_clip)

    assert key in str(refusal.value)


@pytest.mark.parametrize(
    "key, value",
    [
        ("created", ""),
        ("title", ""),
        ("title", "\n  - a list"),
        ("source", ""),
        ("source", '"ftp://example.com/page"'),
        ("source", '"https://"'),
    ],
)
def test_a_clip_without_its_required_properties_is_refused(key, value):
    raw_clip = with_property(fixture_bytes(ARTICLE), key, value)

    with pytest.raises(ValidationError):
        convert(raw_clip)


def test_a_refused_clip_is_described_by_its_field_without_its_value():
    raw_clip = with_property(fixture_bytes(ARTICLE), "source", '"SECRET-not-a-url"')

    with pytest.raises(ValidationError) as refusal:
        convert(raw_clip)

    assert "SECRET" not in str(refusal.value)
    assert "source" in str(refusal.value)


def test_a_refusal_does_not_quote_the_value_received():
    with pytest.raises(ValidationError) as refusal:
        ClipFrontmatter.model_validate({"created": "RECEIVED_TEXT"})

    # Defense in depth behind without_values (core/quoting.py, #212).
    assert "RECEIVED_TEXT" not in str(refusal.value)


@pytest.mark.parametrize(
    "raw_clip, reason",
    [
        (b"\xffSECRET", "clip not UTF-8"),
        (b"SECRET without frontmatter", "clip without frontmatter"),
        (b"---\ntitle: SECRET\nno closing line", "clip without frontmatter"),
        (b"---\n---\nSECRET", "frontmatter not a mapping"),
        (b"---\n- SECRET\n---\n", "frontmatter not a mapping"),
        (b'---\ntitle: "SECRET\n---\n', "frontmatter not YAML: line"),
        (b"---\ntitle: [SECRET\n---\n", "frontmatter not YAML: line 3, column 1"),
    ],
)
def test_a_clip_that_cannot_be_read_is_unreadable_without_its_content(raw_clip, reason):
    with pytest.raises(ValueError) as unreadable:
        read_clip(raw_clip)

    assert reason in str(unreadable.value)
    assert "SECRET" not in str(unreadable.value)
    # Not chained: the traceback of the import would show the error of PyYAML, which quotes.
    assert unreadable.value.__cause__ is None
    assert unreadable.value.__context__ is None or unreadable.value.__suppress_context__


def test_a_python_tag_builds_no_object_and_its_value_is_refused_as_a_title():
    raw_clip = with_property(
        fixture_bytes(ARTICLE), "title", "!!python/object/apply:os.system [echo SECRET]"
    )

    assert read_clip(raw_clip).entries[0].properties["title"] == ["echo SECRET"]
    with pytest.raises(ValidationError):
        convert(raw_clip)


def test_a_readable_clip_is_one_entry_without_rejection():
    reading = read_clip(fixture_bytes(ARTICLE))

    assert len(reading.entries) == 1
    assert reading.rejection_reasons == []
    check_not_all_invalid(reading)


# Tags (#260).

FIXTURE_TAGS = 'tags:\n  - "clippings"\n'


def with_tags(raw_clip, tags):
    """The clip with its tags property, as the fixture writes it, replaced by tags."""
    text = raw_clip.decode()
    assert FIXTURE_TAGS in text
    return text.replace(FIXTURE_TAGS, f"{tags}\n").encode()


@pytest.mark.parametrize(
    ("tags", "read"),
    [
        ('tags:\n  - "LLM"\n  - " llm "\n  - "#llm"', ("llm",)),
        ('tags:\n  - "# llm"', ("llm",)),
        ('tags:\n  - "Security"\n  - "agents"', ("agents", "security")),
        ("tags: llm", ("llm",)),
        ("tags:", ()),
        ("tags: []", ()),
        ("tags: null", ()),
        ('tags:\n  - ""\n  - null\n  - "~"\n  - "llm"', ("llm",)),
        ('tags:\n  - "#"\n  - "  "\n  - "llm"', ("llm",)),
        ('tags:\n  - "clippings"\n  - "Clippings"\n  - "#clippings"', ()),
    ],
    ids=[
        "case_spaces_and_hash",
        "hash_then_space",
        "sorted",
        "text",
        "empty",
        "empty_list",
        "null",
        "absent_elements",
        "empty_once_normalized",
        "template_tag",
    ],
)
def test_the_tags_of_a_clip_are_read_normalized(tags, read):
    item = convert(with_tags(fixture_bytes(ARTICLE), tags))

    assert item.tags == read


def test_a_clip_without_tags_property_has_none():
    item = convert(with_tags(fixture_bytes(ARTICLE), ""))

    assert item.tags == ()


@pytest.mark.parametrize(
    "tags",
    [
        "tags:\n  - [llm, ai]",
        "tags:\n  - {llm: ai}",
        "tags: {llm: ai}",
        'tags:\n  - "' + "x" * 101 + '"',
        'tags:\n  - "SECRET\\0"',
        'tags:\n  - "##llm"',
    ],
    ids=["list", "mapping_element", "mapping", "too_long", "nul", "two_hashes"],
)
def test_a_clip_with_an_invalid_tag_is_refused_by_its_field(tags):
    with pytest.raises(ValidationError) as refusal:
        convert(with_tags(fixture_bytes(ARTICLE), tags))

    assert "tags" in str(refusal.value)
    assert "SECRET" not in str(refusal.value)


def test_a_tag_of_the_maximum_length_is_kept():
    item = convert(with_tags(fixture_bytes(ARTICLE), 'tags:\n  - "' + "x" * 100 + '"'))

    assert item.tags == ("x" * 100,)


def test_a_tag_too_long_is_refused_with_its_length_and_the_maximum():
    with pytest.raises(ValidationError) as refusal:
        convert(with_tags(fixture_bytes(ARTICLE), 'tags:\n  - "' + "x" * 101 + '"'))

    assert "tag too long: 101 characters, maximum 100" in str(refusal.value)


def listed_tags(count):
    return "tags:\n" + "\n".join(f'  - "tag{number}"' for number in range(count))


def test_a_clip_of_the_maximum_count_of_tags_is_kept():
    item = convert(with_tags(fixture_bytes(ARTICLE), listed_tags(30)))

    assert len(item.tags) == 30


def test_a_clip_with_too_many_tags_is_refused_with_its_count_and_the_maximum():
    with pytest.raises(ValidationError) as refusal:
        convert(with_tags(fixture_bytes(ARTICLE), listed_tags(31)))

    assert "tags too long: 31 elements, maximum 30" in str(refusal.value)
