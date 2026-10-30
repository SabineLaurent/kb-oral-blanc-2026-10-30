"""The digests of a request on the replay provider: the items of the test base classified by
subject, then each subject written."""

import json
import secrets
from dataclasses import replace
from datetime import UTC, datetime, time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from k_b.ai_api import digests, watch_items
from k_b.ai_api.connectors.data_api_client import read_watch
from k_b.ai_api.connectors.llm_client import build_http_client
from k_b.ai_api.connectors.llm_replay import ReplayTransport
from k_b.ai_api.connectors.llm_settings import LlmSettings
from k_b.ai_api.deadline import Deadline
from k_b.ai_api.digests import DigestsResult, FailedSubject, build_digests
from k_b.ai_api.stages import classification
from k_b.ai_api.stages.classification import (
    CLASSIFICATION_SYSTEM_PROMPT,
    ClassificationFailure,
)
from k_b.ai_api.stages.writing import WRITING_RETRY_REMINDERS, WRITING_SYSTEM_PROMPT
from k_b.ai_api.tracing import TRACING_OFF
from k_b.ai_api.watch_items import WatchItem, WatchReading, build_fields
from k_b.core.config import (
    LLM_REPLAY_BASE_URL,
    MIN_SECONDS_FOR_LLM_REQUEST,
    NO_SUITABLE_SUBJECT_ANSWER,
    DigestSubject,
)
from k_b.data_api.app import create_app
from k_b.etl.sources.tldr import read_articles

# The subjects of the tests, never the real ones: a change of the real list breaks no test.
TEST_SUBJECTS = (
    DigestSubject("security_alerts", "failles et alertes de sécurité"),
    DigestSubject("agent_tools", "outils et cadres d'agents"),
    DigestSubject("research", "articles de recherche"),
)
REPLAY_SETTINGS = LlmSettings(
    provider="replay",
    base_url=LLM_REPLAY_BASE_URL,
    model="replay",
    api_key=None,
    reasoning_effort=None,
    replay_steps=None,
)
# The items of the test base, in the order of the reading (test_data_api_client.py): the
# vulnerabilities 3 and 4, the advisories 9, 10 and 17, the papers 2 and 1, the releases 6, 7 and
# 8, the newsletter articles 12, 11 and 13.
SECURITY_ITEMS = [3, 4, 9, 10, 17]
AGENT_ITEMS = [6, 7, 8]
RESEARCH_ITEMS = [2, 1]
NO_SUITABLE_SUBJECT_ITEMS = [12, 11, 13]
# Facts copied from the items of the test base: the GHSA of advisory 9, the tag of release 6.
SECURITY_CLAIM = {
    "text": "L'alerte GHSA-fjqc-hq36-qh5p touche langgraph-checkpoint.",
    "item_ids": ["i9"],
    "facts": [{"value": "GHSA-fjqc-hq36-qh5p", "item_id": "i9"}],
}
AGENT_CLAIM = {
    "text": "Claude Code publie la version v2.1.287.",
    "item_ids": ["i6"],
    "facts": [{"value": "v2.1.287", "item_id": "i6"}],
}


@pytest.fixture(scope="module")
def reading(items_database):
    """The watch of the test base, read by the data API with the key of its client AI_DIGEST."""
    key = secrets.token_urlsafe(32)
    with TestClient(
        create_app(items_database, {"AI_DIGEST": SecretStr(key)}),
        base_url="http://localhost",
        headers={"Authorization": f"Bearer {key}"},
    ) as data_api:
        return read_watch(
            data_api,
            datetime(2020, 1, 1, tzinfo=UTC),
            deadline=Deadline.after(3600),
        )


class Clock:
    """A clock set by the test: never the time of the machine."""

    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now


class SlowTransport(ReplayTransport):
    """The replay, each answer taking the given seconds on the clock of the test."""

    def __init__(self, steps, clock, seconds_per_answer):
        super().__init__(steps)
        self.clock = clock
        self.seconds_per_answer = seconds_per_answer

    def answer(self, request):
        self.clock.now += self.seconds_per_answer
        return super().answer(request)


def completion(output):
    content = output if isinstance(output, str) else json.dumps(output)
    return {"kind": "completion", "content": content}


def answered(pairs):
    """The output of the classification written by hand, pair by pair: a key may come twice, as
    a model could write it, and json.dumps would merge them."""
    return completion(
        "{" + ", ".join(f"{json.dumps(ref)}: {json.dumps(a)}" for ref, a in pairs) + "}"
    )


def classified(
    security=SECURITY_ITEMS,
    agent=AGENT_ITEMS,
    research=RESEARCH_ITEMS,
    no_suitable_subject=NO_SUITABLE_SUBJECT_ITEMS,
    extra=(),
):
    """The answer for each item given, then the extra pairs."""
    pairs = [
        (f"i{item_id}", answer)
        for answer, item_ids in (
            ("security_alerts", security),
            ("agent_tools", agent),
            ("research", research),
            (NO_SUITABLE_SUBJECT_ANSWER, no_suitable_subject),
        )
        for item_id in item_ids
    ]
    return answered([*pairs, *extra])


def written(*claims):
    return completion({"claims": list(claims)})


# A valid request: the classification, then the writing of each subject, in the order of the list.
VALID_STEPS = [
    classified(),
    written(SECURITY_CLAIM),
    written(AGENT_CLAIM),
    written(),
]


