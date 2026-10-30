import json
import re
import sqlite3
import typing
from datetime import UTC, datetime

import pytest
from pydantic import ConfigDict, TypeAdapter, ValidationError

from k_b.core.config import GITHUB_ECOSYSTEMS, RETENTION_YEARS_BY_ACQUISITION_MODE
from k_b.database.creation import DATABASE_SCRIPTS
from k_b.items import (
    GHSA_ID,
    HIGHLIGHT_MAX_LENGTH_BY_FIELD,
    MAX_COUNT_BY_KIND,
    MAX_INT64,
    MAX_LENGTH_BY_KIND,
    TAG_MAX_LENGTH,
    TREND_REPOSITORY_NAME_MAX_LENGTH,
    CveId,
    Highlight,
    Int64,
    ItemKind,
    NonEmptyText,
    RepositoryName,
    Tag,
    Text,
    TrendRow,
    UtcDatetime,
)

EXPECTED_TABLES = {
    "source",
    "raw_data",
    "item",
    "publication",
    "highlight",
    "trend",
    "vulnerability",
    "tracked_repository",
    "release",
    "advisory",
    "newsletter_article",
    "zotero_entry",
    "clip",
    "tag",
    "item_has_tag",
}

PAPER_COLUMNS = (
    "title, summary, authors, submitted_at, revised_at",
    (
        "'A title', 'A summary', '[\"Ada Lovelace\"]', '2026-09-25T13:53:50Z',"
        " '2026-09-25T13:53:50Z'"
    ),
)
KEV_COLUMNS = (
    "name, description, vendor, product, date_added, ransomware_use",
    (
        "'A name', 'A description', 'Apple', 'Multiple Products',"
        " '2026-09-29T00:00:00Z', 'Unknown'"
    ),
)


@pytest.fixture
def database():
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(DATABASE_SCRIPTS.joinpath("schema.sql").read_text())
    connection.executescript(DATABASE_SCRIPTS.joinpath("seed_sources.sql").read_text())
    yield connection
    connection.close()


def test_the_schema_and_the_sources_load_with_every_expected_table(database):
    tables = {
        name
        for (name,) in database.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }

    assert tables == EXPECTED_TABLES
    assert database.execute("SELECT count(*) FROM source").fetchone() == (7,)


def test_each_acquisition_mode_of_the_schema_has_a_retention_in_the_config():
    schema = DATABASE_SCRIPTS.joinpath("schema.sql").read_text()
    (allowed_values,) = re.findall(r"acquisition_mode IN \(([^)]*)\)", schema)

    assert set(re.findall(r"'(\w+)'", allowed_values)) == set(
        RETENTION_YEARS_BY_ACQUISITION_MODE
    )


def test_an_acquisition_mode_outside_the_list_is_refused(database):
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        database.execute(
            "INSERT INTO source (source_code, label, source_type, acquisition_mode)"
            " VALUES ('rss', 'RSS', 'web_service', 'archive')"
        )


def test_each_seeded_source_has_its_acquisition_mode(database):
    modes = dict(database.execute("SELECT source_code, acquisition_mode FROM source"))

    assert modes == {
        "github": "watch",
        "arxiv": "watch",
        "cisa_kev": "watch",
        "clipper": "library",
        "tldr": "watch",
        "zotero": "library",
        "gh_archive": "watch",
    }


def test_a_raw_data_of_an_unknown_source_is_refused(database):
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        database.execute(
            "INSERT INTO raw_data (source_id, raw_file_path, stream, collected_at)"
            " VALUES (99, 'automated_watch/rss/x.xml', 'rss', '2026-10-05T09:30:00Z')"
        )


@pytest.fixture
def item_id(database):
    database.execute(
        "INSERT INTO raw_data (id, source_id, raw_file_path, stream, collected_at)"
        " VALUES (1, 1, 'automated_watch/x/2026-10-05T093000Z.json', 'x',"
        " '2026-10-05T09:30:00Z')"
    )
    return database.execute(
        "INSERT INTO item (kind, raw_data_id) VALUES ('paper', 1) RETURNING id"
    ).fetchone()[0]


def test_the_kinds_of_the_schema_are_those_of_the_items():
    schema = DATABASE_SCRIPTS.joinpath("schema.sql").read_text()
    (allowed_values,) = re.findall(r"kind IN \(([^)]*)\)", schema)

    assert set(re.findall(r"'(\w+)'", allowed_values)) == set(typing.get_args(ItemKind))


def test_a_kind_outside_the_list_is_refused(database, item_id):
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        database.execute("INSERT INTO item (kind, raw_data_id) VALUES ('tweet', 1)")


def test_a_publication_and_a_vulnerability_cited_without_their_item_are_accepted(
    database,
):
    database.execute("INSERT INTO publication (arxiv_id) VALUES ('2609.31282')")
    database.execute("INSERT INTO vulnerability (cve_id) VALUES ('CVE-2026-86950')")

    assert database.execute("SELECT count(*) FROM publication").fetchone() == (1,)
    assert database.execute("SELECT count(*) FROM vulnerability").fetchone() == (1,)


def test_a_publication_with_its_item_and_its_arxiv_columns_is_accepted(
    database, item_id
):
    columns, values = PAPER_COLUMNS
    database.execute(
        f"INSERT INTO publication (arxiv_id, item_id, {columns})"  # noqa: S608 — constants
        f" VALUES ('2609.31282', ?, {values})",
        (item_id,),
    )

    assert database.execute("SELECT count(*) FROM publication").fetchone() == (1,)


def test_a_publication_without_item_but_with_a_title_is_refused(database):
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        database.execute(
            "INSERT INTO publication (arxiv_id, title) VALUES ('2609.31282', 'A title')"
        )


def test_a_publication_with_its_item_but_without_title_is_refused(database, item_id):
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        database.execute(
            "INSERT INTO publication (arxiv_id, item_id, summary, authors, submitted_at,"
            " revised_at) VALUES ('2609.31282', ?, 'A summary', '[\"Ada Lovelace\"]',"
            " '2026-09-25T13:53:50Z', '2026-09-25T13:53:50Z')",
            (item_id,),
        )


