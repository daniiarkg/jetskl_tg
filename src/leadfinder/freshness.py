from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum

from leadfinder.config import Settings


class FreshnessBand(StrEnum):
    HOT = "hot"
    ACTIVE = "active"
    REVIEW = "review"
    STALE = "stale"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class FreshnessDecision:
    band: FreshnessBand
    notification_eligible: bool
    automatic_lead_eligible: bool
    reason: str


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _parse_event_date(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return _as_utc(value)
    if isinstance(value, date):
        return datetime.combine(value, time.min, tzinfo=UTC)
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        try:
            parsed_date = date.fromisoformat(normalized)
        except ValueError:
            return None
        return datetime.combine(parsed_date, time.min, tzinfo=UTC)
    return _as_utc(parsed)


def has_future_event_date(
    extracted_data: Mapping[str, object] | None,
    *,
    now: datetime | None = None,
) -> bool:
    """Return true only for an explicit event date after the current instant."""
    if not extracted_data:
        return False
    reference = _as_utc(now or datetime.now(UTC))
    event_date = _parse_event_date(extracted_data.get("event_date"))
    return event_date is not None and event_date > reference


def assess_freshness(
    settings: Settings,
    message_date: datetime | None,
    extracted_data: Mapping[str, object] | None = None,
    *,
    now: datetime | None = None,
) -> FreshnessDecision:
    """Apply the deterministic age gate before lead creation or notification."""
    if message_date is None:
        return FreshnessDecision(
            band=FreshnessBand.UNKNOWN,
            notification_eligible=False,
            automatic_lead_eligible=False,
            reason="message-date-missing",
        )

    reference = _as_utc(now or datetime.now(UTC))
    published = _as_utc(message_date)
    age = max(reference - published, timedelta(0))

    if age <= timedelta(days=settings.lead_hot_max_age_days):
        return FreshnessDecision(
            band=FreshnessBand.HOT,
            notification_eligible=True,
            automatic_lead_eligible=True,
            reason="hot",
        )
    if age <= timedelta(days=settings.lead_active_max_age_days):
        return FreshnessDecision(
            band=FreshnessBand.ACTIVE,
            notification_eligible=True,
            automatic_lead_eligible=True,
            reason="active",
        )
    if age <= timedelta(days=settings.lead_review_max_age_days):
        future_event = has_future_event_date(extracted_data, now=reference)
        return FreshnessDecision(
            band=FreshnessBand.REVIEW,
            notification_eligible=future_event,
            automatic_lead_eligible=False,
            reason="review-future-event" if future_event else "review-no-future-event",
        )
    return FreshnessDecision(
        band=FreshnessBand.STALE,
        notification_eligible=False,
        automatic_lead_eligible=False,
        reason="older-than-review-window",
    )