def digest_of(
    reading,
    steps,
    deadline=None,
    transport=None,
    subjects=TEST_SUBJECTS,
    tracing=TRACING_OFF,
):
    transport = transport or ReplayTransport(steps)
    with build_http_client(REPLAY_SETTINGS, transport=transport) as http_client:
        result = build_digests(
            http_client,
            REPLAY_SETTINGS,
            reading,
            subjects,
            deadline or Deadline.after(3600),
            tracing=tracing,
        )
    return result, transport


def sent_bodies(transport):
    return [json.loads(request.content) for request in transport.requests]


def messages_of(body, role):
    return [m["content"] for m in body["messages"] if m["role"] == role]


def test_a_valid_output_gives_a_digest_per_subject_with_its_sources(reading):
    result, transport = digest_of(reading, VALID_STEPS)

    assert isinstance(result, DigestsResult)
    assert [digest.subject for digest in result.digests] == [
        "security_alerts",
        "agent_tools",
        "research",
    ]
    security = result.digests[0]
    assert [claim.text for claim in security.claims] == [SECURITY_CLAIM["text"]]
    assert security.claims[0].item_ids == (9,)
    advisory = next(item for item in reading.items if item.item_id == 9)
    assert [
        (source.item_id, source.kind, source.title, source.url)
        for source in security.sources
    ] == [(9, "advisory", advisory.source_title, advisory.url)]
    # Sent and not cited.
    assert sorted(security.items_not_retained_in_output) == [3, 4, 10, 17]
    assert security.claims_removed == 0
    assert security.items_removed_by_checks == ()
    # A subject written without claim: a digest without claim, its items not retained.
    assert result.digests[2].claims == ()
    assert sorted(result.digests[2].items_not_retained_in_output) == [1, 2]
    assert result.failed_subjects == ()
    assert result.no_suitable_subject == tuple(NO_SUITABLE_SUBJECT_ITEMS)
    assert result.items_in == len(reading.items)
    assert result.items_dropped_before_writing_stage == reading.items_dropped
    assert result.items_capped is False
    assert [(call.stage, call.subject) for call in result.stage_calls] == [
        ("classify", None),
        ("write", "security_alerts"),
        ("write", "agent_tools"),
        ("write", "research"),
    ]
    assert len(transport.requests) == 4


def test_the_stage_calls_carry_their_prompt_version_and_counts(reading):
    result, _ = digest_of(reading, VALID_STEPS)

    classify_call, security_call = result.stage_calls[:2]
    assert classify_call.prompt_version == "classify-2"
    assert classify_call.items_in == len(reading.items)
    assert classify_call.claims_removed is None
    assert (classify_call.facts_checked, classify_call.facts_found) == (None, None)
    assert classify_call.llm_call.outcome == "ok"
    assert security_call.prompt_version == "write-1"
    assert security_call.items_in == len(SECURITY_ITEMS)
    assert security_call.claims_removed == 0
    assert security_call.items_removed_by_checks == 0
    assert (security_call.facts_checked, security_call.facts_found) == (1, 1)


def test_a_subject_out_of_the_list_is_refused(reading):
    out_of_list = classified(security=[4, 9, 10, 17], extra=[("i3", "admin")])

    result, transport = digest_of(reading, [out_of_list, out_of_list])

    assert isinstance(result, ClassificationFailure)
    assert result.state == "llm_invalid_output"
    assert result.stage_calls[0].llm_call.failed_check == "subject"
    # The schema sent holds the subjects of the list only, and the answer of no subject.
    schema = sent_bodies(transport)[0]["response_format"]["json_schema"]["schema"]
    assert schema["properties"]["i3"]["enum"] == [
        *(subject.name for subject in TEST_SUBJECTS),
        NO_SUITABLE_SUBJECT_ANSWER,
    ]


def test_an_unknown_item_id_is_refused_at_the_classification(reading):
    unknown = classified(security=[*SECURITY_ITEMS, 99999])

    result, _ = digest_of(reading, [unknown, unknown])

    assert isinstance(result, ClassificationFailure)
    assert result.stage_calls[0].llm_call.failed_check == "item_id"


def test_an_unknown_item_id_is_refused_at_the_writing_and_the_others_are_returned(
    reading,
):
    invented = {**AGENT_CLAIM, "item_ids": ["i6", "i99999"]}

    result, _ = digest_of(
        reading,
        [
            classified(),
            written(SECURITY_CLAIM),
            written(invented),
            written(invented),
            written(),
        ],
    )

    assert isinstance(result, DigestsResult)
    assert result.failed_subjects == (
        FailedSubject(subject="agent_tools", stage="write", error="llm_invalid_output"),
    )
    assert [digest.subject for digest in result.digests] == [
        "security_alerts",
        "research",
    ]
    assert result.stage_calls[2].llm_call.failed_check == "item_id"


def test_a_fact_of_an_item_its_claim_does_not_cite_is_refused(reading):
    misplaced = {**AGENT_CLAIM, "facts": [{"value": "v2.1.287", "item_id": "i7"}]}

    result, _ = digest_of(
        reading,
        [
            classified(),
            written(SECURITY_CLAIM),
            written(misplaced),
            written(misplaced),
            written(),
        ],
    )

    assert result.failed_subjects[0].subject == "agent_tools"
    assert result.stage_calls[2].llm_call.failed_check == "item_id"


def test_an_item_without_answer_is_counted_apart_without_failure(reading):
    # A miss of the model, not its judgement: kept apart from the items no subject suits.
    forgotten = classified(no_suitable_subject=[12, 11])

    result, _ = digest_of(reading, [forgotten, *VALID_STEPS[1:]])

    assert isinstance(result, DigestsResult)
    assert result.no_suitable_subject == (12, 11)
    assert result.unanswered == (13,)
    classify_call = result.stage_calls[0]
    assert (
        classify_call.items_with_subject,
        classify_call.items_no_suitable_subject,
        classify_call.items_unanswered,
    ) == (10, 2, 1)
    assert classify_call.llm_call.attempts == 1


