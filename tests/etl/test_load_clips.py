"""The import of the clips: the latest version of a URL is kept, a clip whose content changed is
written again, each clip written as the collection writes it."""

import sqlite3
from pathlib import Path

import pytest
from load_helpers import dump_database, fetch_rows, read_discards, read_log, run_import

from k_b.database.creation import create_database

FIXTURES_DIR = Path(__file__).parents[1] / "fixtures" / "clipping"
ARTICLE = "article_with_author.md"
PAGE_WITHOUT_AUTHOR = "page_without_author_or_date.md"
ARTICLE_URL = (
    "https://medium.com/skillstuff/"
    "7-coding-patterns-i-stole-from-senior-engineers-c95f757e52a6"
)
FIRST = "2026-10-17T093000Z"
SECOND = "2026-10-18T093000Z"
FIRST_AT = "2026-10-17T09:30:00Z"
SECOND_AT = "2026-10-18T09:30:00Z"
CLIPPING_DIR = "selected_library/clipping"
CLIP_QUERY = (
    "SELECT url, title, body, saved_at, published_at, raw_file_path"
    " FROM clip JOIN item ON item.id = clip.item_id"
    " JOIN raw_data ON raw_data.id = item.raw_data_id ORDER BY url"
)
TAGS_QUERY = (
    "SELECT clip.url, tag.name FROM clip"
    " JOIN item_has_tag ON item_has_tag.item_id = clip.item_id"
    " JOIN tag ON tag.id = item_has_tag.tag_id ORDER BY clip.url, tag.name"
)
FIXTURE_TAGS = 'tags:\n  - "clippings"\n'
OTHER_URL = (
    "https://owasp.github.io/www-project-agentic-skills-top-10/videos/?video=ast01"
)


@pytest.fixture
def data_dir(tmp_path):
    create_database(tmp_path / "k-b.sqlite")
    return tmp_path


def fixture_text(name):
    return (FIXTURES_DIR / name).read_text()


def write_clip(data_dir, collection, page_name, text):
    """A clip copied by a collection, under its page name."""
    folder = data_dir / "raw" / CLIPPING_DIR / collection
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{page_name}.md").write_text(text)
    return f"{CLIPPING_DIR}/{collection}/{page_name}.md"


def test_a_clip_gives_an_item_and_its_clip(data_dir):
    raw_file_path = write_clip(data_dir, FIRST, "a1", fixture_text(ARTICLE))

    run_import(data_dir)

    assert fetch_rows(data_dir, CLIP_QUERY) == [
        (
            ARTICLE_URL,
            "7 Coding Patterns I Stole From Senior Engineers",
            (
                "## Most developers do not become better simply by learning more syntax.\n\n"
                "Most junior developers think senior engineers know more tricks.\n\n"
                "They usually do not.\n"
            ),
            "2026-09-17T00:00:00Z",
            "2026-06-08T00:00:00Z",
            raw_file_path,
        )
    ]
    assert fetch_rows(
        data_dir,
        "SELECT source.source_code, source.acquisition_mode, item.kind FROM item"
        " JOIN raw_data ON raw_data.id = item.raw_data_id"
        " JOIN source ON source.id = raw_data.source_id",
    ) == [("clipper", "library", "clip")]
    assert read_log(data_dir) == []


def test_a_clip_without_publication_date_has_none(data_dir):
    write_clip(data_dir, FIRST, "b2", fixture_text(PAGE_WITHOUT_AUTHOR))

    run_import(data_dir)

    assert fetch_rows(data_dir, "SELECT published_at FROM clip") == [(None,)]


def test_a_clip_copied_again_with_the_same_content_is_left_out(data_dir):
    """Its item points to the newest raw file of that content, which the order guard compares
    with."""
    write_clip(data_dir, FIRST, "a1", fixture_text(ARTICLE))
    run_import(data_dir)
    second_path = write_clip(data_dir, SECOND, "a1", fixture_text(ARTICLE))

    run_import(data_dir)

    assert [row[5] for row in fetch_rows(data_dir, CLIP_QUERY)] == [second_path]
    assert fetch_rows(data_dir, "SELECT revised_at FROM clip") == [(None,)]
    assert read_discards(data_dir) == [
        {
            "kind": "discard",
            "rule": "already_seen",
            "source_code": "clipper",
            "source_item_id": ARTICLE_URL,
            "raw_file_path": second_path,
            "compared_with_collected_at": FIRST_AT,
        }
    ]