@pytest.mark.parametrize("authors", ["Ada Lovelace", '{"name": "Ada Lovelace"}', "[]"])
def test_authors_that_are_not_a_json_list_of_names_are_refused(
    database, item_id, authors
):
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        database.execute(
            "INSERT INTO publication (arxiv_id, item_id, title, summary, authors,"
            " submitted_at, revised_at) VALUES ('2609.31282', ?, 'A title', 'A summary',"
            " ?, '2026-09-25T13:53:50Z', '2026-09-25T13:53:50Z')",
            (item_id, authors),
        )


def test_two_publications_cannot_point_to_the_same_item(database, item_id):
    columns, values = PAPER_COLUMNS
    statement = (
        f"INSERT INTO publication (arxiv_id, item_id, {columns})"  # noqa: S608 — constants
        f" VALUES (?, ?, {values})"
    )
    database.execute(statement, ("2609.31282", item_id))

    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        database.execute(statement, ("2609.33401", item_id))


def test_a_vulnerability_with_its_item_and_its_kev_columns_is_accepted(
    database, item_id
):
    columns, values = KEV_COLUMNS
    database.execute(
        f"INSERT INTO vulnerability (cve_id, item_id, {columns})"  # noqa: S608 — constants
        f" VALUES ('CVE-2026-86950', ?, {values})",
        (item_id,),
    )

    assert database.execute("SELECT count(*) FROM vulnerability").fetchone() == (1,)


@pytest.mark.parametrize(
    "column, value",
    [("name", "A name"), ("vendor", "Apple"), ("revised_at", "2026-10-05T09:30:00Z")],
)
def test_a_vulnerability_without_item_but_with_a_kev_column_is_refused(
    database, column, value
):
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        database.execute(
            f"INSERT INTO vulnerability (cve_id, {column})"  # noqa: S608 — test parameters
            " VALUES ('CVE-2026-86950', ?)",
            (value,),
        )


def test_a_vulnerability_with_its_item_but_without_vendor_is_refused(database, item_id):
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        database.execute(
            "INSERT INTO vulnerability (cve_id, item_id, name, description, product,"
            " date_added, ransomware_use) VALUES ('CVE-2026-86950', ?, 'A name',"
            " 'A description', 'Multiple Products', '2026-09-29T00:00:00Z', 'Unknown')",
            (item_id,),
        )


def test_a_ransomware_use_outside_the_list_is_refused(database, item_id):
    columns, values = KEV_COLUMNS
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        database.execute(
            f"INSERT INTO vulnerability (cve_id, item_id, {columns})"  # noqa: S608 — constants
            f" VALUES ('CVE-2026-86950', ?, {values.replace('Unknown', 'Yes')})",
            (item_id,),
        )


def test_a_cve_identifier_is_unique(database):
    database.execute("INSERT INTO vulnerability (cve_id) VALUES ('CVE-2026-86950')")

    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        database.execute("INSERT INTO vulnerability (cve_id) VALUES ('CVE-2026-86950')")


def test_the_ecosystems_of_the_schema_are_those_of_the_advisories():
    schema = DATABASE_SCRIPTS.joinpath("schema.sql").read_text()
    (allowed_values,) = re.findall(r"ecosystem IN \(([^)]*)\)", schema)

    assert set(re.findall(r"'(\w+)'", allowed_values)) == set(GITHUB_ECOSYSTEMS)


INSERT_TRACKED_REPOSITORY = (
    "INSERT INTO tracked_repository (repository_name, ecosystem, packages)"
    " VALUES (?, ?, ?) RETURNING id"
)


@pytest.fixture
def tracked_repository_id(database):
    return database.execute(
        INSERT_TRACKED_REPOSITORY,
        ("anthropics/claude-code", "npm", '["@anthropic-ai/claude-code"]'),
    ).fetchone()[0]


def test_an_ecosystem_outside_the_list_is_refused(database):
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        database.execute(
            INSERT_TRACKED_REPOSITORY, ("rust-lang/rust", "cargo", '["rustc"]')
        )


@pytest.mark.parametrize("packages", ["langchain", "[]"])
def test_tracked_packages_that_are_not_a_json_list_are_refused(database, packages):
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        database.execute(
            INSERT_TRACKED_REPOSITORY, ("langchain-ai/langchain", "pip", packages)
        )


def test_a_tracked_repository_name_is_unique_whatever_its_case(
    database, tracked_repository_id
):
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        database.execute(
            INSERT_TRACKED_REPOSITORY,
            ("Anthropics/Claude-Code", "npm", '["@anthropic-ai/claude-code"]'),
        )


def insert_item(database, kind):
    database.execute(
        "INSERT OR IGNORE INTO raw_data (id, source_id, raw_file_path, stream,"
        " collected_at) VALUES (1, 1, 'automated_watch/x/2026-10-05T093000Z.json',"
        " 'x', '2026-10-05T09:30:00Z')"
    )
    return database.execute(
        "INSERT INTO item (kind, raw_data_id) VALUES (?, 1) RETURNING id", (kind,)
    ).fetchone()[0]


INSERT_RELEASE = (
    "INSERT INTO release (item_id, kind, tracked_repository_id, release_id, tag_name,"
    " name, body, published_at, url) VALUES (?, ?, ?, ?, 'v2.1.287', 'v2.1.287',"
    " '## What''s changed', '2026-10-01T18:00:22Z',"
    " 'https://github.com/anthropics/claude-code/releases/tag/v2.1.287')"
)
INSERT_ADVISORY = (
    "INSERT INTO advisory (item_id, kind, ghsa_id, vulnerability_id, summary,"
    " published_at, url, packages) VALUES (?, ?, ?, ?, 'A summary',"
    " '2026-06-25T18:25:42Z', 'https://github.com/advisories/GHSA-fjqc-hq36-qh5p', ?)"
)


def test_a_release_of_its_tracked_repository_is_accepted(
    database, tracked_repository_id
):
    item_id = insert_item(database, "release")
    database.execute(
        INSERT_RELEASE, (item_id, "release", tracked_repository_id, 401174143)
    )

    assert database.execute("SELECT count(*) FROM release").fetchone() == (1,)


def test_a_release_without_tracked_repository_is_refused(database):
    item_id = insert_item(database, "release")

    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        database.execute(INSERT_RELEASE, (item_id, "release", 99, 401174143))


def test_a_release_identifier_is_unique(database, tracked_repository_id):
    database.execute(
        INSERT_RELEASE,
        (insert_item(database, "release"), "release", tracked_repository_id, 1),
    )

    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        database.execute(
            INSERT_RELEASE,
            (insert_item(database, "release"), "release", tracked_repository_id, 1),
        )