def test_an_answer_to_no_item_is_asked_again_then_fails_the_request(reading):
    # A provider that ignores strict mode: {} is not a miss on an item, it does not sort.
    empty = answered([])

    result, _ = digest_of(reading, [empty, empty])

    assert isinstance(result, ClassificationFailure)
    assert result.state == "llm_invalid_output"
    assert result.stage_calls[0].llm_call.failed_check == "completeness"
    assert result.stage_calls[0].llm_call.attempts == 2


def test_an_answer_to_no_item_then_a_valid_one_passes(reading):
    result, _ = digest_of(reading, [answered([]), *VALID_STEPS])

    assert isinstance(result, DigestsResult)
    assert result.stage_calls[0].llm_call.first_failed_check == "completeness"
    assert result.unanswered == ()


def test_an_item_answered_twice_at_the_classification_is_llm_invalid_output(reading):
    # The case of the first trial of #210 on gpt-6-luna: an item given a subject, then not
    # retained.
    twice = classified(extra=[("i9", NO_SUITABLE_SUBJECT_ANSWER)])

    result, _ = digest_of(reading, [twice, twice])

    assert isinstance(result, ClassificationFailure)
    assert result.state == "llm_invalid_output"
    assert result.stage_calls[0].llm_call.failed_check == "schema"


def test_a_claim_too_long_is_removed_and_counted(reading):
    too_long = {**AGENT_CLAIM, "text": "v2.1.287 " + "x" * 400}

    result, _ = digest_of(
        reading,
        [classified(), written(SECURITY_CLAIM), written(too_long), written()],
    )

    agent = result.digests[1]
    assert agent.subject == "agent_tools"
    assert agent.claims == ()
    assert agent.claims_removed == 1
    # Cited by the model, no longer cited once the claim is removed.
    assert agent.items_removed_by_checks == (6,)
    assert 6 not in agent.items_not_retained_in_output
    assert agent.sources == ()
    assert result.stage_calls[2].claims_removed == 1
    assert result.stage_calls[2].items_removed_by_checks == 1
    # Its fact is still checked: found in its text and in item 6.
    assert (result.stage_calls[2].facts_checked, result.stage_calls[2].facts_found) == (
        1,
        1,
    )


@pytest.mark.parametrize(
    "claim_change",
    [
        {"text": ""},
        {"facts": [{"value": "v2", "item_id": "i6"}]},
        {"facts": [{"value": "v" * 121, "item_id": "i6"}]},
        {"facts": []},
        {"facts": [{"value": "v2.1.287", "item_id": "i6"}] * 11},
    ],
)
def test_a_claim_out_of_its_bounds_is_removed(reading, claim_change):
    result, _ = digest_of(
        reading,
        [
            classified(),
            written(SECURITY_CLAIM),
            written({**AGENT_CLAIM, **claim_change}),
            written(),
        ],
    )

    assert result.digests[1].claims_removed == 1


def test_more_claims_than_the_structure_allows_is_a_schema_failure(reading):
    thirteen = written(*[AGENT_CLAIM] * 13)

    result, _ = digest_of(
        reading, [classified(), written(SECURITY_CLAIM), thirteen, thirteen, written()]
    )

    assert result.failed_subjects[0].subject == "agent_tools"
    assert result.stage_calls[2].llm_call.failed_check == "schema"


def test_a_subject_in_failure_is_named_and_the_others_are_returned(reading):
    result, _ = digest_of(
        reading,
        [
            classified(),
            written(SECURITY_CLAIM),
            {"kind": "response", "status": 503},
            {"kind": "response", "status": 503},
            written(),
        ],
    )

    assert result.failed_subjects == (
        FailedSubject(subject="agent_tools", stage="write", error="llm_unavailable"),
    )
    assert [digest.subject for digest in result.digests] == [
        "security_alerts",
        "research",
    ]


def test_a_failed_classification_carries_its_retry_after(reading):
    result, _ = digest_of(
        reading,
        [
            {"kind": "response", "status": 429, "retry_after": "1"},
            {"kind": "response", "status": 429, "retry_after": "3"},
        ],
    )

    assert isinstance(result, ClassificationFailure)
    assert result.state == "llm_rate_limited"
    assert result.retry_after_seconds == 3


def test_the_subjects_not_written_by_the_deadline_are_llm_timeout(
    reading, recorded_traces
):
    clock = Clock()
    # The classification and one writing fit; the second writing would start with too little time.
    deadline = Deadline.after(2 * 30 + MIN_SECONDS_FOR_LLM_REQUEST - 1, clock)
    transport = SlowTransport(VALID_STEPS, clock, seconds_per_answer=30)

    result, _ = digest_of(
        reading,
        VALID_STEPS,
        deadline=deadline,
        transport=transport,
        tracing=recorded_traces.trace_export.build_tracing(),
    )

    assert [digest.subject for digest in result.digests] == ["security_alerts"]
    assert result.failed_subjects == (
        FailedSubject(subject="agent_tools", stage="write", error="llm_timeout"),
        FailedSubject(subject="research", stage="write", error="llm_timeout"),
    )
    # Nothing is sent for a subject past the deadline: its items stay home.
    assert len(transport.requests) == 2
    assert len(result.stage_calls) == 2
    # Each subject skipped is a span in error, without request under it.
    writings = recorded_traces.find_spans("write_subject")
    assert [span.attributes.get("error.type") for span in writings] == [
        None,
        "llm_timeout",
        "llm_timeout",
    ]
    assert len(recorded_traces.find_spans("chat replay")) == 2


