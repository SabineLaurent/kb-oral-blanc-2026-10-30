"""The façade of the traces: a span per step, its failure named, its attributes cut, and the
evaluation of a claim removed tied to the request to the model."""

import json

import pytest
from opentelemetry._logs import SeverityNumber
from opentelemetry.trace import SpanKind, StatusCode

from k_b.ai_api.tracing import (
    TRACING_OFF,
    AttemptContent,
    build_content_body,
    count_fitting,
    cut_ids,
    cut_text,
    encode_content_body,
)
from k_b.core.config import MAX_CONTENT_EVENT_BYTES, MAX_TRACE_ATTRIBUTE_BYTES

CONTENT_EVENT = "gen_ai.client.inference.operation.details"


class ItemTextError(Exception):
    """An error whose text quotes an item, as a ValidationError would."""


def test_a_step_is_a_span_of_its_kind_with_its_attributes(recorded_traces):
    tracing = recorded_traces.trace_export.build_tracing()

    with tracing.step("chat replay", "client", {"gen_ai.request.model": "replay"}):
        pass

    [span] = recorded_traces.spans.get_finished_spans()
    assert (span.name, span.kind) == ("chat replay", SpanKind.CLIENT)
    assert dict(span.attributes) == {"gen_ai.request.model": "replay"}
    assert span.status.status_code is StatusCode.UNSET


def test_a_step_inside_another_is_its_child(recorded_traces):
    tracing = recorded_traces.trace_export.build_tracing()

    with tracing.step("POST /digests", "server"), tracing.step("read_watch"):
        pass

    child, parent = recorded_traces.spans.get_finished_spans()
    assert child.parent.span_id == parent.context.span_id


def test_a_failed_step_names_its_state_without_description(recorded_traces):
    tracing = recorded_traces.trace_export.build_tracing()

    with tracing.step("write_subject") as step:
        step.fail("llm_timeout")

    [span] = recorded_traces.spans.get_finished_spans()
    assert span.status.status_code is StatusCode.ERROR
    assert span.status.description is None
    assert span.attributes["error.type"] == "llm_timeout"


def test_an_exception_fails_the_step_by_its_type_never_its_text(recorded_traces):
    tracing = recorded_traces.trace_export.build_tracing()

    with pytest.raises(ItemTextError), tracing.step("POST /digests", "server"):
        raise ItemTextError("titre de l'item piégé")

    [span] = recorded_traces.spans.get_finished_spans()
    assert span.status.status_code is StatusCode.ERROR
    assert span.status.description is None
    assert span.attributes["error.type"] == "ItemTextError"
    assert span.events == ()


def test_an_exception_keeps_the_state_named_before(recorded_traces):
    tracing = recorded_traces.trace_export.build_tracing()

    with pytest.raises(ItemTextError), tracing.step("read_watch") as step:
        step.fail("data_api_timeout")
        raise ItemTextError("texte")

    [span] = recorded_traces.spans.get_finished_spans()
    assert span.attributes["error.type"] == "data_api_timeout"


def test_a_none_is_left_out_and_a_text_cut_in_bytes(recorded_traces):
    tracing = recorded_traces.trace_export.build_tracing()

    with tracing.step("chat replay") as step:
        step.set(
            {"k_b.failed_check": None, "k_b.subject": "é" * MAX_TRACE_ATTRIBUTE_BYTES}
        )

    [span] = recorded_traces.spans.get_finished_spans()
    assert "k_b.failed_check" not in span.attributes
    assert len(span.attributes["k_b.subject"].encode()) == MAX_TRACE_ATTRIBUTE_BYTES


def test_a_list_of_ids_is_cut_to_fit_and_says_it(recorded_traces):
    tracing = recorded_traces.trace_export.build_tracing()
    ids = list(range(10_000, 11_000))

    with tracing.step("classify_batch") as step:
        step.set_ids("k_b.items.unanswered.ids", ids)
        step.set_ids("k_b.items.no_suitable_subject.ids", [1, 2])

    [span] = recorded_traces.spans.get_finished_spans()
    kept = span.attributes["k_b.items.unanswered.ids"]
    assert list(kept) == ids[: len(kept)]
    assert len(",".join(map(str, kept))) <= MAX_TRACE_ATTRIBUTE_BYTES
    assert span.attributes["k_b.items.unanswered.ids_truncated"] is True
    assert list(span.attributes["k_b.items.no_suitable_subject.ids"]) == [1, 2]
    assert "k_b.items.no_suitable_subject.ids_truncated" not in span.attributes


def test_cut_ids_keeps_what_fits_exactly():
    assert cut_ids([12, 345]) == [12, 345]
    assert len(",".join(map(str, cut_ids([7] * 2000)))) <= MAX_TRACE_ATTRIBUTE_BYTES
    assert cut_ids([7] * 2000) == [7] * 1024


def test_cut_text_never_cuts_inside_a_character():
    assert cut_text("é" * 1025) == "é" * 1024


def test_an_evaluation_is_a_log_record_tied_to_the_end_of_its_span(recorded_traces):
    tracing = recorded_traces.trace_export.build_tracing()
    with tracing.step("chat replay", "client") as step:
        pass

    tracing.evaluate(step.ref, 3, ["out_of_bounds", "fact_not_found:0:17"])

    [span] = recorded_traces.spans.get_finished_spans()
    [record] = recorded_traces.log_records.get_finished_logs()
    log_record = record.log_record
    assert log_record.event_name == "gen_ai.evaluation.result"
    assert (log_record.trace_id, log_record.span_id) == (
        span.context.trace_id,
        span.context.span_id,
    )
    assert log_record.timestamp == span.end_time
    assert dict(log_record.attributes) == {
        "gen_ai.evaluation.name": "k_b.claim_checks",
        "gen_ai.evaluation.score.label": "fail",
        "gen_ai.evaluation.explanation": "out_of_bounds,fact_not_found:0:17",
        "k_b.claim.index": 3,
    }
    assert log_record.body == "out_of_bounds,fact_not_found:0:17"
    assert log_record.exception is None


