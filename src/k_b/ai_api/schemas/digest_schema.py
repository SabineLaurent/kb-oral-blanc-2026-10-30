"""The request and the response of POST /digests (docs/fiches-veille.md)."""

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from k_b.ai_api.connectors.llm_client import LlmFailure
from k_b.ai_api.stage_calls import Stage
from k_b.ai_api.watch_items import WatchKind
from k_b.core.config import MAX_DIGEST_WINDOW_DAYS, MIN_DIGEST_WINDOW_DAYS

WindowDays = Annotated[int, Field(ge=MIN_DIGEST_WINDOW_DAYS, le=MAX_DIGEST_WINDOW_DAYS)]


class DigestRequestSchema(BaseModel):
    # Any other field refused; strict: true, 3.0 and "3" are not integers.
    model_config = ConfigDict(extra="forbid", strict=True)

    window_days: WindowDays | None = Field(
        default=None,
        description="Absent or null: the window of KB_DIGEST_WINDOW_DAYS.",
    )


class WindowSchema(BaseModel):
    days: int
    since: datetime
    until: datetime
    capped_kind: WatchKind | None = Field(
        description="The kind cut by the cap of the reading; null when nothing is cut."
    )
    covered_after: datetime | None = Field(
        description="The date of the first item of the kind cut left out, excluded: each item"
        " of that kind strictly more recent was read; null when nothing is cut."
    )


class ClaimSchema(BaseModel):
    text: str
    item_ids: list[int]


class SourceSchema(BaseModel):
    """Read from the data API, never from the output of the model."""

    item_id: int
    kind: WatchKind
    title: str
    url: str | None


class DigestSchema(BaseModel):
    subject: str
    claims: list[ClaimSchema]
    sources: list[SourceSchema]
    items_not_retained_in_output: list[int]
    claims_removed: int
    items_removed_by_checks: list[int]


class FailedSubjectSchema(BaseModel):
    subject: str
    stage: Stage
    error: LlmFailure


class DigestsSchema(BaseModel):
    request_id: str
    model: str = Field(
        description="The model of KB_LLM_MODEL that wrote the digests; replay for the replay."
    )
    window: WindowSchema
    digests: list[DigestSchema]
    failed_subjects: list[FailedSubjectSchema]
    no_suitable_subject: list[int] = Field(
        description="Items of the classification that no subject suits."
    )
    unanswered: list[int] = Field(
        description="Items the classification left without answer, in no digest either."
    )
    items_in: int
    items_truncated: int
    items_dropped_before_writing_stage: int
    items_capped: bool
