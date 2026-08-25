from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from leadfinder.models import (
    AuditEvent,
    JobLease,
    Lead,
    LeadSignal,
    RunLog,
    Signal,
    SourceSubscription,
    utc_now,
)

SOURCE_STATUSES = {"candidate", "approved", "paused", "rejected"}
SIGNAL_STATUSES = {"new", "possible", "qualified", "rejected"}
LEAD_STATUSES = {"new", "reviewed", "contactable", "contacted", "won", "lost"}


def upsert_subscription(
    session: Session,
    profile_id: int,
    source_id: int,
    relevance_score: float = 0.0,
    evidence_increment: int = 0,
) -> SourceSubscription:
    subscription = session.scalar(
        select(SourceSubscription).where(
            SourceSubscription.profile_id == profile_id,
            SourceSubscription.source_id == source_id,
        )
    )
    if subscription is None:
        subscription = SourceSubscription(profile_id=profile_id, source_id=source_id)
        session.add(subscription)
        session.flush()
    subscription.relevance_score = max(subscription.relevance_score, relevance_score)
    subscription.evidence_count += evidence_increment
    return subscription


def add_audit_event(
    session: Session,
    action: str,
    entity_type: str,
    entity_id: int | None,
    details: dict[str, object] | None = None,
) -> AuditEvent:
    event = AuditEvent(
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        details=details or {},
    )
    session.add(event)
    return event


def set_subscription_status(
    session: Session,
    subscription: SourceSubscription,
    status: str,
) -> SourceSubscription:
    if status not in SOURCE_STATUSES:
        raise ValueError(f"Unsupported source status: {status}")
    previous = subscription.status
    subscription.status = status
    subscription.monitor_enabled = status == "approved"
    subscription.last_error = None
    add_audit_event(
        session,
        "source.status_changed",
        "source_subscription",
        subscription.id,
        {"from": previous, "to": status},
    )
    return subscription


def _parse_event_date(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def create_or_update_lead_from_signal(session: Session, signal: Signal) -> Lead | None:
    if signal.author_user_id is None or signal.author_is_bot:
        return None
    lead = session.scalar(
        select(Lead).where(
            Lead.source_id == signal.source_id,
            Lead.telegram_user_id == signal.author_user_id,
        )
    )
    if lead is None:
        lead = Lead(
            source_id=signal.source_id,
            telegram_user_id=signal.author_user_id,
            platform=signal.source.platform,
            external_user_id=signal.author_external_id,
            status="new",
        )
        session.add(lead)
        session.flush()

    lead.username = signal.author_username or lead.username
    lead.platform = signal.source.platform
    lead.external_user_id = signal.author_external_id or lead.external_user_id
    lead.display_name = signal.author_display_name or lead.display_name
    lead.confidence = max(lead.confidence, signal.final_score)
    extracted = signal.extracted_data or {}
    if extracted.get("intent"):
        lead.intent = str(extracted["intent"])
    if extracted.get("location"):
        lead.location = str(extracted["location"])
    if extracted.get("language"):
        lead.language = str(extracted["language"])
    if isinstance(extracted.get("party_size"), int):
        lead.party_size = int(extracted["party_size"])
    event_date = _parse_event_date(extracted.get("event_date"))
    if event_date is not None:
        lead.event_date = event_date

    link = session.scalar(
        select(LeadSignal).where(
            LeadSignal.lead_id == lead.id,
            LeadSignal.signal_id == signal.id,
        )
    )
    if link is None:
        session.add(LeadSignal(lead_id=lead.id, signal_id=signal.id, is_primary=True))
    return lead


def review_signal(
    session: Session,
    signal: Signal,
    status: str,
    note: str = "",
) -> Lead | None:
    if status not in {"qualified", "rejected"}:
        raise ValueError("Signal review status must be qualified or rejected")
    previous = signal.status
    signal.status = status
    signal.review_note = note or None
    signal.reviewed_at = utc_now()
    lead = create_or_update_lead_from_signal(session, signal) if status == "qualified" else None
    if status == "rejected":
        _remove_unconfirmed_leads_for_signal(session, signal)
    add_audit_event(
        session,
        "signal.reviewed",
        "signal",
        signal.id,
        {"from": previous, "to": status, "lead_id": lead.id if lead else None},
    )
    return lead


def _remove_unconfirmed_leads_for_signal(session: Session, signal: Signal) -> int:
    removed = 0
    linked_leads = list(
        session.scalars(
            select(Lead)
            .join(LeadSignal, LeadSignal.lead_id == Lead.id)
            .where(LeadSignal.signal_id == signal.id)
        )
    )
    for lead in linked_leads:
        other_active_signal = session.scalar(
            select(Signal.id)
            .join(LeadSignal, LeadSignal.signal_id == Signal.id)
            .where(
                LeadSignal.lead_id == lead.id,
                Signal.id != signal.id,
                Signal.status != "rejected",
            )
            .limit(1)
        )
        if other_active_signal is None and lead.status == "new":
            add_audit_event(
                session,
                "lead.false_positive_removed",
                "lead",
                lead.id,
                {"rejected_signal_id": signal.id},
            )
            session.delete(lead)
            removed += 1
    return removed


def update_lead_status(session: Session, lead: Lead, status: str) -> Lead:
    if status not in LEAD_STATUSES:
        raise ValueError(f"Unsupported lead status: {status}")
    previous = lead.status
    lead.status = status
    add_audit_event(
        session,
        "lead.status_changed",
        "lead",
        lead.id,
        {"from": previous, "to": status},
    )
    return lead


def start_run(session: Session, run_type: str, profile_slug: str | None) -> RunLog:
    run = RunLog(run_type=run_type, profile_slug=profile_slug, status="running")
    session.add(run)
    session.flush()
    return run


def finish_run(
    session: Session,
    run_id: int,
    status: str,
    counters: dict[str, object],
    error: str | None = None,
) -> None:
    run = session.get(RunLog, run_id)
    if run is None:
        return
    run.status = status
    run.counters = counters
    run.error = error
    run.finished_at = utc_now()


def acquire_job_lease(
    session: Session,
    name: str,
    owner: str,
    ttl_seconds: int = 21600,
) -> bool:
    now = datetime.now(UTC)
    expires_at = now + timedelta(seconds=ttl_seconds)
    result = session.execute(
        update(JobLease)
        .where(JobLease.name == name, JobLease.expires_at < now)
        .values(owner=owner, expires_at=expires_at)
    )
    if result.rowcount:
        return True
    try:
        with session.begin_nested():
            session.add(JobLease(name=name, owner=owner, expires_at=expires_at))
            session.flush()
        return True
    except IntegrityError:
        return False


def release_job_lease(session: Session, name: str, owner: str) -> None:
    session.execute(delete(JobLease).where(JobLease.name == name, JobLease.owner == owner))
