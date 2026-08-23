from __future__ import annotations

from pydantic import BaseModel, Field


class LeadDecisionPayload(BaseModel):
    is_potential_customer: bool = Field(
        description="True only when the author is seeking the configured service"
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description=(
            "Probability from 0 to 1 that the author is a potential customer; "
            "it must agree with is_potential_customer"
        ),
    )
    is_provider_ad: bool
    reason: str = Field(max_length=500)
    intent: str | None = None
    location: str | None = None
    event_date_text: str | None = None
    party_size: int | None = Field(default=None, ge=1, le=1000)
    language: str | None = Field(
        default=None,
        description="ISO 639-1 language code of the message, such as en, es, pt or ru",
    )


def lead_probability(payload: LeadDecisionPayload) -> float:
    """Make the boolean decision authoritative even if model confidence is inconsistent."""
    score = payload.confidence
    if not payload.is_potential_customer:
        score = min(score, 0.05)
    if payload.is_provider_ad:
        score = min(score, 0.05)
    return max(0.0, min(1.0, score))
