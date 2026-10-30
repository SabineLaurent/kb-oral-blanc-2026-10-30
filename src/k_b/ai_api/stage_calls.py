"""The calls of the stages to the model, and what the metric lines and the response read of
them."""

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel

from k_b.ai_api.connectors.llm_client import LlmCall

type Stage = Literal["classify", "write"]


@dataclass(frozen=True)
class StageCall:
    """A call to the model and what its metric line needs of it."""

    stage: Stage
    # None for the classification.
    subject: str | None
    prompt_version: str
    items_in: int
    # Items of this call with a field cut: at 500 characters for the classification, at 2,000
    # for the writing.
    items_truncated: int
    # Of a writing whose output is checked; None otherwise. items_removed_by_checks counts the
    # items no longer cited once the claims out of their bounds or unfaithful are removed.
    facts_checked: int | None
    facts_found: int | None
    claims_removed: int | None
    items_removed_by_checks: int | None
    # Of a classification whose output is checked; None otherwise: the items given a subject,
    # those no subject suits, and those the model left without answer, items_in in all.
    items_with_subject: int | None
    items_no_suitable_subject: int | None
    items_unanswered: int | None
    llm_call: LlmCall[BaseModel]
