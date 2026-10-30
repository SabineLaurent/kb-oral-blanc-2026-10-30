"""The errors of the AI API that the model or the data API gives; 502, 503 and 504 say the
failure upstream, as a gateway (RFC 9110, § 15.6)."""

from itertools import groupby
from typing import Any

from k_b.ai_api.connectors.data_api_client import DataApiState
from k_b.ai_api.connectors.llm_client import LlmFailure
from k_b.core.api_errors import API_ERROR_RESPONSES, ApiError, ErrorSchema

# The states of a reading or of a classification that fails the request; a writing that fails
# names its subject in the response instead.
STATUS_BY_STATE: dict[LlmFailure | DataApiState, int] = {
    "data_api_invalid_response": 502,
    "data_api_timeout": 504,
    "data_api_unauthorized": 502,
    "data_api_unavailable": 502,
    "llm_content_filtered": 502,
    "llm_invalid_output": 502,
    "llm_rate_limited": 503,
    "llm_timeout": 504,
    "llm_truncated": 502,
    "llm_unavailable": 502,
}


def to_api_error(
    state: LlmFailure | DataApiState, retry_after_seconds: int | None = None
) -> ApiError:
    """The error of the state; a Retry-After only when the provider gave one."""
    headers = (
        None
        if retry_after_seconds is None
        else {"Retry-After": str(retry_after_seconds)}
    )
    return ApiError(STATUS_BY_STATE[state], state, headers)


def describe_upstream_errors() -> dict[int | str, dict[str, Any]]:
    """The states of each upstream status, for the OpenAPI."""
    by_status = sorted(STATUS_BY_STATE.items(), key=lambda pair: (pair[1], pair[0]))
    return {
        status: {
            "model": ErrorSchema,
            "description": ", ".join(state for state, _ in pairs),
        }
        for status, pairs in groupby(by_status, key=lambda pair: pair[1])
    }


# The error bodies of POST /digests, declared in the OpenAPI.
ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    **API_ERROR_RESPONSES,
    500: {"model": ErrorSchema, "description": "internal_error"},
    **describe_upstream_errors(),
}
