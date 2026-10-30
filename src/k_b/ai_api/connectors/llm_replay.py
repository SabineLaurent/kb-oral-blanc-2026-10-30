"""The replay provider: answers known in advance, given in order by the transport of the HTTP
client, so that a real call runs whole, without network, key nor cost; the steps assume calls one
after the other."""

from pathlib import Path
from typing import Annotated, Literal

import httpx2
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError


class CompletionStep(BaseModel):
    """A chat completion of the provider, with one choice."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["completion"]
    content: str | None
    finish_reason: str | None = "stop"
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    # Not given by every provider.
    reasoning_tokens: int | None = Field(default=None, ge=0)


class ResponseStep(BaseModel):
    """Any HTTP response: a 429 with its Retry-After, a 5xx, an error, a body that is not a
    completion."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["response"]
    status: int = Field(ge=100, le=599)
    body: str = ""
    retry_after: str | None = None


class TimeoutStep(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["timeout"]
    phase: Literal["connect", "read"] = "read"


class TransportErrorStep(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["transport_error"]
    error: Literal["connect", "remote_protocol"]


type ReplayStep = Annotated[
    CompletionStep | ResponseStep | TimeoutStep | TransportErrorStep,
    Field(discriminator="kind"),
]
REPLAY_STEPS = TypeAdapter(list[ReplayStep])


class InvalidReplayFileError(Exception):
    """A file of steps out of the model: the position, the type of error and the field, never the
    value."""


class ReplayExhaustedError(Exception):
    """A request beyond the last step: a fault of the steps given, not a state of the model."""


def read_replay_steps(path: Path) -> list[ReplayStep]:
    try:
        return REPLAY_STEPS.validate_json(path.read_bytes())
    except ValidationError as error:
        raise InvalidReplayFileError(describe_first_error(error)) from None


def describe_first_error(error: ValidationError) -> str:
    """The first error: the step, its type and its field, without the value."""
    first = error.errors(include_url=False, include_input=False)[0]
    count = f"errors in the file: {error.error_count()}"
    location = list(first["loc"])
    if not location:
        return f"{first['type']}; {count}"
    step = f"step {int(location.pop(0)) + 1}: {first['type']}"
    field = ".".join(str(part) for part in location)
    return f"{step} at {field}; {count}" if field else f"{step}; {count}"


class ReplayTransport(httpx2.MockTransport):
    """Gives the steps in order, one per request, and keeps each request."""

    def __init__(self, steps: list[ReplayStep] | list[dict[str, object]]) -> None:
        super().__init__(self.answer)
        self.steps: list[ReplayStep] = REPLAY_STEPS.validate_python(steps)
        self.requests: list[httpx2.Request] = []

    def answer(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        if len(self.requests) > len(self.steps):
            raise ReplayExhaustedError(
                f"{len(self.steps)} steps given, request {len(self.requests)}"
            )
        step = self.steps[len(self.requests) - 1]
        match step:
            case CompletionStep():
                return httpx2.Response(200, json=build_completion(step))
            case ResponseStep():
                headers = (
                    {}
                    if step.retry_after is None
                    else {"Retry-After": step.retry_after}
                )
                return httpx2.Response(step.status, headers=headers, text=step.body)
            case TimeoutStep(phase="connect"):
                raise httpx2.ConnectTimeout("replay: connect timeout", request=request)
            case TimeoutStep():
                raise httpx2.ReadTimeout("replay: read timeout", request=request)
            case TransportErrorStep(error="connect"):
                raise httpx2.ConnectError("replay: connection refused", request=request)
            case TransportErrorStep():
                raise httpx2.RemoteProtocolError(
                    "replay: connection broken", request=request
                )


def build_completion(step: CompletionStep) -> dict[str, object]:
    """A chat completion as an OpenAI-compatible API gives it."""
    usage: dict[str, object] = {
        "prompt_tokens": step.prompt_tokens,
        "completion_tokens": step.completion_tokens,
    }
    if step.reasoning_tokens is not None:
        usage["completion_tokens_details"] = {"reasoning_tokens": step.reasoning_tokens}
    return {
        "object": "chat.completion",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": step.content},
                "finish_reason": step.finish_reason,
            }
        ],
        "usage": usage,
    }