@pytest.mark.parametrize(
    ("fixture", "kept", "changed", "column", "written"),
    [
        (
            ARTICLE,
            "They usually do not.",
            "Changed.",
            "body",
            (
                "## Most developers do not become better simply by learning more syntax.\n\n"
                "Most junior developers think senior engineers know more tricks.\n\n"
                "Changed.\n"
            ),
        ),
        (
            ARTICLE,
            'title: "7 Coding',
            'title: "Seven Coding',
            "title",
            "Seven Coding Patterns I Stole From Senior Engineers",
        ),
        (
            ARTICLE,
            "created: 2026-09-17",
            "created: 2026-09-18",
            "saved_at",
            "2026-09-18T00:00:00Z",
        ),
        (
            ARTICLE,
            "published: 2026-06-08",
            "published: 2026-06-09",
            "published_at",
            "2026-06-09T00:00:00Z",
        ),
        (ARTICLE, "published: 2026-06-08", "published:", "published_at", None),
        (
            PAGE_WITHOUT_AUTHOR,
            "published:\n",
            "published: 2026-10-01\n",
            "published_at",
            "2026-10-01T00:00:00Z",
        ),
    ],
    ids=[
        "text",
        "title",
        "created",
        "published",
        "published_removed",
        "published_added",
    ],
)
def test_a_clip_whose_content_changed_is_written_again(
    data_dir, fixture, kept, changed, column, written
):
    write_clip(data_dir, FIRST, "a1", fixture_text(fixture))
    run_import(data_dir)
    changed_text = fixture_text(fixture).replace(kept, changed)
    second_path = write_clip(data_dir, SECOND, "a1", changed_text)

    run_import(data_dir)

    (value, revised_at, raw_file_path) = fetch_rows(
        data_dir,
        f"SELECT clip.{column}, clip.revised_at, raw_data.raw_file_path FROM clip"  # noqa: S608 — constants
        " JOIN item ON item.id = clip.item_id"
        " JOIN raw_data ON raw_data.id = item.raw_data_id",
    )[0]
    assert (value, revised_at, raw_file_path) == (written, SECOND_AT, second_path)
    assert fetch_rows(data_dir, "SELECT count(*) FROM item") == [(1,)]
    assert read_log(data_dir) == []


@pytest.mark.parametrize(
    ("kept", "changed"),
    [
        ('"[[Jane Doe]]"', '"[[John Roe]]"'),
        ('simply by learning more syntax."', 'by learning syntax."'),
        ('- "clippings"', '- "Clippings"'),
    ],
    ids=["author", "description", "template_tag"],
)
def test_a_change_of_a_property_the_base_does_not_keep_is_left_out(
    data_dir, kept, changed
):
    write_clip(data_dir, FIRST, "a1", fixture_text(ARTICLE))
    run_import(data_dir)
    changed_text = fixture_text(ARTICLE).replace(kept, changed)
    assert changed_text != fixture_text(ARTICLE)
    second_path = write_clip(data_dir, SECOND, "a1", changed_text)

    run_import(data_dir)

    assert [row[5] for row in fetch_rows(data_dir, CLIP_QUERY)] == [second_path]
    assert fetch_rows(data_dir, "SELECT revised_at FROM clip") == [(None,)]
    assert [discard["rule"] for discard in read_discards(data_dir)] == ["already_seen"]


def test_an_older_version_imported_after_a_newer_one_is_left_out(data_dir):
    """A raw file collected before the version kept, imported after it (two imports at once)."""
    second_path = write_clip(
        data_dir, SECOND, "a1", fixture_text(ARTICLE).replace("do not", "never")
    )
    run_import(data_dir)
    first_path = write_clip(data_dir, FIRST, "a1", fixture_text(ARTICLE))

    run_import(data_dir)

    assert [row[5] for row in fetch_rows(data_dir, CLIP_QUERY)] == [second_path]
    assert "They usually never." in fetch_rows(data_dir, "SELECT body FROM clip")[0][0]
    assert fetch_rows(data_dir, "SELECT revised_at FROM clip") == [(None,)]
    assert read_discards(data_dir) == [
        {
            "kind": "discard",
            "rule": "no_newer_version",
            "source_code": "clipper",
            "source_item_id": ARTICLE_URL,
            "raw_file_path": first_path,
            "compared_with_collected_at": SECOND_AT,
        }
    ]


def test_two_urls_that_differ_by_their_utm_parameters_are_one_clip(data_dir):
    write_clip(data_dir, FIRST, "a1", fixture_text(ARTICLE))
    with_tracking = fixture_text(ARTICLE).replace(
        f'source: "{ARTICLE_URL}"', f'source: "{ARTICLE_URL}?utm_source=newsletter"'
    )
    write_clip(data_dir, FIRST, "a2", with_tracking)

    run_import(data_dir)

    assert fetch_rows(data_dir, "SELECT url FROM clip") == [(ARTICLE_URL,)]
    assert [discard["rule"] for discard in read_discards(data_dir)] == ["already_seen"]


