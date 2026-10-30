"""The traces of a digest request: a span per step and per request to the model, the evaluation of
each claim removed, and, when KB_TRACE_CONTENT lets it, the content of a request to the model, each a
log record tied to its span (docs/fiches-veille.md, "Traces"). The API of OpenTelemetry only; its
SDK is in connectors/trace_export.py."""

import json
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Literal

from opentelemetry import trace
from opentelemetry._logs import Logger, NoOpLogger, SeverityNumber
from opentelemetry.trace import (
    NonRecordingSpan,
    NoOpTracer,
    Span,
    SpanContext,
    SpanKind,
    Status,
    StatusCode,
    Tracer,
)

from k_b.core.config import (
    MAX_CONTENT_EVENT_BYTES,
    MAX_TRACE_ATTRIBUTE_BYTES,
    TraceContent,
)

# The instrumentation scope of the spans and of the log records.
SCOPE_NAME = "k_b.ai_api"
type StepKind = Literal["server", "internal", "client"]
SPAN_KIND_BY_STEP_KIND: Mapping[StepKind, SpanKind] = {
    "server": SpanKind.SERVER,
    "internal": SpanKind.INTERNAL,
    "client": SpanKind.CLIENT,
}
type AttributeValue = str | bool | int | Sequence[str]
ERROR_TYPE = "error.type"
# Added beside a list of identifiers cut to MAX_TRACE_ATTRIBUTE_BYTES.
TRUNCATED_SUFFIX = "_truncated"
IDENTIFIER_SEPARATOR = ","
EVALUATION_EVENT = "gen_ai.evaluation.result"
# One evaluation per claim: its bounds and its fidelity (stages/checking.py).
CLAIM_CHECKS_EVALUATION = "k_b.claim_checks"
FAILED_LABEL = "fail"
# Beside the explanation of an evaluation, when its codes did not all fit.
REASONS_TRUNCATED = "k_b.claim.reasons_truncated"
# The content of a request to the model: the event of the semantic conventions for generative AI,
# its texts in the body, which Loki keeps up to 256 KB, not in attributes (.claude/rules/poc.md,
# rule 5, exception).
CONTENT_EVENT = "gen_ai.client.inference.operation.details"
CONTENT_TRUNCATED = "k_b.content_truncated"


@dataclass(frozen=True)
class SpanRef:
    """A span ended, to tie a log record to it: its context and the instant it ended."""

    span_context: SpanContext
    ended_ns: int


@dataclass(frozen=True)
class AttemptContent:
    """The texts of a request to the model, kept when KB_TRACE_CONTENT lets it: the system prompt
    sent, its reminder too on a retry, the user message, and the output of a 200, None otherwise
    (the body of an error may quote the input). Never in a repr."""

    span_ref: SpanRef
    system_prompt: str = field(repr=False)
    user_message: str = field(repr=False)
    output_text: str | None = field(repr=False)


class Step:
    """A span under way: its attributes set as the step learns them, its failure named."""

    def __init__(self, span: Span) -> None:
        self._span = span
        self._has_failed = False
        self._ended_ns: int | None = None

    def set(self, attributes: Mapping[str, AttributeValue | None]) -> None:
        """Set the attributes, a None left out, a text cut to MAX_TRACE_ATTRIBUTE_BYTES."""
        for name, value in attributes.items():
            if isinstance(value, str):
                self._span.set_attribute(name, cut_text(value))
            elif value is not None:
                self._span.set_attribute(name, value)

    def set_ids(self, name: str, ids: Sequence[int]) -> None:
        """Set a list of identifiers, cut to fit MAX_TRACE_ATTRIBUTE_BYTES once written with
        commas; name_truncated says it was."""
        kept = cut_ids(ids)
        self._span.set_attribute(name, kept)
        if len(kept) < len(ids):
            self._span.set_attribute(f"{name}{TRUNCATED_SUFFIX}", True)

    def fail(self, error_type: str) -> None:
        """The step ended in a named state: the status ERROR, without description, which could
        quote a text, and error.type."""
        self._has_failed = True
        self._span.set_status(Status(StatusCode.ERROR))
        self._span.set_attribute(ERROR_TYPE, cut_text(error_type))

    def end(self, error: Exception | None) -> None:
        """End the span; an exception fails it by its type, unless a state was named before."""
        if error is not None and not self._has_failed:
            self.fail(type(error).__name__)
        self._ended_ns = time.time_ns()
        self._span.end(end_time=self._ended_ns)

    @property
    def ref(self) -> SpanRef:
        """The span once ended; before, a fault of the caller, raised."""
        if self._ended_ns is None:
            raise ValueError("a step under way has no reference")
        return SpanRef(self._span.get_span_context(), self._ended_ns)


