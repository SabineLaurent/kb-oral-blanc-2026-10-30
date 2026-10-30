"""The load writes each raw file into the base: an item and the row of its kind, at their maximums too."""

import json

import pytest
from load_helpers import (
    FIRST_CVE_ID,
    FIRST_PAPER_ID,
    FIRST_RELEASE_ID,
    KEV_QUERY,
    PAPER_QUERY,
    count_rows,
    fetch_rows,
    read_log,
    run_import,
)
from test_items import (
    COLLECTED_AT,
    RAW_FILE_PATH,
    advisory_fields,
    item_fields,
    newsletter_article_fields,
    release_fields,
    text_of_length,
    vulnerability_fields,
)
from test_transform import FIXTURES_DIR

from k_b.core.config import GITHUB_TRACKED_REPOSITORIES
from k_b.database.creation import create_database
from k_b.etl.source_contract import EntryReading, SourceKind, SourceReader
from k_b.items import (
    MAX_COUNT_BY_KIND,
    MAX_ELEMENT_LENGTH_BY_KIND,
    MAX_LENGTH_BY_KIND,
    Item,
)

ADVISORY_WITH_TWO_PACKAGES = "GHSA-gr75-jv2w-4656"


def test_the_raw_files_go_into_the_database(data_dir):
    summary = run_import(data_dir)

    assert summary.item_count == 3 + 4 + 3 + 3
    assert count_rows(data_dir, "SELECT count(*) FROM item") == summary.item_count
    assert count_rows(data_dir, "SELECT count(*) FROM raw_data") == 4
    assert count_rows(data_dir, "SELECT count(*) FROM publication") == 3
    # The 4 CVE of CISA KEV, and the 3 CVE the advisories cite.
    assert count_rows(data_dir, "SELECT count(*) FROM vulnerability") == 4 + 3
    assert count_rows(data_dir, "SELECT count(*) FROM release") == 3
    assert count_rows(data_dir, "SELECT count(*) FROM advisory") == 3


def test_an_arxiv_item_gives_an_item_and_its_publication(data_dir):
    run_import(data_dir)

    ((_, kind, raw_file_path, title, authors, submitted_at, revised_at),) = fetch_rows(
        data_dir, PAPER_QUERY, (FIRST_PAPER_ID,)
    )
    assert kind == "paper"
    assert raw_file_path == "automated_watch/arxiv_cscr_x_csai/2026-10-05T093000Z.xml"
    assert title.startswith("Resource-Optimized and Energy-Aware Agentic AI")
    assert json.loads(authors) == ["Toqeer Ali Syed", "Asadullah Abdullah Khan"]
    assert submitted_at == revised_at == "2026-09-25T13:53:50Z"


def test_a_cisa_row_gives_an_item_and_its_vulnerability(data_dir):
    run_import(data_dir)

    assert fetch_rows(
        data_dir,
        "SELECT item.kind, name, vendor, product, date_added, ransomware_use,"
        " revised_at, withdrawn_at, substr(description, 1, 25)"
        " FROM vulnerability JOIN item ON item.id = vulnerability.item_id"
        " WHERE cve_id = ?",
        (FIRST_CVE_ID,),
    ) == [
        (
            "vulnerability",
            "Apple Multiple Products Out-of-Bounds Write Vulnerability",
            "Apple",
            "Multiple Products",
            "2026-09-29T00:00:00Z",
            "Unknown",
            None,
            None,
            "Apple iOS, macOS, and iPa",
        )
    ]


def test_an_item_points_to_the_raw_file_and_date_of_its_collection(data_dir):
    run_import(data_dir)

    rows = fetch_rows(
        data_dir,
        "SELECT DISTINCT collected_at, raw_file_path, stream, source.source_code"
        " FROM item JOIN raw_data ON raw_data.id = item.raw_data_id"
        " JOIN source ON source.id = raw_data.source_id"
        " WHERE item.kind = 'vulnerability'",
    )

    assert rows == [
        (
            "2026-10-05T09:30:00Z",
            "automated_watch/cisa_kev/2026-10-05T093000Z.csv",
            "cisa_kev",
            "cisa_kev",
        )
    ]