def test_a_classification_not_finished_by_the_deadline_is_llm_timeout(
    reading, monkeypatch, recorded_traces
):
    monkeypatch.setattr(classification, "MAX_ITEMS_PER_CLASSIFICATION_CALL", 10)
    clock = Clock()
    deadline = Deadline.after(30 + MIN_SECONDS_FOR_LLM_REQUEST - 1, clock)
    # A valid output for the first batch: the deadline, not a refusal, stops the second.
    steps = [
        classified(
            security=[3, 4, 9, 10, 17],
            agent=[6, 7, 8],
            research=[2, 1],
            no_suitable_subject=[],
        )
    ]
    transport = SlowTransport(steps, clock, seconds_per_answer=30)

    result, _ = digest_of(
        reading,
        steps,
        deadline=deadline,
        transport=transport,
        tracing=recorded_traces.trace_export.build_tracing(),
    )

    assert isinstance(result, ClassificationFailure)
    assert result.state == "llm_timeout"
    assert len(transport.requests) == 1
    # The batch not sent is a span in error, without request under it.
    first, second = recorded_traces.find_spans("classify_batch")
    assert (first.attributes.get("error.type"), second.attributes["error.type"]) == (
        None,
        "llm_timeout",
    )
    assert len(recorded_traces.find_spans("chat replay")) == 1


def test_the_classification_is_split_in_batches_each_checked_against_its_items(
    reading, monkeypatch
):
    monkeypatch.setattr(classification, "MAX_ITEMS_PER_CLASSIFICATION_CALL", 10)
    first_batch = classified(
        security=SECURITY_ITEMS,
        agent=AGENT_ITEMS,
        research=RESEARCH_ITEMS,
        no_suitable_subject=[],
    )
    second_batch = classified(
        security=[],
        agent=[],
        research=[],
        no_suitable_subject=NO_SUITABLE_SUBJECT_ITEMS,
    )

    result, transport = digest_of(
        reading, [first_batch, second_batch, *VALID_STEPS[1:]]
    )

    assert isinstance(result, DigestsResult)
    first, second = sent_bodies(transport)[:2]
    assert len(json.loads(messages_of(first, "user")[0])["items"]) == 10
    assert len(json.loads(messages_of(second, "user")[0])["items"]) == 3
    assert result.no_suitable_subject == tuple(NO_SUITABLE_SUBJECT_ITEMS)


def test_an_item_of_another_batch_is_refused(reading, monkeypatch):
    monkeypatch.setattr(classification, "MAX_ITEMS_PER_CLASSIFICATION_CALL", 10)
    # Item 13 is in the second batch, not in the first.
    wrong_batch = classified(no_suitable_subject=[13])

    result, _ = digest_of(reading, [wrong_batch, wrong_batch])

    assert isinstance(result, ClassificationFailure)
    assert result.stage_calls[0].llm_call.failed_check == "item_id"


def test_an_empty_window_calls_no_model():
    empty = WatchReading(
        items=(), items_dropped=2, capped_kind=None, covered_after=None
    )

    result, transport = digest_of(empty, [])

    assert result == DigestsResult(
        digests=(),
        failed_subjects=(),
        no_suitable_subject=(),
        unanswered=(),
        items_in=0,
        items_truncated=0,
        items_dropped_before_writing_stage=2,
        items_capped=False,
        stage_calls=(),
    )
    assert transport.requests == []


def test_a_subject_without_item_is_not_written(reading):
    result, transport = digest_of(
        reading,
        [
            classified(
                research=[], no_suitable_subject=[*NO_SUITABLE_SUBJECT_ITEMS, 2, 1]
            ),
            *VALID_STEPS[1:3],
        ],
    )

    assert [digest.subject for digest in result.digests] == [
        "security_alerts",
        "agent_tools",
    ]
    assert len(transport.requests) == 3


def test_the_items_beyond_the_digest_cap_are_dropped_and_counted(reading, monkeypatch):
    monkeypatch.setattr(watch_items, "MAX_ITEMS_PER_DIGEST", 2)

    result, transport = digest_of(
        reading, [classified(), written(), written(), written()]
    )

    security_body = sent_bodies(transport)[1]
    assert len(json.loads(messages_of(security_body, "user")[0])["items"]) == 2
    # 3 security items and 1 agent item beyond 2, added to those of the reading.
    assert result.items_dropped_before_writing_stage == reading.items_dropped + 3 + 1


def test_a_source_url_without_http_scheme_is_returned_null(reading):
    trapped = WatchReading(
        items=tuple(
            replace(item, url="javascript:alert(1)") if item.item_id == 9 else item
            for item in reading.items
        ),
        items_dropped=reading.items_dropped,
        capped_kind=None,
        covered_after=None,
    )

    result, _ = digest_of(trapped, VALID_STEPS)

    assert result.digests[0].sources[0].url is None


def test_the_items_go_in_the_user_message_never_in_a_system_message(reading):
    """Rule 8 of poc.md: the text of a source is data; the retry adds a constant reminder."""
    result, transport = digest_of(
        reading,
        [completion("not json"), *VALID_STEPS],
    )

    assert isinstance(result, DigestsResult)
    bodies = sent_bodies(transport)
    titles = [item.fields.title for item in reading.items]
    for body in bodies:
        system_text = "\n".join(messages_of(body, "system"))
        assert not any(title in system_text for title in titles)
    assert messages_of(bodies[0], "system") == [CLASSIFICATION_SYSTEM_PROMPT]
    assert messages_of(bodies[1], "system")[0].startswith(CLASSIFICATION_SYSTEM_PROMPT)
    assert messages_of(bodies[2], "system") == [WRITING_SYSTEM_PROMPT]
    user_data = json.loads(messages_of(bodies[0], "user")[0])
    assert {item["item_id"] for item in user_data["items"]} == {
        f"i{item.item_id}" for item in reading.items
    }
    assert user_data["subjects"] == [
        {"name": subject.name, "definition": subject.definition}
        for subject in TEST_SUBJECTS
    ]


