"""The metric lines of the AI API: one JSON line per call to the model, on stdout, with measures
and states, never a text nor a key (.claude/rules/poc.md, rules 4 and 5)."""

import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from k_b.ai_api.connectors.llm_settings import LlmSettings
from k_b.ai_api.stage_calls import StageCall
from k_b.core.config import (
    LLM_INPUT_USD_PER_MILLION_TOKENS,
    LLM_OUTPUT_USD_PER_MILLION_TOKENS,
)

LLM_CALL_EVENT = "llm_call"
TOKENS_PER_MILLION = 1_000_000
# A millionth of a dollar: below, a call of a few tokens.
COST_DECIMALS = 6
# A percentage to two decimals: enough to read the threshold of 95 % (#210).
FIDELITY_RATE_DECIMALS = 4


@dataclass(frozen=True)
class RequestMeasures:
    """The measures of the request, on the first line of classification only."""

    data_fetch_ms: int
    items_dropped_before_writing_stage: int
    items_capped: bool


def write_llm_call_lines(
    request_id: str,
    settings: LlmSettings,
    stage_calls: Sequence[StageCall],
    request_measures: RequestMeasures,
) -> None:
    """One line per call, each written and flushed at once, as a line kept in a pipe buffer would
    be lost on SIGTERM."""
    for position, stage_call in enumerate(stage_calls):
        line = build_llm_call_line(
            request_id,
            settings,
            stage_call,
            request_measures if position == 0 else None,
        )
        sys.stdout.write(json.dumps(line, ensure_ascii=False) + "\n")
        sys.stdout.flush()


def build_llm_call_line(
    request_id: str,
    settings: LlmSettings,
    stage_call: StageCall,
    request_measures: RequestMeasures | None,
) -> dict[str, object]:
    """The fields of the spec, in its order."""
    call = stage_call.llm_call
    return {
        "ts": format_timestamp(datetime.now(UTC)),
        "event": LLM_CALL_EVENT,
        "request_id": request_id,
        "stage": stage_call.stage,
        "subject": stage_call.subject,
        "provider": settings.provider,
        "model": settings.model,
        "prompt_version": stage_call.prompt_version,
        "items_in": stage_call.items_in,
        "items_truncated": stage_call.items_truncated,
        "items_dropped_before_writing_stage": None
        if request_measures is None
        else request_measures.items_dropped_before_writing_stage,
        "items_capped": None
        if request_measures is None
        else request_measures.items_capped,
        "input_chars": call.input_chars,
        "tokens_in": call.tokens_in,
        "tokens_out": call.tokens_out,
        "reasoning_tokens": call.reasoning_tokens,
        "data_fetch_ms": None
        if request_measures is None
        else request_measures.data_fetch_ms,
        "llm_ms": call.llm_ms,
        "check_ms": call.check_ms,
        "attempts": call.attempts,
        "outcome": call.outcome,
        "finish_reason": call.finish_reason,
        "http_status": call.http_status,
        "schema_valid": call.schema_valid,
        "failed_check": call.failed_check,
        "first_failed_check": call.first_failed_check,
        "facts_checked": stage_call.facts_checked,
        "facts_found": stage_call.facts_found,
        "fidelity_rate": compute_fidelity_rate(
            stage_call.facts_found, stage_call.facts_checked
        ),
        "claims_removed": stage_call.claims_removed,
        "items_removed_by_checks": stage_call.items_removed_by_checks,
        "items_with_subject": stage_call.items_with_subject,
        "items_no_suitable_subject": stage_call.items_no_suitable_subject,
        "items_unanswered": stage_call.items_unanswered,
        "cost_estimated": estimate_cost(call.tokens_in, call.tokens_out),
    }


def estimate_cost(tokens_in: int, tokens_out: int) -> float:
    """In USD, at the prices of core/config.py: the reasoning tokens are in tokens_out."""
    cost = (
        tokens_in * LLM_INPUT_USD_PER_MILLION_TOKENS
        + tokens_out * LLM_OUTPUT_USD_PER_MILLION_TOKENS
    ) / TOKENS_PER_MILLION
    return round(cost, COST_DECIMALS)


def compute_fidelity_rate(
    facts_found: int | None, facts_checked: int | None
) -> float | None:
    """The facts found over the facts checked; None without a fact checked."""
    if facts_found is None or not facts_checked:
        return None
    return round(facts_found / facts_checked, FIDELITY_RATE_DECIMALS)


def format_timestamp(moment: datetime) -> str:
    """ISO 8601 in UTC, to the second, with its zone (conventions.md, rule 3)."""
    return moment.astimezone(UTC).replace(microsecond=0, tzinfo=None).isoformat() + "Z"
