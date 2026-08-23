from __future__ import annotations

from dataclasses import asdict, dataclass

from sqlalchemy import select

from leadfinder.classification import HybridClassifier, MessageContext
from leadfinder.config import Settings
from leadfinder.db import Database
from leadfinder.models import (
    ChatSource,
    Lead,
    LeadSignal,
    SearchProfileRecord,
    Signal,
    utc_now,
)
from leadfinder.repository import spec_from_record
from leadfinder.services import (
    add_audit_event,
    create_or_update_lead_from_signal,
    finish_run,
    start_run,
)


@dataclass(frozen=True, slots=True)
class ReclassificationSummary:
    signals_examined: int = 0
    candidates_confirmed: int = 0
    signals_rejected: int = 0
    leads_created_or_updated: int = 0
    invalid_leads_removed: int = 0


def reclassify_pending_signals(
    settings: Settings,
    database: Database,
    profile_slug: str,
    classifier: HybridClassifier,
    limit: int | None = None,
) -> ReclassificationSummary:
    with database.session() as session:
        profile_record = session.scalar(
            select(SearchProfileRecord).where(SearchProfileRecord.slug == profile_slug)
        )
        if profile_record is None:
            raise RuntimeError(f"Unknown profile: {profile_slug}")
        profile = spec_from_record(profile_record)
        query = (
            select(Signal.id)
            .where(
                Signal.profile_id == profile_record.id,
                Signal.status.in_(("new", "possible")),
            )
            .order_by(Signal.id)
        )
        if limit is not None:
            query = query.limit(limit)
        signal_ids = list(session.scalars(query))
        run_id = start_run(session, "reclassify", profile_slug).id

    counters = {
        "signals_examined": 0,
        "candidates_confirmed": 0,
        "signals_rejected": 0,
        "leads_created_or_updated": 0,
        "invalid_leads_removed": 0,
    }
    try:
        for signal_id in signal_ids:
            with database.session() as session:
                signal = session.get(Signal, signal_id)
                if signal is None or signal.status not in {"new", "possible"}:
                    continue
                source = session.get(ChatSource, signal.source_id)
                result = classifier.classify(
                    profile,
                    MessageContext(
                        text=signal.text,
                        query=profile.classifier_prompt,
                        source_language=source.language if source is not None else None,
                    ),
                )
                counters["signals_examined"] += 1
                signal.keyword_score = result.keyword_score
                signal.embedding_score = result.embedding_score
                signal.llm_score = result.llm_score
                signal.final_score = result.final_score
                signal.classification_reasons = list(result.reasons)
                extracted = dict(signal.extracted_data or {})
                extracted.update(result.extracted_data)
                signal.extracted_data = extracted
                if result.is_candidate:
                    signal.status = "new"
                    counters["candidates_confirmed"] += 1
                    if settings.auto_create_leads:
                        lead = create_or_update_lead_from_signal(session, signal)
                        counters["leads_created_or_updated"] += int(lead is not None)
                else:
                    signal.status = "rejected"
                    signal.reviewed_at = utc_now()
                    signal.review_note = "Automatically rejected by required Gemini classifier"
                    counters["signals_rejected"] += 1
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
                        if (
                            other_active_signal is None
                            and lead.status == "new"
                            and not lead.consent_to_call
                        ):
                            add_audit_event(
                                session,
                                "lead.false_positive_removed",
                                "lead",
                                lead.id,
                                {"rejected_signal_id": signal.id},
                            )
                            session.delete(lead)
                            counters["invalid_leads_removed"] += 1

        summary = ReclassificationSummary(**counters)
        with database.session() as session:
            finish_run(session, run_id, "completed", asdict(summary))
        return summary
    except Exception as exc:
        with database.session() as session:
            finish_run(session, run_id, "failed", counters, str(exc))
        raise