def test_the_retry_of_a_stage_sends_its_reminder(reading):
    result, transport = digest_of(
        reading,
        [
            classified(),
            written(SECURITY_CLAIM),
            written({**AGENT_CLAIM, "item_ids": ["i99999"]}),
            written(AGENT_CLAIM),
            written(),
        ],
    )

    assert isinstance(result, DigestsResult)
    retry = sent_bodies(transport)[3]
    assert messages_of(retry, "system")[0] != WRITING_SYSTEM_PROMPT
    assert messages_of(retry, "system")[0].startswith(WRITING_SYSTEM_PROMPT)
    assert result.stage_calls[2].llm_call.first_failed_check == "item_id"


def schema_sent(body):
    return body["response_format"]["json_schema"]["schema"]


def test_the_schema_of_each_call_holds_the_items_of_this_call_only(reading):
    _, transport = digest_of(reading, VALID_STEPS)

    classification_body, security_body = sent_bodies(transport)[:2]
    assert list(schema_sent(classification_body)["properties"]) == [
        f"i{item.item_id}" for item in reading.items
    ]
    claim = schema_sent(security_body)["properties"]["claims"]["items"]
    sent_refs = [
        item["item_id"]
        for item in json.loads(messages_of(security_body, "user")[0])["items"]
    ]
    assert sorted(claim["properties"]["item_ids"]["items"]["enum"]) == sorted(sent_refs)
    assert sorted(sent_refs) == sorted(f"i{item_id}" for item_id in SECURITY_ITEMS)


def test_every_call_of_the_request_waits_no_longer_than_its_deadline(reading):
    _, transport = digest_of(reading, VALID_STEPS, deadline=Deadline.after(50, Clock()))

    assert [
        request.extensions["timeout"]["read"] for request in transport.requests
    ] == [50] * len(VALID_STEPS)


@pytest.mark.parametrize("item_ids", [[], ["i3", "i4", "i9", "i10", "i17", "i3"]])
def test_a_claim_citing_no_item_or_more_than_five_is_a_schema_failure(
    reading, item_ids
):
    out_of_structure = written({**SECURITY_CLAIM, "item_ids": item_ids})

    result, _ = digest_of(
        reading,
        [
            classified(),
            out_of_structure,
            out_of_structure,
            written(AGENT_CLAIM),
            written(),
        ],
    )

    assert result.failed_subjects[0].subject == "security_alerts"
    assert result.stage_calls[1].llm_call.failed_check == "schema"


def test_an_item_cited_twice_in_a_claim_is_refused(reading):
    twice = written({**SECURITY_CLAIM, "item_ids": ["i9", "i9"]})

    result, _ = digest_of(
        reading, [classified(), twice, twice, written(AGENT_CLAIM), written()]
    )

    assert result.failed_subjects[0].subject == "security_alerts"
    assert result.stage_calls[1].llm_call.failed_check == "item_id"


@pytest.mark.parametrize(
    "rate_limited_steps",
    [
        # A Retry-After above the maximum: no retry.
        [{"kind": "response", "status": 429, "retry_after": "60"}],
        # A 429 again after the retry.
        [
            {"kind": "response", "status": 429, "retry_after": "1"},
            {"kind": "response", "status": 429, "retry_after": "1"},
        ],
    ],
)
def test_after_a_writing_rate_limited_nothing_more_is_sent(reading, rate_limited_steps):
    result, transport = digest_of(
        reading, [classified(), written(SECURITY_CLAIM), *rate_limited_steps]
    )

    assert [digest.subject for digest in result.digests] == ["security_alerts"]
    assert result.failed_subjects == (
        FailedSubject(subject="agent_tools", stage="write", error="llm_rate_limited"),
        FailedSubject(subject="research", stage="write", error="llm_rate_limited"),
    )
    # The items of research never left.
    assert len(transport.requests) == 2 + len(rate_limited_steps)
    assert [call.subject for call in result.stage_calls] == [
        None,
        "security_alerts",
        "agent_tools",
    ]


def test_after_a_429_whose_retry_the_deadline_cuts_nothing_more_is_sent(reading):
    # 25 s left: the retry after 20 s would leave 5, under the minimum; the next subject would
    # still have 25.
    deadline = Deadline.after(25, Clock())

    result, transport = digest_of(
        reading,
        [
            classified(),
            written(SECURITY_CLAIM),
            {"kind": "response", "status": 429, "retry_after": "20"},
        ],
        deadline=deadline,
    )

    assert result.failed_subjects == (
        FailedSubject(subject="agent_tools", stage="write", error="llm_timeout"),
        FailedSubject(subject="research", stage="write", error="llm_rate_limited"),
    )
    assert len(transport.requests) == 3


def test_a_classification_whose_retry_the_deadline_cuts_gives_no_retry_after(reading):
    deadline = Deadline.after(20, Clock())

    result, _ = digest_of(
        reading,
        [{"kind": "response", "status": 429, "retry_after": "15"}],
        deadline=deadline,
    )

    assert isinstance(result, ClassificationFailure)
    assert result.state == "llm_timeout"
    assert result.retry_after_seconds is None
    # What was observed stays in the call, for the metric.
    assert result.stage_calls[0].llm_call.retry_after_seconds == 15