def test_a_release_pointing_to_an_item_of_another_kind_is_refused(
    database, tracked_repository_id
):
    paper_item_id = insert_item(database, "paper")

    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        database.execute(
            INSERT_RELEASE, (paper_item_id, "release", tracked_repository_id, 1)
        )


@pytest.mark.parametrize("kind", [None, "paper"])
def test_a_release_whose_kind_is_not_release_is_refused(
    database, tracked_repository_id, kind
):
    paper_item_id = insert_item(database, "paper")

    with pytest.raises(sqlite3.IntegrityError, match="NOT NULL|CHECK"):
        database.execute(
            INSERT_RELEASE, (paper_item_id, kind, tracked_repository_id, 1)
        )


def test_an_advisory_without_vulnerability_is_accepted(database):
    item_id = insert_item(database, "advisory")
    database.execute(
        INSERT_ADVISORY,
        (item_id, "advisory", "GHSA-fjqc-hq36-qh5p", None, '["langgraph-checkpoint"]'),
    )

    assert database.execute("SELECT count(*) FROM advisory").fetchone() == (1,)


def test_an_advisory_identifier_is_unique(database):
    parameters = ("advisory", "GHSA-fjqc-hq36-qh5p", None, '["langgraph"]')
    database.execute(INSERT_ADVISORY, (insert_item(database, "advisory"), *parameters))

    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        database.execute(
            INSERT_ADVISORY, (insert_item(database, "advisory"), *parameters)
        )


@pytest.mark.parametrize("kind", [None, "paper"])
def test_an_advisory_whose_kind_is_not_advisory_is_refused(database, kind):
    paper_item_id = insert_item(database, "paper")

    with pytest.raises(sqlite3.IntegrityError, match="NOT NULL|CHECK"):
        database.execute(
            INSERT_ADVISORY,
            (paper_item_id, kind, "GHSA-fjqc-hq36-qh5p", None, '["langgraph"]'),
        )


def test_an_advisory_pointing_to_an_item_of_another_kind_is_refused(database):
    paper_item_id = insert_item(database, "paper")

    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        database.execute(
            INSERT_ADVISORY,
            (paper_item_id, "advisory", "GHSA-fjqc-hq36-qh5p", None, '["langgraph"]'),
        )


@pytest.mark.parametrize("packages", ["langgraph", '{"name": "langgraph"}', "[]"])
def test_advisory_packages_that_are_not_a_json_list_are_refused(database, packages):
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        database.execute(
            INSERT_ADVISORY,
            (
                insert_item(database, "advisory"),
                "advisory",
                "GHSA-fjqc-hq36-qh5p",
                None,
                packages,
            ),
        )


# The column each bounded field of an item is written to (database/writers/).
COLUMN_BY_KIND_AND_FIELD = {
    ("paper", "source_item_id"): ("publication", "arxiv_id"),
    ("paper", "title"): ("publication", "title"),
    ("paper", "summary"): ("publication", "summary"),
    ("paper", "authors"): ("publication", "authors"),
    ("vulnerability", "cve"): ("vulnerability", "cve_id"),
    ("vulnerability", "title"): ("vulnerability", "name"),
    ("vulnerability", "summary"): ("vulnerability", "description"),
    ("vulnerability", "vendor"): ("vulnerability", "vendor"),
    ("vulnerability", "product"): ("vulnerability", "product"),
    ("release", "tag_name"): ("release", "tag_name"),
    ("release", "release_name"): ("release", "name"),
    ("release", "summary"): ("release", "body"),
    ("release", "url"): ("release", "url"),
    ("advisory", "source_item_id"): ("advisory", "ghsa_id"),
    ("advisory", "cve"): ("vulnerability", "cve_id"),
    ("advisory", "title"): ("advisory", "summary"),
    ("advisory", "url"): ("advisory", "url"),
    ("advisory", "package_names"): ("advisory", "packages"),
    ("newsletter_article", "edition"): ("newsletter_article", "edition"),
    ("newsletter_article", "title"): ("newsletter_article", "title"),
    ("newsletter_article", "summary"): ("newsletter_article", "summary"),
    ("newsletter_article", "url"): ("newsletter_article", "url"),
    ("zotero_entry", "item_type"): ("zotero_entry", "item_type"),
    ("zotero_entry", "title"): ("zotero_entry", "title"),
    ("zotero_entry", "url"): ("zotero_entry", "url"),
    ("zotero_entry", "arxiv_id"): ("publication", "arxiv_id"),
    ("clip", "title"): ("clip", "title"),
    ("clip", "url"): ("clip", "url"),
    ("clip", "summary"): ("clip", "body"),
}


def read_table_definitions():
    """The definition of each table of schema.sql, without its comments."""
    schema = DATABASE_SCRIPTS.joinpath("schema.sql").read_text()
    statements = "\n".join(line.split("--")[0] for line in schema.splitlines())
    return dict(
        re.findall(r"CREATE TABLE (\w+) \((.*?)\) STRICT;", statements, re.DOTALL)
    )


def read_maximums_of_the_schema(check_pattern):
    return {
        (table, column): int(maximum)
        for table, definition in read_table_definitions().items()
        for column, maximum in re.findall(check_pattern, definition)
    }


def list_maximums_of_the_items(maximum_by_kind):
    """The maximum of each column, as the items hold it; two kinds that fill one column must hold
    the same."""
    maximums = {}
    for kind, maximum_by_field in maximum_by_kind.items():
        for field, maximum in maximum_by_field.items():
            column = COLUMN_BY_KIND_AND_FIELD[kind, field]
            assert maximums.setdefault(column, maximum) == maximum
    return maximums


def test_each_maximum_length_of_the_items_is_the_check_of_its_column():
    # \b: not the length( inside json_array_length(. A trend row and a highlight are not items:
    # their maximums are held by TrendRow and Highlight.
    assert read_maximums_of_the_schema(
        r"\blength\((\w+)\) <= (\d+)"
    ) == list_maximums_of_the_items(MAX_LENGTH_BY_KIND) | {
        ("trend", "repository_name"): TREND_REPOSITORY_NAME_MAX_LENGTH
    } | {
        ("highlight", field): maximum
        for field, maximum in HIGHLIGHT_MAX_LENGTH_BY_FIELD.items()
    } | {("tag", "name"): TAG_MAX_LENGTH}


