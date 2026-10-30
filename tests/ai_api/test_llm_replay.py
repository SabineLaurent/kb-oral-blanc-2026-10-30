"""The replay provider: the steps of a file, checked, then given in order."""

import json

import httpx2
import pytest

from k_b.ai_api.connectors.llm_replay import (
    InvalidReplayFileError,
    ReplayExhaustedError,
    ReplayTransport,
    read_replay_steps,
)
from k_b.core.config import LLM_REPLAY_BASE_URL

STEPS = [
    {"kind": "completion", "content": '{"subject": "models", "count": 2}'},
    {"kind": "response", "status": 429, "retry_after": "2"},
    {"kind": "timeout"},
    {"kind": "transport_error", "error": "connect"},
]


def test_the_steps_of_a_file_are_read(tmp_path):
    path = tmp_path / "replay.json"
    path.write_text(json.dumps(STEPS))

    steps = read_replay_steps(path)

    assert [step.kind for step in steps] == [
        "completion",
        "response",
        "timeout",
        "transport_error",
    ]


@pytest.mark.parametrize(
    ("steps", "message"),
    [
        ([{"kind": "sleep"}], "step 1: union_tag_invalid; errors in the file: 1"),
        (
            [STEPS[0], {"kind": "timeout", "seconds": 3}],
            "step 2: extra_forbidden at timeout.seconds; errors in the file: 1",
        ),
        (
            [{"kind": "response", "status": 99}],
            "step 1: greater_than_equal at response.status; errors in the file: 1",
        ),
        ({"kind": "timeout"}, "list_type; errors in the file: 1"),
    ],
    ids=["unknown-kind", "field-in-excess", "status-out-of-range", "not-a-list"],
)
def test_a_file_out_of_the_model_is_refused_without_its_values(
    tmp_path, steps, message
):
    path = tmp_path / "replay.json"
    path.write_text(json.dumps(steps))

    with pytest.raises(InvalidReplayFileError, match=message):
        read_replay_steps(path)


def test_the_steps_are_given_in_order_then_refused_once_spent():
    transport = ReplayTransport(STEPS[:2])

    with httpx2.Client(transport=transport, base_url=LLM_REPLAY_BASE_URL) as client:
        assert client.post("chat/completions", json={}).status_code == 200
        rate_limited = client.post("chat/completions", json={})
        with pytest.raises(ReplayExhaustedError, match="2 steps given, request 3"):
            client.post("chat/completions", json={})

    assert rate_limited.status_code == 429
    assert rate_limited.headers["Retry-After"] == "2"
    assert len(transport.requests) == 3


@pytest.mark.parametrize(
    ("step", "error"),
    [
        ({"kind": "timeout"}, httpx2.ReadTimeout),
        ({"kind": "timeout", "phase": "connect"}, httpx2.ConnectTimeout),
        ({"kind": "transport_error", "error": "connect"}, httpx2.ConnectError),
        (
            {"kind": "transport_error", "error": "remote_protocol"},
            httpx2.RemoteProtocolError,
        ),
    ],
)
def test_a_failure_of_the_provider_is_raised_by_the_transport(step, error):
    with (
        httpx2.Client(
            transport=ReplayTransport([step]), base_url=LLM_REPLAY_BASE_URL
        ) as client,
        pytest.raises(error),
    ):
        client.post("chat/completions", json={})
