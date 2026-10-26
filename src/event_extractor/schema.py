from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class EventExtraction(BaseModel):
    """The business contract. Unknown values are explicit, never guessed."""

    model_config = ConfigDict(extra="forbid")

    event_type: str = Field(description="Normalized event type, e.g. product_demo")
    date: str | None = Field(
        description="Resolved ISO date YYYY-MM-DD; null when it cannot be determined"
    )
    period: Literal["morning", "afternoon", "evening", "all_day", "unknown"]
    attendees: list[str] = Field(description="Explicitly mentioned attendee names")

    @field_validator("date")
    @classmethod
    def validate_iso_date(cls, value: str | None) -> str | None:
        if value is not None:
            datetime.strptime(value, "%Y-%m-%d")
        return value