def test_two_urls_are_two_clips_even_for_the_same_article(data_dir):
    """The limit of the key: no content fingerprint."""
    write_clip(data_dir, FIRST, "a1", fixture_text(ARTICLE))
    other_url = fixture_text(ARTICLE).replace(
        f'source: "{ARTICLE_URL}"', 'source: "https://example.com/the-same-article"'
    )
    write_clip(data_dir, FIRST, "a2", other_url)

    run_import(data_dir)

    assert fetch_rows(data_dir, "SELECT count(*), count(DISTINCT body) FROM clip") == [
        (2, 1)
    ]
    assert read_discards(data_dir) == []


def test_an_older_version_imported_after_a_newer_copy_of_the_same_content_is_left_out(
    data_dir,
):
    """A version of 2026-10-18 imported after a copy of 2026-10-19 of the version of 2026-10-17:
    the base keeps the content of the newest copy."""
    write_clip(data_dir, FIRST, "a1", fixture_text(ARTICLE))
    run_import(data_dir)
    write_clip(data_dir, "2026-10-19T093000Z", "a1", fixture_text(ARTICLE))
    run_import(data_dir)
    write_clip(data_dir, SECOND, "a1", fixture_text(ARTICLE).replace("do not", "never"))

    run_import(data_dir)

    assert "They usually do not." in fetch_rows(data_dir, "SELECT body FROM clip")[0][0]
    assert fetch_rows(data_dir, "SELECT revised_at FROM clip") == [(None,)]
    assert [
        (discard["rule"], discard["compared_with_collected_at"])
        for discard in read_discards(data_dir)
    ] == [("no_newer_version", "2026-10-19T09:30:00Z")]


def test_of_two_files_of_one_url_in_a_collection_the_second_is_written_again(data_dir):
    """The limit of the key: in page name order, the second writes over the first."""
    write_clip(data_dir, FIRST, "a1", fixture_text(ARTICLE))
    second_path = write_clip(
        data_dir, FIRST, "b2", fixture_text(ARTICLE).replace("do not", "never")
    )

    run_import(data_dir)

    assert [row[5] for row in fetch_rows(data_dir, CLIP_QUERY)] == [second_path]
    assert "They usually never." in fetch_rows(data_dir, "SELECT body FROM clip")[0][0]
    assert fetch_rows(data_dir, "SELECT revised_at FROM clip") == [(FIRST_AT,)]
    assert read_discards(data_dir) == []


def test_a_refused_clip_is_logged_by_its_field_without_its_title(data_dir):
    refused = fixture_text(ARTICLE).replace("created: 2026-09-17", "created: tomorrow")
    raw_file_path = write_clip(data_dir, FIRST, "a1", refused)
    write_clip(data_dir, FIRST, "b2", fixture_text(PAGE_WITHOUT_AUTHOR))

    run_import(data_dir)

    (rejection,) = read_log(data_dir)
    assert rejection["raw_file_path"] == raw_file_path
    assert "created" in rejection["reason"]
    assert "Coding Patterns" not in rejection["reason"]
    assert "Jane Doe" not in rejection["reason"]
    assert fetch_rows(data_dir, "SELECT count(*) FROM clip") == [(1,)]


def test_an_unreadable_clip_is_rejected_at_each_import_and_not_registered(data_dir):
    raw_file_path = write_clip(data_dir, FIRST, "a1", "Coding Patterns, no frontmatter")

    run_import(data_dir)
    run_import(data_dir)

    assert read_log(data_dir) == [
        {
            "kind": "rejection",
            "raw_file_path": raw_file_path,
            "reason": "ValueError: clip without frontmatter",
        }
    ]
    assert fetch_rows(data_dir, "SELECT count(*) FROM raw_data") == [(0,)]


def with_tags(text, *tags):
    """The clip with tags in place of the tag of the fixture."""
    assert FIXTURE_TAGS in text
    listed = "".join(f'  - "{tag}"\n' for tag in tags)
    return text.replace(FIXTURE_TAGS, f"tags:\n{listed}")


