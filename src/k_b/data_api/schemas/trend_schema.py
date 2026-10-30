"""The response of GET /trends."""

from datetime import date

from pydantic import BaseModel, Field


class TrendSchema(BaseModel):
    repository_id: int
    repository_name: str
    stars_gained: int
    forks_gained: int


class TrendWindowSchema(BaseModel):
    window_end: date = Field(description="The last day of the window of 3 days.")
    # The most stars gained first, then the most forks, then by repository_id.
    trends: list[TrendSchema]