def test_each_maximum_count_of_the_items_is_the_check_of_its_column():
    assert read_maximums_of_the_schema(
        r"json_array_length\((\w+)\) <= (\d+)"
    ) == list_maximums_of_the_items(MAX_COUNT_BY_KIND)


DATE = "2026-10-05T09:30:00Z"
DATETIME = datetime(2026, 10, 5, 9, 30, tzinfo=UTC)
# A day that does not exist, in the right form.
INVALID_DATE = "2026-02-30T09:30:00Z"
PAPER_ITEM_ID = 1
RELEASE_ITEM_ID = 2
ADVISORY_ITEM_ID = 3
NEWSLETTER_ARTICLE_ITEM_ID = 5
ZOTERO_ENTRY_ITEM_ID = 7
CLIP_ITEM_ID = 8

# A row every constraint of its table accepts, in a base that holds the rows of filled_database.
# The values are those of tests/fixtures (the collection), the long texts cut short; a new source
# has no collected example. The Zotero rows are those of the fabricated library.
VALID_ROW_BY_TABLE = {
    "source": {
        "source_code": "rss",
        "label": "RSS",
        "source_type": "web_service",
        "acquisition_mode": "watch",
    },
    "raw_data": {
        "source_id": 1,
        "raw_file_path": "automated_watch/cisa_kev/2026-10-05T093000Z.csv",
        "stream": "cisa_kev",
        "collected_at": DATE,
    },
    "item": {"kind": "paper", "raw_data_id": 1},
    "publication": {
        "arxiv_id": "2609.31282",
        "item_id": PAPER_ITEM_ID,
        "title": (
            "Resource-Optimized and Energy-Aware Agentic AI Framework Anchored on"
            " Blockchain for Secure Software Supply Chains"
        ),
        "summary": "This paper proposes a blockchain-backed agentic security framework",
        "authors": '["Toqeer Ali Syed", "Asadullah Abdullah Khan"]',
        "submitted_at": "2026-09-25T13:53:50Z",
        "revised_at": "2026-09-25T13:53:50Z",
    },
    "highlight": {
        "zotero_key": "ANNOTAA2",
        "text": "A highlighted sentence on the first page.",
        "comment": "A comment on it.",
        "highlighted_at": DATE,
        "revised_at": DATE,
        "withdrawn_at": DATE,
        "item_id": ZOTERO_ENTRY_ITEM_ID,
    },
    "trend": {
        "raw_data_id": 1,
        "window_end": "2026-10-10T00:00:00Z",
        "repository_id": 1209966933,
        "repository_name": "morluto/rea",
        "stars_gained": 1087,
        "forks_gained": 215,
    },
    "vulnerability": {
        "cve_id": "CVE-2026-86950",
        "item_id": PAPER_ITEM_ID,
        "name": "Apple Multiple Products Out-of-Bounds Write Vulnerability",
        "description": "Apple iOS, macOS, and iPadOS contain an out-of-bounds write",
        "vendor": "Apple",
        "product": "Multiple Products",
        "date_added": "2026-09-29T00:00:00Z",
        "ransomware_use": "Unknown",
    },
    "tracked_repository": {
        "repository_name": "langchain-ai/langchain",
        "ecosystem": "pip",
        "packages": '["langchain"]',
    },
    "release": {
        "item_id": RELEASE_ITEM_ID,
        "kind": "release",
        "tracked_repository_id": 1,
        "release_id": 401174143,
        "tag_name": "v2.1.287",
        "name": "v2.1.287",
        "body": "## What's changed",
        "published_at": "2026-10-01T18:00:22Z",
        "url": "https://github.com/anthropics/claude-code/releases/tag/v2.1.287",
    },
    "advisory": {
        "item_id": ADVISORY_ITEM_ID,
        "kind": "advisory",
        "ghsa_id": "GHSA-fjqc-hq36-qh5p",
        "summary": "LangGraph Checkpoint: Unsafe JSON deserialization in checkpoint loading",
        "published_at": "2026-06-25T18:25:42Z",
        "url": "https://github.com/advisories/GHSA-fjqc-hq36-qh5p",
        "packages": '["langgraph-checkpoint"]',
    },
    "newsletter_article": {
        "item_id": NEWSLETTER_ARTICLE_ITEM_ID,
        "kind": "newsletter_article",
        "edition": "ai",
        "edition_date": "2026-10-07T00:00:00Z",
        "rank": 2,
        "title": "Mistral Large 4 (9 minute read)",
        "summary": "Mistral Large 4 is a one-trillion-parameter multimodal model",
        "url": "https://mistral.ai/news/mistral-large-4/",
    },
    "zotero_entry": {
        "item_id": ZOTERO_ENTRY_ITEM_ID,
        "kind": "zotero_entry",
        "zotero_key": "AAAAAAA1",
        "item_type": "preprint",
        "title": "Prompt injection through tool results",
        "url": "http://arxiv.org/abs/2609.00001",
        "saved_at": "2026-10-01T08:15:00Z",
        "revised_at": DATE,
        "withdrawn_at": DATE,
    },
    # The article of tests/fixtures/clipping.
    "clip": {
        "item_id": CLIP_ITEM_ID,
        "kind": "clip",
        "url": (
            "https://medium.com/skillstuff/"
            "7-coding-patterns-i-stole-from-senior-engineers-c95f757e52a6"
        ),
        "title": "7 Coding Patterns I Stole From Senior Engineers",
        "body": "Most junior developers think senior engineers know more tricks.",
        "saved_at": "2026-09-17T00:00:00Z",
        "published_at": "2026-06-08T00:00:00Z",
    },
    "tag": {"name": "llm"},
}


def json_list(count):
    return "[" + ", ".join(['"x"'] * count) + "]"