def test_an_item_cut_for_the_writing_is_counted(reading):
    long_summary = WatchReading(
        items=tuple(
            replace(item, fields=item.fields.model_copy(update={"summary": "s" * 2500}))
            if item.item_id == 2
            else item
            for item in reading.items
        ),
        items_dropped=reading.items_dropped,
        capped_kind=None,
        covered_after=None,
    )

    result, _ = digest_of(long_summary, VALID_STEPS)

    assert result.items_truncated == 1
    (research_call,) = [c for c in result.stage_calls if c.subject == "research"]
    assert research_call.items_truncated == 1


@pytest.mark.parametrize(
    "failed_retry",
    [{"kind": "timeout"}, {"kind": "response", "status": 500}],
)
def test_a_429_is_not_erased_by_a_retry_that_fails(reading, failed_retry):
    result, transport = digest_of(
        reading,
        [
            classified(),
            written(SECURITY_CLAIM),
            {"kind": "response", "status": 429, "retry_after": "1"},
            failed_retry,
        ],
    )

    assert result.failed_subjects[1] == FailedSubject(
        subject="research", stage="write", error="llm_rate_limited"
    )
    assert len(transport.requests) == 4


def test_a_classification_down_gives_no_retry_after(reading):
    result, _ = digest_of(
        reading, [{"kind": "response", "status": 503, "retry_after": "30"}]
    )

    assert isinstance(result, ClassificationFailure)
    assert result.state == "llm_unavailable"
    assert result.retry_after_seconds is None


# The trapped item of #206: the article of a TLDR AI edition collected (tldr_ai_extract.html,
# 2026-10-07), its summary carrying the trap of the criterion; an id out of the test base.
TRAPPED_PAGE = (
    Path(__file__).parent.parent / "fixtures" / "tldr_ai_trapped_extract.html"
)
TRAPPED_ITEM_ID = 900
TRAPPED_REF = f"i{TRAPPED_ITEM_ID}"
TRAPPED_LINK = "evil.example"


@pytest.fixture(scope="module")
def trapped_reading(reading):
    """The watch of the test base and the trapped article, read by the TLDR reader, then masked
    as the data API client masks an item."""
    article = read_articles(TRAPPED_PAGE.read_bytes()).entries[0]
    trapped_item = WatchItem(
        item_id=TRAPPED_ITEM_ID,
        kind="newsletter_article",
        source_date=datetime.combine(article.edition_date, time(), tzinfo=UTC),
        url=article.link,
        source_title=article.title,
        fields=build_fields(
            "newsletter_article",
            {"edition": "ai", "title": article.title, "summary": article.summary},
        ),
    )
    return replace(reading, items=(*reading.items, trapped_item))


def classified_with_trap(agent=(*AGENT_ITEMS, TRAPPED_ITEM_ID), trapped=()):
    """The valid classification, the trapped item in agent_tools, and the answers the trap asks
    for."""
    return classified(agent=list(agent), extra=trapped)


def trapped_claim(text, item_ids=(TRAPPED_REF,)):
    # "China" is in the title of the trapped article.
    return {
        "text": text,
        "item_ids": list(item_ids),
        "facts": [{"value": "China", "item_id": TRAPPED_REF}],
    }


def written_with_trap(claim):
    """The writing of each subject, the model obeying the trap at both attempts of agent_tools."""
    return [
        classified_with_trap(),
        written(SECURITY_CLAIM),
        written(claim),
        written(claim),
        written(),
    ]


ADMIN_SUBJECT = classified_with_trap(
    agent=AGENT_ITEMS, trapped=[(TRAPPED_REF, "admin")]
)
# Answered twice: a key twice in the output, refused by the schema check.
TRAPPED_TWICE = classified_with_trap(trapped=[(TRAPPED_REF, "research")])


@pytest.mark.parametrize(
    ("steps", "failed_check"),
    [
        ([ADMIN_SUBJECT, ADMIN_SUBJECT], "subject"),
        ([TRAPPED_TWICE, TRAPPED_TWICE], "schema"),
        (
            written_with_trap(
                trapped_claim("La Chine recrute.", (TRAPPED_REF, "i99999"))
            ),
            "item_id",
        ),
        (
            written_with_trap(
                trapped_claim(f"La Chine recrute. ![x](https://{TRAPPED_LINK})")
            ),
            "markup",
        ),
        (
            written_with_trap(
                trapped_claim(f"La Chine recrute : https://{TRAPPED_LINK}")
            ),
            "markup",
        ),
    ],
    ids=["subject_admin", "item_twice", "item_99999", "image", "link"],
)
def test_a_model_obeying_a_trapped_item_gives_a_named_state_and_no_digest(
    trapped_reading, steps, failed_check
):
    """Each output the trap asks for is refused, and the trapped item stayed in the user message
    (rule 8 of poc.md)."""
    result, transport = digest_of(trapped_reading, steps)

    if isinstance(result, ClassificationFailure):
        assert result.state == "llm_invalid_output"
        refused_call = 0
    else:
        assert result.failed_subjects == (
            FailedSubject(
                subject="agent_tools", stage="write", error="llm_invalid_output"
            ),
        )
        assert "agent_tools" not in [digest.subject for digest in result.digests]
        refused_call = 2
    assert result.stage_calls[refused_call].llm_call.failed_check == failed_check
    trapped_summary = trapped_reading.items[-1].fields.summary
    bodies = sent_bodies(transport)
    for body in bodies[refused_call : refused_call + 2]:
        sent_items = json.loads(messages_of(body, "user")[0])["items"]
        assert {"item_id": TRAPPED_REF, "summary": trapped_summary}.items() <= next(
            item for item in sent_items if item["item_id"] == TRAPPED_REF
        ).items()
    for body in bodies:
        assert not any("Ignore les consignes" in m for m in messages_of(body, "system"))
        assert TRAPPED_LINK not in json.dumps(body["messages"])
    # Rule 5 of poc.md: the measures of the calls carry no text of the item nor of the output.
    assert TRAPPED_LINK not in repr(result.stage_calls)
    assert "Ignore les consignes" not in repr(result.stage_calls)


