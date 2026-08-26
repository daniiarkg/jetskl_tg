from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import select
from telethon import types
from telethon.errors import FloodWaitError

from leadfinder.classification import HybridClassifier, MessageContext
from leadfinder.config import Settings
from leadfinder.db import Database
from leadfinder.discovery import message_permalink
from leadfinder.extraction import extract_message_facts
from leadfinder.freshness import FreshnessBand, assess_freshness
from leadfinder.models import ChatSource, SearchProfileRecord, Signal, SourceSubscription, utc_now
from leadfinder.monitor import display_name, resolve_source_entity
from leadfinder.notifications import enqueue_signal_notifications
from leadfinder.repository import spec_from_record
from leadfinder.services import (
    acquire_job_lease,
    create_or_update_lead_from_signal,
    finish_run,
    release_job_lease,
    start_run,
)
from leadfinder.telegram_gateway import create_client


@dataclass(frozen=True, slots=True)
class BackfillSummary:
    sources_scanned: int = 0
    searches_run: int = 0
    messages_examined: int = 0
    signals_created: int = 0
    leads_created_or_updated: int = 0
    sources_failed: int = 0


def _search_terms(services: tuple[str, ...], max_terms: int) -> list[str]:
    terms: list[str] = []
    seen: set[str] = set()
    for service in services:
        normalized = " ".join(service.casefold().split())
        if normalized in seen:
            continue
        seen.add(normalized)
        terms.append(service)
        if len(terms) >= max_terms:
            break
    return terms