def test_the_tracked_repositories_of_the_code_fill_their_table(data_dir):
    run_import(data_dir)

    rows = fetch_rows(
        data_dir,
        "SELECT repository_name, ecosystem, packages FROM tracked_repository ORDER BY id",
    )
    assert [
        (name, ecosystem, tuple(json.loads(packages)))
        for name, ecosystem, packages in rows
    ] == [
        (repository.name, repository.ecosystem, repository.packages)
        for repository in GITHUB_TRACKED_REPOSITORIES
    ]


def test_a_release_gives_an_item_and_its_release_of_its_tracked_repository(data_dir):
    run_import(data_dir)

    assert fetch_rows(
        data_dir,
        "SELECT item.kind, release.kind, repository_name, tag_name, name, published_at,"
        " url, substr(body, 1, 17), raw_file_path"
        " FROM release JOIN item ON item.id = release.item_id"
        " JOIN raw_data ON raw_data.id = item.raw_data_id"
        " JOIN tracked_repository ON tracked_repository.id = release.tracked_repository_id"
        " WHERE release_id = ?",
        (FIRST_RELEASE_ID,),
    ) == [
        (
            "release",
            "release",
            "anthropics/claude-code",
            "v2.1.287",
            "v2.1.287",
            "2026-10-01T18:00:22Z",
            "https://github.com/anthropics/claude-code/releases/tag/v2.1.287",
            "## What's changed",
            "automated_watch/github_releases_anthropics_claude_code/2026-10-05T093000Z.json",
        )
    ]


def test_an_advisory_citing_an_absent_cve_creates_it_without_kev_columns(data_dir):
    run_import(data_dir)

    assert fetch_rows(
        data_dir,
        "SELECT item.kind, cve_id, vulnerability.item_id, name, vendor, date_added,"
        " packages, advisory.url FROM advisory"
        " JOIN item ON item.id = advisory.item_id"
        " JOIN vulnerability ON vulnerability.id = advisory.vulnerability_id"
        " WHERE ghsa_id = ?",
        (ADVISORY_WITH_TWO_PACKAGES,),
    ) == [
        (
            "advisory",
            "CVE-2026-55443",
            None,
            None,
            None,
            None,
            '["langchain", "langchain-anthropic"]',
            f"https://github.com/advisories/{ADVISORY_WITH_TWO_PACKAGES}",
        )
    ]


def test_the_packages_of_an_advisory_are_kept_once_each(data_dir):
    run_import(data_dir)

    ((packages,),) = fetch_rows(
        data_dir,
        "SELECT packages FROM advisory WHERE ghsa_id = 'GHSA-3644-q5cj-c5c7'",
    )
    assert json.loads(packages) == ["langsmith", "langchain-classic", "langchain"]


@pytest.mark.parametrize(
    "advisory_collection", ["2026-10-04T093000Z", "2026-10-06T093000Z"]
)
def test_an_advisory_and_a_kev_row_of_the_same_cve_share_one_vulnerability(
    data_dir, advisory_collection
):
    """The advisory collected before CISA KEV, then after it."""
    advisories = json.loads(
        (FIXTURES_DIR / "github_advisories_extract.json").read_text()
    )
    advisories[0]["cve_id"] = FIRST_CVE_ID
    (data_dir / "raw/automated_watch/github_advisories_pip").joinpath(
        f"{advisory_collection}.json"
    ).write_text(json.dumps([advisories[0]]))
    # The recorded file would give the same advisory with its own CVE: removed.
    (
        data_dir / "raw/automated_watch/github_advisories_pip/2026-10-05T093000Z.json"
    ).unlink()

    run_import(data_dir)

    assert fetch_rows(
        data_dir,
        "SELECT vulnerability.id = advisory.vulnerability_id, item.kind, vendor"
        " FROM vulnerability JOIN item ON item.id = vulnerability.item_id"
        " JOIN advisory ON advisory.vulnerability_id = vulnerability.id"
        " WHERE cve_id = ?",
        (FIRST_CVE_ID,),
    ) == [(1, "vulnerability", "Apple")]
    assert fetch_rows(data_dir, KEV_QUERY, (FIRST_CVE_ID,))[0][2] is None
    assert (
        count_rows(
            data_dir,
            f"SELECT count(*) FROM vulnerability WHERE cve_id = '{FIRST_CVE_ID}'",  # noqa: S608 — constant
        )
        == 1
    )