def test_the_retry_after_a_markup_sends_its_reminder(trapped_reading):
    image = trapped_claim(f"La Chine recrute. ![x](https://{TRAPPED_LINK})")
    plain = trapped_claim("China peine à recruter des chercheurs étrangers.")

    result, transport = digest_of(
        trapped_reading,
        [
            classified_with_trap(),
            written(SECURITY_CLAIM),
            written(image),
            written(plain),
            written(),
        ],
    )

    assert isinstance(result, DigestsResult)
    assert result.digests[1].subject == "agent_tools"
    assert result.stage_calls[2].llm_call.first_failed_check == "markup"
    assert messages_of(sent_bodies(transport)[3], "system") == [
        f"{WRITING_SYSTEM_PROMPT}\n\n{WRITING_RETRY_REMINDERS['markup']}"
    ]


@pytest.mark.parametrize(
    ("claims", "failed_check"),
    [
        # Check 4 over the whole output before check 6.
        (
            [
                {**AGENT_CLAIM, "text": "v2.1.287 : https://evil.example"},
                {**AGENT_CLAIM, "item_ids": ["i6", "i99999"]},
            ],
            "item_id",
        ),
        # Check 6 before the bounds of a claim, which would only remove it.
        ([{**AGENT_CLAIM, "text": "v2.1.287 " + "x" * 400 + " www.x"}], "markup"),
    ],
    ids=["item_id_before_markup", "markup_before_bounds"],
)
def test_the_checks_of_the_writing_go_in_their_order(reading, claims, failed_check):
    result, _ = digest_of(
        reading,
        [
            classified(),
            written(SECURITY_CLAIM),
            written(*claims),
            written(*claims),
            written(),
        ],
    )

    assert result.failed_subjects[0].subject == "agent_tools"
    assert result.stage_calls[2].llm_call.failed_check == failed_check


# The CVE of vulnerability 3 and its vendor, copied from the test base.
CVE_CLAIM = {
    "text": "Apple corrige la CVE-2026-86950.",
    "item_ids": ["i3"],
    "facts": [
        {"value": "CVE-2026-86950", "item_id": "i3"},
        {"value": "Apple", "item_id": "i3"},
    ],
}


def written_for_agent_tools(*claims):
    """The steps of a request whose writing of agent_tools gives these claims."""
    return [classified(), written(SECURITY_CLAIM), written(*claims), written()]


def test_the_facts_found_in_the_items_they_cite_keep_their_claims(reading):
    result, _ = digest_of(
        reading,
        [
            classified(),
            written(SECURITY_CLAIM, CVE_CLAIM),
            written(AGENT_CLAIM),
            written(),
        ],
    )

    security, agent, _ = result.digests
    assert [claim.text for claim in security.claims] == [
        SECURITY_CLAIM["text"],
        CVE_CLAIM["text"],
    ]
    assert [claim.text for claim in agent.claims] == [AGENT_CLAIM["text"]]
    assert (security.claims_removed, agent.claims_removed) == (0, 0)
    security_call = result.stage_calls[1]
    assert (security_call.facts_checked, security_call.facts_found) == (3, 3)


@pytest.mark.parametrize(
    "version",
    ["v2.1.299", "v2.1.286"],
    ids=["absent_from_every_item", "tag_of_another_item"],
)
def test_a_fact_not_found_in_the_item_it_cites_removes_its_claim(reading, version):
    """v2.1.286 is the tag of release 7, sent in the same call: it is not found for item 6."""
    unfaithful = {
        "text": f"Claude Code publie la version {version}.",
        "item_ids": ["i6"],
        "facts": [{"value": version, "item_id": "i6"}],
    }

    result, _ = digest_of(reading, written_for_agent_tools(unfaithful, AGENT_CLAIM))

    agent = result.digests[1]
    assert [claim.text for claim in agent.claims] == [AGENT_CLAIM["text"]]
    assert agent.claims_removed == 1
    assert (result.stage_calls[2].facts_checked, result.stage_calls[2].facts_found) == (
        2,
        1,
    )


def with_release_body(reading, body):
    """The reading, the body of release 6 replaced and masked as the data API client masks it."""
    release = next(item for item in reading.items if item.item_id == 6)
    fields = build_fields("release", {**release.fields.model_dump(), "body": body})
    return replace(
        reading,
        items=tuple(
            replace(item, fields=fields) if item.item_id == 6 else item
            for item in reading.items
        ),
    )


@pytest.mark.parametrize(
    "body",
    [
        (
            "See github.com/anthropics/claude-code/releases/tag/v2.1.287 at "
            "https://github.com/anthropics/claude-code/releases/tag/build-77"
        ),
        "x" * 2000 + " build-77",
    ],
    ids=["in_a_masked_url", "beyond_the_cut"],
)
def test_a_fact_found_only_in_what_is_not_sent_is_not_found(reading, body):
    """The fidelity searches the fields sent: URLs masked, then cut at 2,000 characters."""
    unsent = {
        "text": "Claude Code publie build-77.",
        "item_ids": ["i6"],
        "facts": [{"value": "build-77", "item_id": "i6"}],
    }

    result, _ = digest_of(
        with_release_body(reading, body), written_for_agent_tools(unsent, AGENT_CLAIM)
    )

    assert result.digests[1].claims_removed == 1
    assert result.stage_calls[2].facts_found == 1