# One refused value per CHECK of schema.sql: its table, the CHECK as SQLite names it when it
# refuses, and the change that makes a valid row of the table break this CHECK alone.
REFUSED_VALUES = [
    ("source", "source_type IN", {"source_type": "rss_feed"}),
    ("source", "acquisition_mode IN", {"acquisition_mode": "archive"}),
    ("raw_data", "collected_at IS strftime", {"collected_at": INVALID_DATE}),
    ("item", "kind IN", {"kind": "tweet"}),
    ("publication", "length(arxiv_id) <= 20", {"arxiv_id": "2" * 21}),
    ("publication", "length(title) <= 500", {"title": "x" * 501}),
    ("publication", "length(summary) <= 5000", {"summary": "x" * 5_001}),
    ("publication", "json_valid(authors)", {"authors": "[]"}),
    (
        "publication",
        "json_array_length(authors) <= 1000",
        {"authors": json_list(1_001)},
    ),
    ("publication", "submitted_at IS strftime", {"submitted_at": INVALID_DATE}),
    ("publication", "revised_at IS strftime", {"revised_at": INVALID_DATE}),
    ("publication", "(item_id IS NULL) = (title IS NULL)", {"title": None}),
    ("highlight", "zotero_key GLOB", {"zotero_key": "annotaa2"}),
    (
        "highlight",
        "text IS NOT NULL OR comment IS NOT NULL",
        {"text": None, "comment": None},
    ),
    ("highlight", "length(text) <= 10000", {"text": "x" * 10_001}),
    ("highlight", "length(comment) <= 10000", {"comment": "x" * 10_001}),
    ("highlight", "highlighted_at IS strftime", {"highlighted_at": INVALID_DATE}),
    ("highlight", "revised_at IS strftime", {"revised_at": INVALID_DATE}),
    ("highlight", "withdrawn_at IS strftime", {"withdrawn_at": INVALID_DATE}),
    # A window ends on a day: an hour of that day is refused.
    ("trend", "window_end IS strftime", {"window_end": "2026-10-10T09:30:00Z"}),
    ("trend", "repository_id > 0", {"repository_id": 0}),
    ("trend", "repository_name GLOB", {"repository_name": "rea"}),
    (
        "trend",
        "length(repository_name) <= 255",
        {"repository_name": "morluto/" + "x" * 248},
    ),
    ("trend", "stars_gained >= 0", {"stars_gained": -1}),
    ("trend", "forks_gained >= 0", {"forks_gained": -1}),
    ("vulnerability", "cve_id GLOB", {"cve_id": "CVE-26-1"}),
    (
        "vulnerability",
        "length(cve_id) <= 20",
        {"cve_id": "CVE-2026-" + "1" * 12},
    ),
    ("vulnerability", "length(name) <= 500", {"name": "x" * 501}),
    ("vulnerability", "length(description) <= 5000", {"description": "x" * 5_001}),
    ("vulnerability", "length(vendor) <= 200", {"vendor": "x" * 201}),
    ("vulnerability", "length(product) <= 500", {"product": "x" * 501}),
    ("vulnerability", "date_added IS strftime", {"date_added": INVALID_DATE}),
    ("vulnerability", "ransomware_use IN", {"ransomware_use": "Yes"}),
    ("vulnerability", "revised_at IS strftime", {"revised_at": INVALID_DATE}),
    ("vulnerability", "withdrawn_at IS strftime", {"withdrawn_at": INVALID_DATE}),
    ("vulnerability", "(item_id IS NULL) = (name IS NULL)", {"vendor": None}),
    ("tracked_repository", "repository_name GLOB", {"repository_name": "langchain"}),
    ("tracked_repository", "ecosystem IN", {"ecosystem": "cargo"}),
    ("tracked_repository", "json_valid(packages)", {"packages": "[]"}),
    ("release", "kind = 'release'", {"kind": "paper"}),
    ("release", "length(tag_name) > 0", {"tag_name": ""}),
    ("release", "length(tag_name) <= 255", {"tag_name": "x" * 256}),
    ("release", "length(name) <= 255", {"name": "x" * 256}),
    ("release", "length(body) <= 125000", {"body": "x" * 125_001}),
    ("release", "published_at IS strftime", {"published_at": INVALID_DATE}),
    ("release", "url GLOB", {"url": "http://github.com/anthropics/claude-code"}),
    ("release", "length(url) <= 2000", {"url": "https://" + "x" * 1_993}),
    ("advisory", "kind = 'advisory'", {"kind": "paper"}),
    ("advisory", "ghsa_id GLOB", {"ghsa_id": "CVE-2026-48775"}),
    ("advisory", "length(ghsa_id) <= 19", {"ghsa_id": "GHSA-fjqc-hq36-qh5pp"}),
    ("advisory", "length(summary) <= 1024", {"summary": "x" * 1_025}),
    ("advisory", "published_at IS strftime", {"published_at": INVALID_DATE}),
    ("advisory", "url GLOB", {"url": "http://github.com/advisories"}),
    ("advisory", "length(url) <= 2000", {"url": "https://" + "x" * 1_993}),
    ("advisory", "json_valid(packages)", {"packages": "[]"}),
    ("advisory", "json_array_length(packages) <= 100", {"packages": json_list(101)}),
    ("newsletter_article", "kind = 'newsletter_article'", {"kind": "paper"}),
    ("newsletter_article", "edition <> ''", {"edition": "AI"}),
    ("newsletter_article", "length(edition) <= 20", {"edition": "a" * 21}),
    ("newsletter_article", "edition_date IS strftime", {"edition_date": INVALID_DATE}),
    ("newsletter_article", "rank >= 1", {"rank": 0}),
    ("newsletter_article", "length(title) <= 500", {"title": "x" * 501}),
    ("newsletter_article", "length(summary) <= 5000", {"summary": "x" * 5_001}),
    ("newsletter_article", "url GLOB", {"url": "http://mistral.ai/news/"}),
    ("newsletter_article", "length(url) <= 2000", {"url": "https://" + "x" * 1_993}),
    ("zotero_entry", "kind = 'zotero_entry'", {"kind": "paper"}),
    ("zotero_entry", "zotero_key GLOB", {"zotero_key": "aaaaaaa1"}),
    ("zotero_entry", "length(item_type) <= 100", {"item_type": "x" * 101}),
    ("zotero_entry", "length(title) <= 1000", {"title": "x" * 1_001}),
    ("zotero_entry", "url GLOB", {"url": "ftp://arxiv.org/abs/2609.00001"}),
    ("zotero_entry", "length(url) <= 2000", {"url": "https://" + "x" * 1_993}),
    ("zotero_entry", "saved_at IS strftime", {"saved_at": INVALID_DATE}),
    ("zotero_entry", "revised_at IS strftime", {"revised_at": INVALID_DATE}),
    ("zotero_entry", "withdrawn_at IS strftime", {"withdrawn_at": INVALID_DATE}),
    ("clip", "kind = 'clip'", {"kind": "paper"}),
    ("clip", "substr(url, 1, 8)", {"url": "ftp://medium.com/skillstuff"}),
    ("clip", "length(url) <= 2000", {"url": "https://" + "x" * 1_993}),
    ("clip", "length(title) > 0", {"title": ""}),
    ("clip", "length(title) <= 1000", {"title": "x" * 1_001}),
    ("clip", "length(body) <= 1000000", {"body": "x" * 1_000_001}),
    # A year on 3 digits, as strftime writes it under Linux (database/dates.py).
    ("clip", "length(saved_at) = 20", {"saved_at": "999-09-17T00:00:00Z"}),
    ("clip", "length(published_at) = 20", {"published_at": "2026-06-08 00:00:00Z"}),
    ("clip", "length(revised_at) = 20", {"revised_at": "2026-10-18 09:30:00Z"}),
    ("tag", "length(name) > 0", {"name": ""}),
    ("tag", "length(name) <= 100", {"name": "x" * 101}),
]