def test_a_step_under_way_has_no_reference(recorded_traces):
    tracing = recorded_traces.trace_export.build_tracing()

    with tracing.step("chat replay") as step, pytest.raises(ValueError):
        _ = step.ref


def test_tracing_off_records_nothing_and_still_gives_a_reference():
    with TRACING_OFF.step("chat replay", "client") as step:
        step.set({"k_b.attempt": 1})
        step.set_ids("k_b.items.sent.ids", [1])
        step.fail("llm_timeout")

    TRACING_OFF.evaluate(step.ref, 0, ["disguised_text"])

    assert not step.ref.span_context.is_valid


def test_an_evaluation_keeps_its_first_codes_whole_and_says_when_it_cut(
    recorded_traces,
):
    tracing = recorded_traces.trace_export.build_tracing()
    with tracing.step("chat replay", "client") as step:
        pass
    # A claim of 200 facts not found: the schema sent bounds no list (writing.py).
    reasons = [f"fact_not_found:{index}:17" for index in range(200)]

    tracing.evaluate(step.ref, 0, reasons)

    [record] = recorded_traces.log_records.get_finished_logs()
    attributes = record.log_record.attributes
    codes = attributes["gen_ai.evaluation.explanation"].split(",")
    assert codes == reasons[: len(codes)]
    assert len(attributes["gen_ai.evaluation.explanation"].encode()) <= (
        MAX_TRACE_ATTRIBUTE_BYTES
    )
    assert attributes["k_b.claim.reasons_truncated"] is True


def test_count_fitting_counts_the_parts_that_fit_whole():
    assert count_fitting([]) == 0
    assert count_fitting(["a" * MAX_TRACE_ATTRIBUTE_BYTES]) == 1
    assert count_fitting(["a" * MAX_TRACE_ATTRIBUTE_BYTES, "b"]) == 1
    assert count_fitting(["é" * 1024, "b"]) == 1


def ended_step_ref(tracing):
    with tracing.step("chat replay", "client") as step:
        pass
    return step.ref


def test_a_content_is_a_debug_log_record_tied_to_the_end_of_its_span(recorded_traces):
    tracing = recorded_traces.build_tracing("all")
    ref = ended_step_ref(tracing)

    tracing.record_content(
        AttemptContent(ref, "Sort the items.", '{"items": []}', '{"i1": "models"}')
    )

    [span] = recorded_traces.spans.get_finished_spans()
    [log_record] = recorded_traces.find_log_records(CONTENT_EVENT)
    assert (log_record.trace_id, log_record.span_id) == (
        span.context.trace_id,
        span.context.span_id,
    )
    assert log_record.timestamp == span.end_time
    assert log_record.severity_number is SeverityNumber.DEBUG
    assert dict(log_record.attributes) == {"gen_ai.operation.name": "chat"}
    assert json.loads(log_record.body) == {
        "system_instructions": [{"type": "text", "content": "Sort the items."}],
        "input_messages": [
            {"role": "user", "parts": [{"type": "text", "content": '{"items": []}'}]}
        ],
        "output_messages": [
            {
                "role": "assistant",
                "parts": [{"type": "text", "content": '{"i1": "models"}'}],
            }
        ],
    }


def test_a_content_without_output_text_has_no_output_message():
    body, is_cut = build_content_body(
        AttemptContent(ended_step_ref(TRACING_OFF), "Sort.", "{}", None)
    )

    assert json.loads(body)["output_messages"] == []
    assert is_cut is False


def test_a_content_beyond_the_maximum_is_cut_keeping_its_structure(recorded_traces):
    tracing = recorded_traces.build_tracing("all")
    # Each character takes two bytes once in JSON: é in UTF-8, the quote and the line feed
    # escaped, as in the user message, itself JSON.
    long_text = 'é"\n' * 100_000
    content = AttemptContent(
        ended_step_ref(tracing), "Sort the items.", long_text, long_text
    )

    tracing.record_content(content)

    [log_record] = recorded_traces.find_log_records(CONTENT_EVENT)
    assert len(log_record.body.encode()) <= MAX_CONTENT_EVENT_BYTES
    body = json.loads(log_record.body)
    assert list(body) == ["system_instructions", "input_messages", "output_messages"]
    [system_part] = body["system_instructions"]
    [user_part] = body["input_messages"][0]["parts"]
    [output_part] = body["output_messages"][0]["parts"]
    assert system_part["content"] == "Sort the items."
    assert long_text.startswith(user_part["content"])
    assert user_part["content"] == output_part["content"]
    # The longest cut that fits: one character more would not.
    kept = len(user_part["content"])
    assert (
        len(encode_content_body(content, kept + 1).encode()) > MAX_CONTENT_EVENT_BYTES
    )
    assert log_record.attributes["k_b.content_truncated"] is True


def test_a_content_within_the_maximum_is_whole():
    text = "é" * 1000
    body, is_cut = build_content_body(
        AttemptContent(ended_step_ref(TRACING_OFF), text, text, text)
    )

    assert is_cut is False
    assert json.loads(body)["input_messages"][0]["parts"][0]["content"] == text


def test_the_repr_of_a_content_holds_no_text():
    content = AttemptContent(
        ended_step_ref(TRACING_OFF), "consigne", "item piégé", "sortie"
    )

    assert not any(text in repr(content) for text in ("consigne", "piégé", "sortie"))