def test_the_tags_of_a_clip_are_in_the_base_normalized(data_dir):
    write_clip(
        data_dir,
        FIRST,
        "a1",
        with_tags(fixture_text(ARTICLE), "LLM", " llm ", "#llm", "Agents", "clippings"),
    )

    run_import(data_dir)

    assert fetch_rows(data_dir, TAGS_QUERY) == [
        (ARTICLE_URL, "agents"),
        (ARTICLE_URL, "llm"),
    ]
    assert fetch_rows(data_dir, "SELECT name FROM tag ORDER BY name") == [
        ("agents",),
        ("llm",),
    ]


def test_a_tag_carried_by_two_clips_is_one_row(data_dir):
    write_clip(data_dir, FIRST, "a1", with_tags(fixture_text(ARTICLE), "llm"))
    write_clip(
        data_dir, FIRST, "b2", with_tags(fixture_text(PAGE_WITHOUT_AUTHOR), "LLM")
    )

    run_import(data_dir)

    assert fetch_rows(data_dir, "SELECT count(*) FROM tag") == [(1,)]
    assert fetch_rows(data_dir, TAGS_QUERY) == [
        (ARTICLE_URL, "llm"),
        (OTHER_URL, "llm"),
    ]


def test_a_clip_without_other_tag_than_the_template_has_none(data_dir):
    write_clip(data_dir, FIRST, "a1", fixture_text(ARTICLE))

    run_import(data_dir)

    assert fetch_rows(data_dir, "SELECT count(*) FROM tag") == [(0,)]
    assert fetch_rows(data_dir, "SELECT count(*) FROM item_has_tag") == [(0,)]


def test_a_clip_copied_again_with_its_tags_in_another_form_is_left_out(data_dir):
    write_clip(data_dir, FIRST, "a1", with_tags(fixture_text(ARTICLE), "llm", "agents"))
    run_import(data_dir)
    write_clip(
        data_dir, SECOND, "a1", with_tags(fixture_text(ARTICLE), "#Agents", "LLM")
    )

    run_import(data_dir)

    assert fetch_rows(data_dir, "SELECT revised_at FROM clip") == [(None,)]
    assert [discard["rule"] for discard in read_discards(data_dir)] == ["already_seen"]


def test_a_tag_added_or_removed_writes_the_clip_again(data_dir):
    """The links of the version before are removed."""
    write_clip(data_dir, FIRST, "a1", with_tags(fixture_text(ARTICLE), "llm"))
    run_import(data_dir)
    write_clip(
        data_dir, SECOND, "a1", with_tags(fixture_text(ARTICLE), "llm", "agents")
    )
    run_import(data_dir)
    added = (
        fetch_rows(data_dir, TAGS_QUERY),
        fetch_rows(data_dir, "SELECT revised_at FROM clip"),
    )
    third_path = write_clip(
        data_dir, "2026-10-19T093000Z", "a1", with_tags(fixture_text(ARTICLE), "agents")
    )

    run_import(data_dir)

    assert added == ([(ARTICLE_URL, "agents"), (ARTICLE_URL, "llm")], [(SECOND_AT,)])
    assert fetch_rows(data_dir, TAGS_QUERY) == [(ARTICLE_URL, "agents")]
    assert fetch_rows(data_dir, "SELECT revised_at FROM clip") == [
        ("2026-10-19T09:30:00Z",)
    ]
    assert [row[5] for row in fetch_rows(data_dir, CLIP_QUERY)] == [third_path]
    assert read_log(data_dir) == []


# The columns of clip a rewrite may write.
CLIP_COLUMNS = ("title", "body", "saved_at", "published_at", "revised_at")
LINKS_QUERY = (
    "SELECT tag.name, item_has_tag.rowid FROM item_has_tag"
    " JOIN tag ON tag.id = item_has_tag.tag_id ORDER BY tag.name"
)


def watch_written_columns(data_dir):
    """A trigger per column of clip, in the test base only: it fires when the column is in the
    SET of an UPDATE, changed or not."""
    connection = sqlite3.connect(data_dir / "k-b.sqlite")
    try:
        connection.execute("CREATE TABLE written_column (name TEXT)")
        for column in CLIP_COLUMNS:
            connection.execute(
                f"CREATE TRIGGER clip_{column}_written AFTER UPDATE OF {column} ON clip"  # noqa: S608 — constants
                f" BEGIN INSERT INTO written_column VALUES ('{column}'); END"
            )
        connection.commit()
    finally:
        connection.close()


def read_written_columns(data_dir):
    return sorted(
        name for (name,) in fetch_rows(data_dir, "SELECT name FROM written_column")
    )