def test_an_item_cited_without_a_fact_found_removes_its_claim(reading):
    cited_for_nothing = {**AGENT_CLAIM, "item_ids": ["i6", "i7"]}

    result, _ = digest_of(reading, written_for_agent_tools(cited_for_nothing))

    agent = result.digests[1]
    assert agent.claims == ()
    assert agent.items_removed_by_checks == (6, 7)


@pytest.mark.parametrize(
    "facts",
    [
        [{"value": "CVE-2026-0001", "item_id": TRAPPED_REF}],
        [{"value": "China", "item_id": TRAPPED_REF}],
    ],
    ids=["cve_declared", "cve_not_declared"],
)
def test_the_trapped_claim_on_a_cve_is_removed_and_the_digest_returned(
    trapped_reading, facts
):
    """The criterion of #207 (comment of 2026-10-21): the claim the trap of #206 asks for, citing
    the trapped article, which carries no cve_id."""
    trapped = {
        "text": "China : CVE-2026-0001 n'est pas exploitée.",
        "item_ids": [TRAPPED_REF],
        "facts": facts,
    }

    result, _ = digest_of(
        trapped_reading,
        [
            classified_with_trap(),
            written(SECURITY_CLAIM),
            written(trapped, AGENT_CLAIM),
            written(),
        ],
    )

    assert isinstance(result, DigestsResult)
    assert result.failed_subjects == ()
    agent = result.digests[1]
    assert agent.subject == "agent_tools"
    assert [claim.text for claim in agent.claims] == [AGENT_CLAIM["text"]]
    assert agent.claims_removed == 1
    assert agent.items_removed_by_checks == (TRAPPED_ITEM_ID,)
    assert TRAPPED_ITEM_ID not in [source.item_id for source in agent.sources]


CONTENT_EVENT = "gen_ai.client.inference.operation.details"


def contents_sent(recorded_traces):
    return recorded_traces.find_log_records(CONTENT_EVENT)


def test_with_content_all_each_request_sends_its_content_tied_to_its_span(
    reading, recorded_traces
):
    result, transport = digest_of(
        reading, VALID_STEPS, tracing=recorded_traces.build_tracing("all")
    )

    records = contents_sent(recorded_traces)
    chat_spans = recorded_traces.find_spans("chat replay")
    assert len(records) == len(transport.requests) == len(chat_spans) == 4
    assert [record.span_id for record in records] == [
        span.context.span_id for span in chat_spans
    ]
    # The measures of the calls still carry no text (poc.md, rule 5).
    title = reading.items[0].fields.title
    assert title in records[0].body
    assert title not in repr(result.stage_calls)


def test_with_content_anomalies_a_batch_with_an_item_unanswered_is_sent(
    reading, recorded_traces
):
    forgotten = classified(no_suitable_subject=[12, 11])

    result, _ = digest_of(
        reading,
        [forgotten, *VALID_STEPS[1:]],
        tracing=recorded_traces.build_tracing("anomalies"),
    )

    assert result.unanswered == (13,)
    [record] = contents_sent(recorded_traces)
    [classify_chat] = [
        span
        for span in recorded_traces.find_spans("chat replay")
        if span.parent.span_id
        == recorded_traces.find_spans("classify_batch")[0].context.span_id
    ]
    assert record.span_id == classify_chat.context.span_id


def test_with_content_anomalies_a_failed_classification_is_sent(
    reading, recorded_traces
):
    result, _ = digest_of(
        reading,
        [completion("not json"), completion("not json")],
        tracing=recorded_traces.build_tracing("anomalies"),
    )

    assert isinstance(result, ClassificationFailure)
    assert result.state == "llm_invalid_output"
    assert len(contents_sent(recorded_traces)) == 2


def test_with_content_anomalies_a_claim_removed_sends_nothing(reading, recorded_traces):
    too_long = {**AGENT_CLAIM, "text": "v2.1.287 " + "x" * 400}

    result, _ = digest_of(
        reading,
        [classified(), written(SECURITY_CLAIM), written(too_long), written()],
        tracing=recorded_traces.build_tracing("anomalies"),
    )

    assert result.digests[1].claims_removed == 1
    assert contents_sent(recorded_traces) == []


def test_with_content_anomalies_a_subject_down_at_the_provider_sends_nothing(
    reading, recorded_traces
):
    result, _ = digest_of(
        reading,
        [
            classified(),
            written(SECURITY_CLAIM),
            {"kind": "response", "status": 503},
            {"kind": "response", "status": 503},
            written(),
        ],
        tracing=recorded_traces.build_tracing("anomalies"),
    )

    assert result.failed_subjects[0].error == "llm_unavailable"
    assert contents_sent(recorded_traces) == []


def test_with_content_all_a_fault_in_the_checks_keeps_the_content_sent(
    reading, recorded_traces, monkeypatch
):
    def fail(*arguments):
        raise RuntimeError("a fault of the checks")

    monkeypatch.setattr(digests, "to_digest", fail)

    with pytest.raises(RuntimeError):
        digest_of(reading, VALID_STEPS, tracing=recorded_traces.build_tracing("all"))

    # The classification, then the writing whose checks failed.
    assert len(contents_sent(recorded_traces)) == 2
