"""The items of the watch as the model will receive them: the fields of the whitelist, masked,
then cut for the writing."""

from datetime import UTC, datetime, timedelta

import pytest

from k_b.ai_api.watch_items import (
    NewsletterArticleFields,
    WatchItem,
    build_fields,
    select_for_writing,
)
from k_b.core.config import MAX_FIELD_CHARS_FOR_WRITING, MAX_ITEMS_PER_DIGEST

# The body of release 6 of the test base (github_releases_extract.json); VARIANT: a @login
# added after its first line, as GitHub writes a thanks to a contributor.
RELEASE_BODY = (
    "## What's changed\n\n"
    "- Added Claude Mods: plugins may now modify deeper behavior, thanks to @jane-doe42\n"
    "- Added You should know, a built-in mod where a side agent watches your back"
)
RELEASE_VALUES = {
    "repository_name": "anthropics/claude-code",
    "tag_name": "v2.1.287",
    "title": "anthropics/claude-code Claude Code 2.1.287",
    "body": RELEASE_BODY,
    "url": "https://github.com/anthropics/claude-code/releases/tag/v2.1.287",
}


@pytest.mark.parametrize(
    ("summary", "masked"),
    [
        ("Read https://mistral.ai/news/mistral-large-4/ now", "Read [url] now"),
        ("Read HTTPS://Example.org/a?b=c now", "Read [url] now"),
        ("Read www.jmeiners.com/lc3-vm/ now", "Read [url] now"),
        ("Read javascript://x%0Aalert(1) now", "Read [url] now"),
        # A domain without scheme: neither masked nor refused (limit of the spec).
        ("Read example.com/x now", "Read example.com/x now"),
    ],
)
def test_every_url_of_a_field_sent_is_masked(summary, masked):
    fields = build_fields(
        "newsletter_article",
        {"edition": "ai", "title": "Mistral Large 4", "summary": summary},
    )

    assert fields == NewsletterArticleFields(
        edition="ai", title="Mistral Large 4", summary=masked
    )


def test_the_fields_out_of_the_whitelist_are_not_kept():
    fields = build_fields(
        "paper",
        {
            "title": "Evaluating System One Models for Agent Security Decisions",
            "summary": "Model-based judges support agent security.",
            "authors": ["Yixuan Liu"],
            "arxiv_id": "2609.33401",
            "url": "https://arxiv.org/abs/2609.33401",
        },
    )

    assert fields.model_dump() == {
        "title": "Evaluating System One Models for Agent Security Decisions",
        "summary": "Model-based judges support agent security.",
    }


def test_a_login_of_a_release_body_is_masked():
    fields = build_fields("release", RELEASE_VALUES)

    assert fields.model_dump() == {
        "repository_name": "anthropics/claude-code",
        "tag_name": "v2.1.287",
        "title": "anthropics/claude-code Claude Code 2.1.287",
        "body": RELEASE_BODY.replace("@jane-doe42", "@user"),
    }


@pytest.mark.parametrize(
    ("body", "masked"),
    [
        # The URL first: its @ is gone with it.
        ("See https://jane@github.com/a by @jane", "See [url] by @user"),
        ("(@jane) and @jane-doe, @jane.", "(@user) and @user, @user."),
        # An address is left: the spec masks a @login (limit written in the module).
        ("Write to jane.doe@example.com", "Write to jane.doe@example.com"),
        # An npm scope is masked as a login: masking too much is the safe way.
        ("npm i @anthropic-ai/claude-code", "npm i @user/claude-code"),
    ],
)
def test_the_masking_of_a_release_body(body, masked):
    fields = build_fields("release", RELEASE_VALUES | {"body": body})

    assert fields.model_dump()["body"] == masked


def test_a_release_without_body_keeps_a_null_body():
    fields = build_fields("release", RELEASE_VALUES | {"body": None})

    assert fields.model_dump()["body"] is None


def test_a_login_out_of_a_release_body_is_kept():
    fields = build_fields(
        "newsletter_article",
        {"edition": "ai", "title": "Mistral Large 4", "summary": "said @mistralai"},
    )

    assert fields.model_dump()["summary"] == "said @mistralai"


def test_the_advisory_sends_exploited_under_its_name_for_the_model():
    fields = build_fields(
        "advisory",
        {
            "ghsa_id": "GHSA-fjqc-hq36-qh5p",
            "cve_id": "CVE-2020-29583",
            "title": "LangGraph Checkpoint: Unsafe JSON deserialization",
            "cve_listed_in_cisa_kev": True,
            "packages": ["langgraph-checkpoint"],
        },
    )

    assert fields.model_dump()["cve_listed_in_cisa_kev"] is True


NOW = datetime(2026, 10, 15, 9, 0, tzinfo=UTC)


def make_item(item_id, hours_ago, summary="A summary."):
    return WatchItem(
        item_id=item_id,
        kind="newsletter_article",
        source_date=NOW - timedelta(hours=hours_ago),
        url=f"https://example.org/{item_id}",
        source_title=f"Article {item_id}",
        fields=NewsletterArticleFields(
            edition="ai", title=f"Article {item_id}", summary=summary
        ),
    )


def test_the_writing_keeps_the_most_recent_items_and_counts_the_others():
    items = [make_item(item_id, hours_ago=item_id) for item_id in range(1, 66)]

    selection = select_for_writing(reversed(items))

    assert MAX_ITEMS_PER_DIGEST == 60
    assert [item.item_id for item in selection.items] == list(range(1, 61))
    assert selection.items_dropped == 5
    assert selection.items_truncated == 0


def test_items_of_the_same_date_are_ordered_by_id():
    selection = select_for_writing([make_item(3, 0), make_item(7, 0), make_item(5, 0)])

    assert [item.item_id for item in selection.items] == [7, 5, 3]


def test_the_writing_cuts_each_field_and_counts_the_items_cut():
    long_summary = "a" * (MAX_FIELD_CHARS_FOR_WRITING + 1)
    items = [make_item(1, 0, long_summary), make_item(2, 1, "a" * 2000)]

    selection = select_for_writing(items)

    assert MAX_FIELD_CHARS_FOR_WRITING == 2000
    first, second = selection.items
    assert isinstance(first.fields, NewsletterArticleFields)
    assert first.fields.summary == "a" * MAX_FIELD_CHARS_FOR_WRITING
    assert isinstance(second.fields, NewsletterArticleFields)
    assert second.fields.summary == "a" * 2000
    assert selection.items_truncated == 1
    assert selection.items_dropped == 0


def test_the_writing_cuts_each_name_of_packages():
    item = WatchItem(
        item_id=9,
        kind="advisory",
        source_date=NOW,
        url=None,
        source_title="An advisory",
        fields=build_fields(
            "advisory",
            {
                "ghsa_id": "GHSA-fjqc-hq36-qh5p",
                "cve_id": None,
                "title": "An advisory",
                "cve_listed_in_cisa_kev": None,
                "packages": ["p" * 2001, "langchain"],
            },
        ),
    )

    selection = select_for_writing([item])

    assert selection.items[0].fields.model_dump()["packages"] == [
        "p" * 2000,
        "langchain",
    ]
    assert selection.items_truncated == 1
