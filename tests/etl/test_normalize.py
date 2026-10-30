from datetime import UTC, datetime, timedelta, timezone

from k_b.etl.normalize import normalize_item
from k_b.items import Item

FIRST_COLLECTION = datetime(2026, 10, 5, 9, 30, tzinfo=UTC)


def make_item(**changes):
    fields = {
        "source_code": "github",
        "source_item_id": "GHSA-fjqc-hq36-qh5p",
        "kind": "advisory",
        "title": "Path traversal in langchain",
        "url": "https://github.com/advisories/GHSA-fjqc-hq36-qh5p",
        "published_at": datetime(2026, 6, 25, 18, 25, 42, tzinfo=UTC),
        "package_names": ("langchain",),
        "collected_at": FIRST_COLLECTION,
        "raw_file_path": "automated_watch/github_advisories_pip/2026-10-05T093000Z.json",
    }
    fields.update(changes)
    return Item(**fields)


def test_utm_parameters_are_removed_and_the_others_kept_in_order():
    item = make_item(
        url="https://example.com/post?utm_source=tldrai&id=7&utm_medium=newsletter&page=2#part",
    )

    assert normalize_item(item).url == "https://example.com/post?id=7&page=2#part"


def test_a_query_made_only_of_utm_parameters_disappears():
    item = make_item(
        url="https://example.com/post?utm_source=tldrai",
    )

    assert normalize_item(item).url == "https://example.com/post"


def test_dates_are_brought_to_utc():
    paris = timezone(timedelta(hours=2))
    item = make_item(published_at=datetime(2026, 10, 1, 20, 0, 22, tzinfo=paris))

    published_at = normalize_item(item).published_at

    assert published_at == datetime(2026, 10, 1, 18, 0, 22, tzinfo=UTC)
    assert published_at.tzinfo == UTC