def test_a_release_of_a_repository_no_longer_tracked_is_rejected_and_logged(data_dir):
    still_tracked = tuple(
        repository
        for repository in GITHUB_TRACKED_REPOSITORIES
        if repository.name != "anthropics/claude-code"
    )

    summary = run_import(data_dir, still_tracked)

    rejections = [line for line in read_log(data_dir) if line["kind"] == "rejection"]
    assert summary.rejection_count == len(rejections) == 3
    assert rejections[0] == {
        "kind": "rejection",
        "raw_file_path": (
            "automated_watch/github_releases_anthropics_claude_code/2026-10-05T093000Z.json"
        ),
        "reason": "repository not in the tracked list: anthropics/claude-code",
    }
    assert summary.item_count == 3 + 4 + 3
    assert count_rows(data_dir, "SELECT count(*) FROM release") == 0


def text_at_its_maximum(kind, field, maximum):
    """A text of the maximum length that names its kind and its field, so that a writer taking
    one field for another is seen."""
    if field in ("cve", "edition") or (kind, field) == ("advisory", "source_item_id"):
        return text_of_length(field, maximum, kind)
    prefix = {"url": "https://"}.get(field, "")
    repeated = f"{kind}.{field}-" * maximum
    return (prefix + repeated)[:maximum]


def build_item_at_its_maximums(build_fields, collected_at, raw_file_path):
    """An item of the kind of build_fields, each bounded text and list at its maximum."""
    kind = build_fields()["kind"]
    fields = {
        field: text_at_its_maximum(kind, field, maximum)
        for field, maximum in MAX_LENGTH_BY_KIND[kind].items()
    }
    for field, maximum_count in MAX_COUNT_BY_KIND.get(kind, {}).items():
        fields[field] = ("x" * MAX_ELEMENT_LENGTH_BY_KIND[kind][field],) * maximum_count
    return Item(
        **build_fields(collected_at=collected_at, raw_file_path=raw_file_path, **fields)
    )


def test_an_item_with_every_text_and_list_at_its_maximum_goes_into_the_database(
    tmp_path,
):
    source_dir = tmp_path / "raw" / "automated_watch" / "fake"
    source_dir.mkdir(parents=True)
    (source_dir / "2026-10-05T093000Z.txt").write_text("")
    create_database(tmp_path / "k-b.sqlite")
    builders = [
        item_fields,
        vulnerability_fields,
        release_fields,
        advisory_fields,
        newsletter_article_fields,
    ]
    kinds = [
        SourceKind(
            "fake",
            (),
            SourceReader(
                "arxiv",
                lambda raw: EntryReading(builders, []),
                build_item_at_its_maximums,
            ),
        )
    ]

    summary = run_import(tmp_path, kinds=kinds)

    assert summary == (5, 0, 0, 1, 0, 0)
    paper, vulnerability, release, advisory, newsletter_article = (
        build_item_at_its_maximums(build_fields, COLLECTED_AT, RAW_FILE_PATH)
        for build_fields in builders
    )
    assert fetch_rows(
        tmp_path, "SELECT arxiv_id, title, summary, authors FROM publication"
    ) == [
        (
            paper.source_item_id,
            paper.title,
            paper.summary,
            json.dumps(list(paper.authors)),
        )
    ]
    assert fetch_rows(
        tmp_path, "SELECT cve_id, name, description, vendor, product FROM vulnerability"
    ) == [
        (
            vulnerability.cve,
            vulnerability.title,
            vulnerability.summary,
            vulnerability.vendor,
            vulnerability.product,
        )
    ]
    assert fetch_rows(tmp_path, "SELECT tag_name, name, body, url FROM release") == [
        (release.tag_name, release.release_name, release.summary, release.url)
    ]
    assert fetch_rows(
        tmp_path, "SELECT ghsa_id, summary, url, packages FROM advisory"
    ) == [
        (
            advisory.source_item_id,
            advisory.title,
            advisory.url,
            json.dumps(list(advisory.package_names)),
        )
    ]
    assert fetch_rows(
        tmp_path, "SELECT edition, title, summary, url FROM newsletter_article"
    ) == [
        (
            newsletter_article.edition,
            newsletter_article.title,
            newsletter_article.summary,
            newsletter_article.url,
        )
    ]