async def run_backfill(
    settings: Settings,
    database: Database,
    profile_slug: str,
    classifier: HybridClassifier,
    lookback_days: int = 180,
    messages_per_term: int = 50,
    max_service_terms: int = 12,
    source_limit: int | None = None,
) -> BackfillSummary:
    with database.session() as session:
        profile_record = session.scalar(
            select(SearchProfileRecord).where(SearchProfileRecord.slug == profile_slug)
        )
        if profile_record is None:
            raise RuntimeError(f"Unknown profile: {profile_slug}")
        profile = spec_from_record(profile_record)
        profile_id = profile_record.id
        query = (
            select(SourceSubscription.id)
            .join(ChatSource, ChatSource.id == SourceSubscription.source_id)
            .where(
                SourceSubscription.profile_id == profile_id,
                SourceSubscription.status == "approved",
                SourceSubscription.monitor_enabled.is_(True),
                ChatSource.kind == "group",
            )
            .order_by(SourceSubscription.relevance_score.desc(), SourceSubscription.id)
        )
        if source_limit is not None:
            query = query.limit(source_limit)
        subscription_ids = list(session.scalars(query))
        run_id = start_run(session, "backfill", profile_slug).id

    client = create_client(settings)
    lease_owner = uuid4().hex
    with database.session() as session:
        lease_acquired = acquire_job_lease(session, "telegram_session", lease_owner)
    if not lease_acquired:
        with database.session() as session:
            finish_run(session, run_id, "failed", {}, "Another Telegram job is running")
        raise RuntimeError("Another Telegram job is already using this account")

    counters = {
        "sources_scanned": 0,
        "searches_run": 0,
        "messages_examined": 0,
        "signals_created": 0,
        "leads_created_or_updated": 0,
        "sources_failed": 0,
    }
    cutoff = datetime.now(UTC) - timedelta(days=lookback_days)
    terms = _search_terms(profile.services, max_service_terms)

    try:
        await client.connect()
        if not await client.is_user_authorized():
            raise RuntimeError("Telegram is not authorized. Run: uv run leadfinder auth-qr")
        for subscription_id in subscription_ids:
            with database.session() as session:
                subscription = session.get(SourceSubscription, subscription_id)
                source = (
                    session.get(ChatSource, subscription.source_id)
                    if subscription is not None
                    else None
                )
                if source is None:
                    continue
                source_id = source.id
                source_snapshot = source
            seen_messages: set[int] = set()
            try:
                entity = await resolve_source_entity(client, source_snapshot)
                for term in terms:
                    counters["searches_run"] += 1
                    async for message in client.iter_messages(
                        entity,
                        search=term,
                        limit=messages_per_term,
                    ):
                        if message.id in seen_messages:
                            continue
                        seen_messages.add(message.id)
                        if message.date and message.date < cutoff:
                            continue
                        text = (message.raw_text or "").strip()
                        if not text or message.out:
                            continue
                        sender = await message.get_sender()
                        if not isinstance(sender, types.User) or sender.bot:
                            continue
                        counters["messages_examined"] += 1
                        result = classifier.classify(
                            profile,
                            MessageContext(
                                text=text,
                                source_language=source_snapshot.language,
                                query=f"Telegram source: {source_snapshot.title}. Search: {term}",
                            ),
                        )
                        if result.final_score < settings.signal_store_threshold:
                            continue
                        facts = extract_message_facts(profile, text, message.date)
                        facts.update(result.extracted_data)
                        freshness = assess_freshness(
                            settings,
                            message.date,
                            facts,
                        )
                        with database.session() as session:
                            existing = session.scalar(
                                select(Signal).where(
                                    Signal.profile_id == profile_id,
                                    Signal.source_id == source_id,
                                    Signal.telegram_message_id == message.id,
                                )
                            )
                            if existing is not None:
                                continue
                            db_source = session.get(ChatSource, source_id)
                            if db_source is None:
                                continue
                            signal = Signal(
                                profile_id=profile_id,
                                source_id=source_id,
                                telegram_message_id=message.id,
                                message_date=message.date,
                                permalink=message_permalink(db_source, message.id),
                                text=text[: settings.max_signal_text_length],
                                author_user_id=sender.id,
                                author_username=sender.username,
                                author_display_name=display_name(sender),
                                author_is_bot=bool(sender.bot),
                                keyword_score=result.keyword_score,
                                embedding_score=result.embedding_score,
                                llm_score=result.llm_score,
                                final_score=result.final_score,
                                classification_reasons=list(result.reasons),
                                extracted_data=facts,
                                status=(
                                    "new"
                                    if result.is_candidate
                                    and freshness.band
                                    in {FreshnessBand.HOT, FreshnessBand.ACTIVE}
                                    else "possible"
                                ),
                            )
                            session.add(signal)
                            session.flush()
                            counters["signals_created"] += 1
                            lead = None
                            if (
                                result.is_candidate
                                and settings.auto_create_leads
                                and freshness.automatic_lead_eligible
                            ):
                                lead = create_or_update_lead_from_signal(session, signal)
                                if lead is not None:
                                    counters["leads_created_or_updated"] += 1
                            if settings.backfill_notifications_enabled:
                                enqueue_signal_notifications(
                                    session,
                                    signal,
                                    lead,
                                    settings=settings,
                                )
                with database.session() as session:
                    subscription = session.get(SourceSubscription, subscription_id)
                    if subscription is not None:
                        subscription.last_error = None
                        subscription.last_scanned_at = utc_now()
                counters["sources_scanned"] += 1
            except FloodWaitError:
                raise
            except Exception as exc:
                counters["sources_failed"] += 1
                with database.session() as session:
                    subscription = session.get(SourceSubscription, subscription_id)
                    if subscription is not None:
                        subscription.last_error = f"backfill: {str(exc)[:980]}"

        summary = BackfillSummary(**counters)
        with database.session() as session:
            finish_run(session, run_id, "completed", asdict(summary))
        return summary
    except FloodWaitError as exc:
        error = f"Telegram rate limit: retry after {exc.seconds} seconds"
        with database.session() as session:
            finish_run(
                session,
                run_id,
                "rate_limited",
                {**counters, "retry_after_seconds": exc.seconds},
                error,
            )
        raise RuntimeError(error) from exc
    except Exception as exc:
        with database.session() as session:
            finish_run(session, run_id, "failed", counters, str(exc))
        raise
    finally:
        try:
            await client.disconnect()
        finally:
            with database.session() as session:
                release_job_lease(session, "telegram_session", lease_owner)