@pytest.fixture
def filled_database(database):
    """The base with the rows a valid row of each table points to."""
    insert_row(
        database,
        "raw_data",
        id=1,
        raw_file_path="automated_watch/arxiv_cscr_x_csai/2026-10-05T093000Z.xml",
        stream="arxiv_cscr_x_csai",
    )
    for item_id, kind in (
        (PAPER_ITEM_ID, "paper"),
        (RELEASE_ITEM_ID, "release"),
        (ADVISORY_ITEM_ID, "advisory"),
        (NEWSLETTER_ARTICLE_ITEM_ID, "newsletter_article"),
        (ZOTERO_ENTRY_ITEM_ID, "zotero_entry"),
        (CLIP_ITEM_ID, "clip"),
    ):
        insert_row(database, "item", id=item_id, kind=kind)
    insert_row(
        database, "tracked_repository", id=1, repository_name="anthropics/claude-code"
    )
    return database


def insert_row(database, table, **changes):
    row = VALID_ROW_BY_TABLE[table] | changes
    database.execute(
        f"INSERT INTO {table} ({', '.join(row)})"  # noqa: S608 — constants of the tests
        f" VALUES ({', '.join('?' * len(row))})",
        tuple(row.values()),
    )


@pytest.mark.parametrize("table", VALID_ROW_BY_TABLE)
def test_the_valid_row_of_each_table_is_accepted(filled_database, table):
    insert_row(filled_database, table)


@pytest.mark.parametrize("table, check, changes", REFUSED_VALUES)
def test_a_value_each_check_forbids_is_refused(filled_database, table, check, changes):
    with pytest.raises(
        sqlite3.IntegrityError, match=re.escape(f"CHECK constraint failed: {check}")
    ):
        insert_row(filled_database, table, **changes)


def test_each_check_of_the_schema_has_its_refused_value():
    """A CHECK added without its refused value, or two refused values for the same CHECK, fail
    here."""
    definitions = read_table_definitions()
    checks_with_a_refused_value = {(table, check) for table, check, _ in REFUSED_VALUES}

    for table, check in checks_with_a_refused_value:
        assert definitions[table].count(f"CHECK ({check}") == 1, (table, check)
    assert len(checks_with_a_refused_value) == len(REFUSED_VALUES)
    assert len(REFUSED_VALUES) == sum(
        len(re.findall(r"\bCHECK \(", definition))
        for definition in definitions.values()
    )


# The editions the item refuses too (tests/test_items.py): the base refuses nothing the item
# accepts.
@pytest.mark.parametrize("edition", ["", "AI", "a1", "ai_old", "ai\n", "AI ai", "aé"])
def test_an_edition_that_is_not_a_slug_is_refused(filled_database, edition):
    with pytest.raises(
        sqlite3.IntegrityError,
        match=re.escape("CHECK constraint failed: edition <> ''"),
    ):
        insert_row(filled_database, "newsletter_article", edition=edition)


def insert_second_newsletter_article(database, **changes):
    insert_row(database, "newsletter_article")
    insert_row(database, "item", id=6, kind="newsletter_article")
    insert_row(database, "newsletter_article", item_id=6, **changes)


def test_an_edition_a_day_and_a_rank_are_one_newsletter_article(filled_database):
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        insert_second_newsletter_article(filled_database)


@pytest.mark.parametrize(
    "changes",
    [{"edition": "dev"}, {"edition_date": "2026-10-08T00:00:00Z"}, {"rank": 3}],
)
def test_the_same_url_is_accepted_in_another_edition_day_or_rank(
    filled_database, changes
):
    # Two editions may summarize the same article (ADR of 2026-10-07).
    insert_second_newsletter_article(filled_database, **changes)

    assert filled_database.execute(
        "SELECT count(DISTINCT url), count(*) FROM newsletter_article"
    ).fetchone() == (1, 2)


def test_a_newsletter_article_pointing_to_an_item_of_another_kind_is_refused(
    filled_database,
):
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        insert_row(filled_database, "newsletter_article", item_id=PAPER_ITEM_ID)


def test_a_clip_kept_from_an_http_page_is_accepted(filled_database):
    insert_row(filled_database, "clip", url="http://example.com/a-page-kept")


def test_a_url_is_one_clip(filled_database):
    insert_row(filled_database, "clip")
    insert_row(filled_database, "item", id=9, kind="clip")

    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        insert_row(filled_database, "clip", item_id=9)


def test_a_clip_pointing_to_an_item_of_another_kind_is_refused(filled_database):
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        insert_row(filled_database, "clip", item_id=PAPER_ITEM_ID)


def test_a_newsletter_article_without_kind_is_refused(filled_database):
    with pytest.raises(sqlite3.IntegrityError, match="NOT NULL"):
        insert_row(filled_database, "newsletter_article", kind=None)


def test_a_newsletter_article_cites_no_publication_or_a_known_one(filled_database):
    insert_row(filled_database, "newsletter_article")
    assert filled_database.execute(
        "SELECT publication_id FROM newsletter_article"
    ).fetchone() == (None,)

    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        filled_database.execute("UPDATE newsletter_article SET publication_id = 99")


def test_a_window_and_a_repository_are_one_trend_row(filled_database):
    insert_row(filled_database, "trend")

    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        insert_row(filled_database, "trend", stars_gained=1)


def test_the_same_repository_is_accepted_in_another_window(filled_database):
    insert_row(filled_database, "trend")
    insert_row(filled_database, "trend", window_end="2026-10-11T00:00:00Z")

    assert filled_database.execute("SELECT count(*) FROM trend").fetchone() == (2,)