@dataclass(frozen=True)
class Tracing:
    """The spans and the log records of the AI API; each function that takes it gets it by
    tracing=, never by a global provider."""

    tracer: Tracer
    logger: Logger
    # Whose content the requests to the model keep; the caller of record_content decides which
    # are sent (connectors/llm_client.py, trace_contents).
    content: TraceContent = "none"

    @contextmanager
    def step(
        self,
        name: str,
        kind: StepKind = "internal",
        attributes: Mapping[str, AttributeValue | None] | None = None,
    ) -> Iterator[Step]:
        """A span, current for the time of the block and ended with it, never recording an
        exception, whose text could quote an item."""
        span = self.tracer.start_span(
            name,
            kind=SPAN_KIND_BY_STEP_KIND[kind],
            record_exception=False,
            set_status_on_exception=False,
        )
        step = Step(span)
        if attributes is not None:
            step.set(attributes)
        error: Exception | None = None
        with trace.use_span(
            span, record_exception=False, set_status_on_exception=False
        ):
            try:
                yield step
            except Exception as raised:
                error = raised
                raise
            finally:
                step.end(error)

    def evaluate(self, ref: SpanRef, claim_index: int, reasons: Sequence[str]) -> None:
        """The evaluation of a claim the checks removed, a log record tied to the request to the
        model that wrote it, at the instant it ended; its reasons are codes, never a text, the
        first that fit kept whole."""
        kept = reasons[: count_fitting(reasons)]
        explanation = IDENTIFIER_SEPARATOR.join(kept)
        attributes: dict[str, str | int | bool] = {
            "gen_ai.evaluation.name": CLAIM_CHECKS_EVALUATION,
            "gen_ai.evaluation.score.label": FAILED_LABEL,
            "gen_ai.evaluation.explanation": explanation,
            "k_b.claim.index": claim_index,
        }
        if len(kept) < len(reasons):
            attributes[REASONS_TRUNCATED] = True
        self.logger.emit(
            timestamp=ref.ended_ns,
            context=trace.set_span_in_context(NonRecordingSpan(ref.span_context)),
            severity_number=SeverityNumber.INFO,
            # The codes in the body too: a log record without body is not shown by every reader.
            body=explanation,
            attributes=attributes,
            event_name=EVALUATION_EVENT,
        )

    def record_content(self, content: AttemptContent) -> None:
        """The content of a request to the model, a log record tied to its span at the instant it
        ended: its texts in a JSON body shaped as the messages of the conventions, cut to
        MAX_CONTENT_EVENT_BYTES; its attributes say only that it was."""
        body, is_cut = build_content_body(content)
        attributes: dict[str, str | bool] = {"gen_ai.operation.name": "chat"}
        if is_cut:
            attributes[CONTENT_TRUNCATED] = True
        self.logger.emit(
            timestamp=content.span_ref.ended_ns,
            context=trace.set_span_in_context(
                NonRecordingSpan(content.span_ref.span_context)
            ),
            severity_number=SeverityNumber.DEBUG,
            body=body,
            attributes=attributes,
            event_name=CONTENT_EVENT,
        )


# Nothing recorded: the default of the functions that take tracing, for their tests.
TRACING_OFF = Tracing(NoOpTracer(), NoOpLogger(SCOPE_NAME))


def cut_text(text: str) -> str:
    """The text cut to MAX_TRACE_ATTRIBUTE_BYTES of UTF-8, never inside a character."""
    return text.encode()[:MAX_TRACE_ATTRIBUTE_BYTES].decode(errors="ignore")


def cut_ids(ids: Sequence[int]) -> list[int]:
    """The first identifiers that fit MAX_TRACE_ATTRIBUTE_BYTES once written with commas."""
    return list(ids[: count_fitting([str(item_id) for item_id in ids])])


def count_fitting(parts: Sequence[str]) -> int:
    """How many of the first parts fit MAX_TRACE_ATTRIBUTE_BYTES of UTF-8 once joined with
    commas, none cut."""
    length = -len(IDENTIFIER_SEPARATOR)
    for count, part in enumerate(parts):
        length += len(IDENTIFIER_SEPARATOR) + len(part.encode())
        if length > MAX_TRACE_ATTRIBUTE_BYTES:
            return count
    return len(parts)


def build_content_body(content: AttemptContent) -> tuple[str, bool]:
    """The body of a content record, in JSON, and whether its texts were cut: each to the longest
    length that fits MAX_CONTENT_EVENT_BYTES, the same for all, every key and part kept."""
    body = encode_content_body(content, None)
    if len(body.encode()) <= MAX_CONTENT_EVENT_BYTES:
        return body, False
    # The size grows with the length kept, and a length of 0 fits: the structure is small.
    low, high = (
        0,
        max(
            len(content.system_prompt),
            len(content.user_message),
            len(content.output_text or ""),
        ),
    )
    while low < high:
        middle = (low + high + 1) // 2
        if (
            len(encode_content_body(content, middle).encode())
            <= MAX_CONTENT_EVENT_BYTES
        ):
            low = middle
        else:
            high = middle - 1
    return encode_content_body(content, low), True


def encode_content_body(content: AttemptContent, max_chars: int | None) -> str:
    """The texts as the conventions shape gen_ai.system_instructions, gen_ai.input.messages and
    gen_ai.output.messages, each cut to max_chars characters if given; no output without a text."""

    def part(text: str) -> dict[str, str]:
        return {"type": "text", "content": text[:max_chars]}

    output_messages = (
        []
        if content.output_text is None
        else [{"role": "assistant", "parts": [part(content.output_text)]}]
    )
    return json.dumps(
        {
            "system_instructions": [part(content.system_prompt)],
            "input_messages": [{"role": "user", "parts": [part(content.user_message)]}],
            "output_messages": output_messages,
        },
        ensure_ascii=False,
    )
