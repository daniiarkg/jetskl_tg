from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from leadfinder.config import Settings
from leadfinder.freshness import FreshnessBand, assess_freshness


def test_freshness_bands_and_automatic_lead_gate() -> None:
    now = datetime(2026, 8, 26, 12, tzinfo=UTC)
    settings = Settings()

    hot = assess_freshness(settings, now - timedelta(days=7), now=now)
    active = assess_freshness(settings, now - timedelta(days=8), now=now)
    review = assess_freshness(settings, now - timedelta(days=31), now=now)
    stale = assess_freshness(settings, now - timedelta(days=91), now=now)

    assert hot.band == FreshnessBand.HOT
    assert hot.notification_eligible and hot.automatic_lead_eligible
    assert active.band == FreshnessBand.ACTIVE
    assert active.notification_eligible and active.automatic_lead_eligible
    assert review.band == FreshnessBand.REVIEW
    assert not review.notification_eligible
    assert not review.automatic_lead_eligible
    assert stale.band == FreshnessBand.STALE
    assert not stale.notification_eligible
    assert not stale.automatic_lead_eligible


def test_review_window_requires_explicit_future_event_date() -> None:
    now = datetime(2026, 8, 26, 12, tzinfo=UTC)
    settings = Settings()
    message_date = now - timedelta(days=45)

    future = assess_freshness(
        settings,
        message_date,
        {"event_date": "2026-09-10T15:00:00+00:00"},
        now=now,
    )
    past = assess_freshness(
        settings,
        message_date,
        {"event_date": "2026-08-01T15:00:00+00:00"},
        now=now,
    )
    malformed = assess_freshness(
        settings,
        message_date,
        {"event_date": "soon"},
        now=now,
    )

    assert future.band == FreshnessBand.REVIEW
    assert future.notification_eligible
    assert not future.automatic_lead_eligible
    assert not past.notification_eligible
    assert not malformed.notification_eligible


def test_missing_message_date_is_never_notification_eligible() -> None:
    decision = assess_freshness(
        Settings(),
        None,
        {"event_date": "2099-01-01T00:00:00+00:00"},
    )
    assert decision.band == FreshnessBand.UNKNOWN
    assert not decision.notification_eligible


def test_freshness_windows_must_be_ordered() -> None:
    with pytest.raises(ValidationError, match="Freshness windows must satisfy"):
        Settings(
            lead_hot_max_age_days=31,
            lead_active_max_age_days=30,
            lead_review_max_age_days=90,
        )