def test_a_trend_row_of_an_unknown_raw_data_is_refused(filled_database):
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        insert_row(filled_database, "trend", raw_data_id=99)


# The fields of the valid trend row, as TrendRow holds them: its window is a day.
TREND_ROW_FIELDS = VALID_ROW_BY_TABLE["trend"] | {"window_end": "2026-10-10"}
del TREND_ROW_FIELDS["raw_data_id"]


def validate_trend_row(**changes):
    """A trend row as the raw result gives it: JSON, read in strict mode."""
    return TrendRow.model_validate_json(json.dumps(TREND_ROW_FIELDS | changes))


# Rule 4 of the conventions: what the base refuses, TrendRow refuses before. One value per
# CHECK of trend that a row of a raw result could hold, with the CHECK that refuses it.
@pytest.mark.parametrize(
    "field, value, check",
    [
        ("repository_id", 0, "repository_id > 0"),
        ("repository_name", "rea", "repository_name GLOB"),
        (
            "repository_name",
            "morluto/" + "x" * 248,
            "length(repository_name) <= 255",
        ),
        ("stars_gained", -1, "stars_gained >= 0"),
        ("forks_gained", -1, "forks_gained >= 0"),
    ],
)
def test_a_value_a_check_of_trend_refuses_is_refused_by_the_trend_row(
    filled_database, field, value, check
):
    with pytest.raises(ValidationError, match=field):
        validate_trend_row(**{field: value})
    with pytest.raises(
        sqlite3.IntegrityError, match=re.escape(f"CHECK constraint failed: {check}")
    ):
        insert_row(filled_database, "trend", **{field: value})


@pytest.mark.parametrize(
    "window_end, written",
    [
        # A day that does not exist; a time of a day.
        ("2026-02-30", "2026-02-30T00:00:00Z"),
        ("2026-10-10T09:30:00Z", "2026-10-10T09:30:00Z"),
    ],
)
def test_a_window_end_that_is_not_a_day_is_refused_by_the_trend_row_and_by_the_base(
    filled_database, window_end, written
):
    with pytest.raises(ValidationError, match="window_end"):
        validate_trend_row(window_end=window_end)
    with pytest.raises(
        sqlite3.IntegrityError,
        match=re.escape("CHECK constraint failed: window_end IS strftime"),
    ):
        insert_row(filled_database, "trend", window_end=written)


@pytest.mark.parametrize(
    "field, value",
    [
        ("repository_id", 1),
        ("repository_id", MAX_INT64),
        ("repository_name", "morluto/" + "x" * 247),
        ("stars_gained", 0),
        ("stars_gained", MAX_INT64),
        ("forks_gained", 0),
        ("forks_gained", MAX_INT64),
    ],
)
def test_a_value_at_a_bound_of_the_trend_row_is_accepted_by_the_base(
    filled_database, field, value
):
    row = validate_trend_row(**{field: value})

    insert_row(filled_database, "trend", **{field: getattr(row, field)})


@pytest.mark.parametrize("field", ["repository_id", "stars_gained", "forks_gained"])
def test_an_integer_over_64_bits_is_refused_by_the_trend_row_as_sqlite_cannot_hold_it(
    filled_database, field
):
    with pytest.raises(ValidationError, match=field):
        validate_trend_row(**{field: MAX_INT64 + 1})
    with pytest.raises(OverflowError):
        insert_row(filled_database, "trend", **{field: MAX_INT64 + 1})


# GLOB stops at the first NUL: the base would accept a name with one. The other control
# characters would reach a terminal with the name (an escape sequence). The ASCII pattern of
# RepositoryName refuses them all.
@pytest.mark.parametrize(
    "repository_name",
    [
        "\0morluto/rea",
        "mor\0luto/rea",
        "morluto/\0rea",
        "morluto/rea\0",
        "morluto/rea\x1b[31m",
        "morluto/rea\x7f",
        "morluto/rea\x9b",
    ],
)
def test_a_repository_name_with_a_control_character_is_refused_by_the_trend_row(
    repository_name,
):
    with pytest.raises(ValidationError, match="repository_name"):
        validate_trend_row(repository_name=repository_name)


# Rule 4 of the conventions: each type items.py defines once refuses a value the base
# refuses, with the CHECK or the error of sqlite3 that refuses it.
FULLWIDTH_CVE = "CVE-\uff12\uff10\uff12\uff16-1234"


def validate(shared_type, value):
    return TypeAdapter(shared_type, config=ConfigDict(strict=True)).validate_python(
        value
    )


def test_a_cve_with_fullwidth_digits_is_refused_by_its_type_and_by_the_base(
    filled_database,
):
    with pytest.raises(ValidationError):
        validate(CveId, FULLWIDTH_CVE)
    with pytest.raises(sqlite3.IntegrityError, match="cve_id GLOB"):
        insert_row(filled_database, "vulnerability", cve_id=FULLWIDTH_CVE)


def test_an_identifier_not_of_the_ghsa_form_is_refused_by_its_rule_and_by_the_base(
    filled_database,
):
    ghsa_id = "XHSA-fjqc-hq36-qh5p"

    assert re.fullmatch(GHSA_ID, ghsa_id) is None
    with pytest.raises(sqlite3.IntegrityError, match="ghsa_id GLOB"):
        insert_row(filled_database, "advisory", ghsa_id=ghsa_id)


def test_a_repository_name_without_owner_is_refused_by_its_type_and_by_the_base(
    filled_database,
):
    with pytest.raises(ValidationError):
        validate(RepositoryName, "langchain")
    with pytest.raises(sqlite3.IntegrityError, match="repository_name GLOB"):
        insert_row(filled_database, "tracked_repository", repository_name="langchain")


def test_a_date_without_time_zone_is_refused_by_its_type_and_by_the_base(
    filled_database,
):
    with pytest.raises(ValidationError):
        validate(UtcDatetime, datetime(2026, 10, 13, 22, 0))
    with pytest.raises(sqlite3.IntegrityError, match="published_at IS strftime"):
        insert_row(filled_database, "release", published_at="2026-10-13T22:00:00")