def test_a_title_changed_alone_writes_only_the_title(data_dir):
    """A patch, not a put."""
    write_clip(data_dir, FIRST, "a1", with_tags(fixture_text(ARTICLE), "llm"))
    run_import(data_dir)
    links = fetch_rows(data_dir, LINKS_QUERY)
    watch_written_columns(data_dir)
    changed = with_tags(fixture_text(ARTICLE), "llm").replace(
        'title: "7 Coding', 'title: "Seven Coding'
    )
    write_clip(data_dir, SECOND, "a1", changed)

    run_import(data_dir)

    assert read_written_columns(data_dir) == ["revised_at", "title"]
    assert fetch_rows(data_dir, LINKS_QUERY) == links


def test_a_tag_added_writes_only_its_link(data_dir):
    write_clip(data_dir, FIRST, "a1", with_tags(fixture_text(ARTICLE), "llm"))
    run_import(data_dir)
    [(_, llm_rowid)] = fetch_rows(data_dir, LINKS_QUERY)
    watch_written_columns(data_dir)
    write_clip(
        data_dir, SECOND, "a1", with_tags(fixture_text(ARTICLE), "llm", "agents")
    )

    run_import(data_dir)

    assert read_written_columns(data_dir) == ["revised_at"]
    links = dict(fetch_rows(data_dir, LINKS_QUERY))
    assert sorted(links) == ["agents", "llm"]
    assert links["llm"] == llm_rowid


def test_a_tag_removed_removes_only_its_link(data_dir):
    write_clip(data_dir, FIRST, "a1", with_tags(fixture_text(ARTICLE), "llm", "agents"))
    run_import(data_dir)
    agents_rowid = dict(fetch_rows(data_dir, LINKS_QUERY))["agents"]
    watch_written_columns(data_dir)
    write_clip(data_dir, SECOND, "a1", with_tags(fixture_text(ARTICLE), "agents"))

    run_import(data_dir)

    assert read_written_columns(data_dir) == ["revised_at"]
    assert fetch_rows(data_dir, LINKS_QUERY) == [("agents", agents_rowid)]


def test_the_last_link_of_a_tag_removed_removes_the_tag(data_dir):
    """A tag removed no longer exists in the base."""
    write_clip(data_dir, FIRST, "a1", with_tags(fixture_text(ARTICLE), "llm", "agents"))
    run_import(data_dir)
    write_clip(data_dir, SECOND, "a1", with_tags(fixture_text(ARTICLE), "agents"))

    run_import(data_dir)

    assert fetch_rows(data_dir, "SELECT name FROM tag") == [("agents",)]


def test_a_tag_another_clip_still_carries_stays(data_dir):
    write_clip(data_dir, FIRST, "a1", with_tags(fixture_text(ARTICLE), "llm"))
    write_clip(
        data_dir, FIRST, "b2", with_tags(fixture_text(PAGE_WITHOUT_AUTHOR), "llm")
    )
    run_import(data_dir)
    write_clip(data_dir, SECOND, "a1", with_tags(fixture_text(ARTICLE), "agents"))

    run_import(data_dir)

    assert fetch_rows(data_dir, "SELECT name FROM tag ORDER BY name") == [
        ("agents",),
        ("llm",),
    ]
    assert fetch_rows(data_dir, TAGS_QUERY) == [
        (ARTICLE_URL, "agents"),
        (OTHER_URL, "llm"),
    ]


def test_an_identical_clip_changes_nothing(data_dir):
    write_clip(data_dir, FIRST, "a1", with_tags(fixture_text(ARTICLE), "llm"))
    run_import(data_dir)
    links = fetch_rows(data_dir, LINKS_QUERY)
    watch_written_columns(data_dir)
    write_clip(data_dir, SECOND, "a1", with_tags(fixture_text(ARTICLE), "LLM"))

    run_import(data_dir)

    assert read_written_columns(data_dir) == []
    assert fetch_rows(data_dir, LINKS_QUERY) == links
    assert fetch_rows(data_dir, "SELECT revised_at FROM clip") == [(None,)]


def test_a_rebuilt_base_is_the_same(data_dir, tmp_path):
    write_clip(data_dir, FIRST, "a1", with_tags(fixture_text(ARTICLE), "llm"))
    write_clip(data_dir, SECOND, "a1", fixture_text(ARTICLE).replace("do not", "never"))
    write_clip(data_dir, SECOND, "b2", fixture_text(PAGE_WITHOUT_AUTHOR))
    run_import(data_dir)
    first_dump = dump_database(data_dir)

    (data_dir / "k-b.sqlite").unlink()
    create_database(data_dir / "k-b.sqlite")
    run_import(data_dir)

    assert dump_database(data_dir) == first_dump