def test_a_text_that_starts_with_a_nul_is_refused_by_its_type_and_by_the_base(
    filled_database,
):
    with pytest.raises(ValidationError):
        validate(NonEmptyText, "\0v1")
    with pytest.raises(sqlite3.IntegrityError, match="length\\(tag_name\\) > 0"):
        insert_row(filled_database, "release", tag_name="\0v1")


def test_a_text_with_a_lone_surrogate_is_refused_by_its_type_and_by_the_base(
    filled_database,
):
    with pytest.raises(ValidationError):
        validate(Text, "v1\ud800")
    with pytest.raises(UnicodeEncodeError):
        insert_row(filled_database, "release", name="v1\ud800")


def test_an_integer_over_64_bits_is_refused_by_its_type_and_by_the_base(
    filled_database,
):
    with pytest.raises(ValidationError):
        validate(Int64, MAX_INT64 + 1)
    with pytest.raises(OverflowError):
        insert_row(filled_database, "newsletter_article", rank=MAX_INT64 + 1)


# The columns the source of truth of a reference table fills: the table CHECK holds one clause
# for each, "filled if and only if item_id is".
COLUMNS_FILLED_WITH_THEIR_ITEM = {
    "publication": ("title", "summary", "authors", "submitted_at", "revised_at"),
    "vulnerability": (
        "name",
        "description",
        "vendor",
        "product",
        "date_added",
        "ransomware_use",
    ),
}
# The columns of vulnerability that may be filled only with item_id, in the last clause.
COLUMNS_ALLOWED_ONLY_WITH_THEIR_ITEM = ("revised_at", "withdrawn_at")


@pytest.mark.parametrize(
    "table, column",
    [
        (table, column)
        for table, columns in COLUMNS_FILLED_WITH_THEIR_ITEM.items()
        for column in columns
    ],
)
def test_a_reference_row_with_its_item_but_without_one_of_its_columns_is_refused(
    filled_database, table, column
):
    with pytest.raises(
        sqlite3.IntegrityError,
        match=re.escape("CHECK constraint failed: (item_id IS NULL) = ("),
    ):
        insert_row(filled_database, table, **{column: None})


@pytest.mark.parametrize("column", COLUMNS_ALLOWED_ONLY_WITH_THEIR_ITEM)
def test_a_vulnerability_without_item_but_with_a_later_date_is_refused(
    filled_database, column
):
    without_item = dict.fromkeys(
        ("item_id", *COLUMNS_FILLED_WITH_THEIR_ITEM["vulnerability"])
    )

    with pytest.raises(
        sqlite3.IntegrityError,
        match=re.escape("CHECK constraint failed: (item_id IS NULL) = ("),
    ):
        insert_row(filled_database, "vulnerability", **without_item, **{column: DATE})


def test_each_clause_of_the_reference_checks_has_its_refused_value():
    """A clause added to these CHECK, or lost, without its refused value fails here."""
    definitions = read_table_definitions()

    for table, columns in COLUMNS_FILLED_WITH_THEIR_ITEM.items():
        assert re.findall(
            r"\(item_id IS NULL\) = \((\w+) IS NULL\)", definitions[table]
        ) == list(columns)
    assert re.findall(
        r"item_id IS NOT NULL OR \((\w+) IS NULL AND (\w+) IS NULL\)",
        definitions["vulnerability"],
    ) == [COLUMNS_ALLOWED_ONLY_WITH_THEIR_ITEM]


# The Zotero keys the item refuses: the base refuses them too, in both tables.
@pytest.mark.parametrize(
    "zotero_key", ["", "annotaa2", "ANNOTAA", "ANNOTAA22", "ANNOT-A2", "ANNOTA\u00c92"]
)
@pytest.mark.parametrize("table", ["highlight", "zotero_entry"])
def test_a_zotero_key_the_item_refuses_is_refused_by_the_base(
    filled_database, table, zotero_key
):
    with pytest.raises(ValidationError):
        Highlight(
            zotero_key=zotero_key,
            text="A highlighted sentence on the first page.",
            comment=None,
            highlighted_at=DATETIME,
        )
    with pytest.raises(
        sqlite3.IntegrityError,
        match=re.escape("CHECK constraint failed: zotero_key GLOB"),
    ):
        insert_row(filled_database, table, zotero_key=zotero_key)


def test_a_highlight_without_text_is_accepted(filled_database):
    insert_row(filled_database, "highlight", text=None)


def insert_tag_link(database, item_id=CLIP_ITEM_ID, tag_id=1):
    database.execute(
        "INSERT INTO item_has_tag (item_id, tag_id) VALUES (?, ?)", (item_id, tag_id)
    )


def test_a_tag_is_one_row_and_an_item_carries_it_once(filled_database):
    insert_row(filled_database, "tag", id=1)
    insert_tag_link(filled_database)

    with pytest.raises(
        sqlite3.IntegrityError, match="UNIQUE constraint failed: tag.name"
    ):
        insert_row(filled_database, "tag", id=2)
    with pytest.raises(
        sqlite3.IntegrityError, match="UNIQUE constraint failed: item_has_tag"
    ):
        insert_tag_link(filled_database)


@pytest.mark.parametrize(
    ("item_id", "tag_id"), [(99, 1), (CLIP_ITEM_ID, 99)], ids=["item", "tag"]
)
def test_a_link_to_an_unknown_item_or_tag_is_refused(filled_database, item_id, tag_id):
    insert_row(filled_database, "tag", id=1)

    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY constraint failed"):
        insert_tag_link(filled_database, item_id, tag_id)


def test_the_links_of_an_item_removed_go_with_it_and_its_tags_stay(filled_database):
    """ON DELETE CASCADE, as highlight: the purge of an item removes its links."""
    insert_row(filled_database, "tag", id=1)
    insert_tag_link(filled_database, item_id=PAPER_ITEM_ID)

    filled_database.execute("DELETE FROM item WHERE id = ?", (PAPER_ITEM_ID,))

    links = filled_database.execute("SELECT count(*) FROM item_has_tag").fetchone()
    assert links == (0,)
    assert filled_database.execute("SELECT name FROM tag").fetchall() == [("llm",)]


@pytest.mark.parametrize("name", ["", "\0llm"], ids=["empty", "nul"])
def test_a_tag_the_base_refuses_is_refused_by_its_type(filled_database, name):
    with pytest.raises(ValidationError):
        validate(Tag, name)
    with pytest.raises(sqlite3.IntegrityError, match="length\\(name\\) > 0"):
        insert_row(filled_database, "tag", name=name)
