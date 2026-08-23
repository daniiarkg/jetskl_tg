from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import select
from telethon import functions, types
from telethon.errors import FloodWaitError

from leadfinder.classification import HybridClassifier, MessageContext
from leadfinder.config import Settings
from leadfinder.db import Database
from leadfinder.discovery import message_permalink
from leadfinder.extraction import extract_message_facts
from leadfinder.models import (
    ChatSource,
    SearchProfileRecord,
    Signal,
    SourceSubscription,
    utc_now,
)
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
class MonitorSummary:
    sources_scanned: int = 0
    messages_examined: int = 0
    signals_created: int = 0
    leads_created_or_updated: int = 0
    sources_failed: int = 0


def display_name(sender: types.User) -> str | None:
    names = (
        getattr(sender, "first_name", None),
        getattr(sender, "last_name", None),
    )
    value = " ".join(
        part for part in names if part
    ).strip()
    return value or None


async def resolve_source_entity(client, source: ChatSource):
    if source.username:
        return await client.get_entity(source.username)
    return await client.get_entity(source.telegram_chat_id)


async def participant_count_snapshot(
    client,
    entity,
    source: ChatSource,
) -> tuple[int | None, bool]:
    """Refresh the expensive full-chat count at most once per day."""
    now = datetime.now(UTC)
    updated_at = source.participant_count_updated_at
    if updated_at is not None and updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=UTC)
    direct_count = getattr(entity, "participants_count", None)
    if updated_at is not None and updated_at >= now - timedelta(days=1):
        return direct_count or source.participant_count, False
    try:
        if isinstance(entity, types.Channel):
            full = await client(functions.channels.GetFullChannelRequest(entity))
        elif isinstance(entity, types.Chat):
            full = await client(functions.messages.GetFullChatRequest(entity.id))
        else:
            return direct_count or source.participant_count, False
        full_count = getattr(full.full_chat, "participants_count", None)
        return full_count or direct_count or source.participant_count, True
    except FloodWaitError:
        raise
    except Exception:
        # Count visibility is optional and must never block lead monitoring.
        return direct_count or source.participant_count, True


async def run_monitor(
    settings: Settings,
    database: Database,
    profile_slug: str,
    classifier: HybridClassifier,
    source_limit: int | None = None,
    messages_per_source: int | None = None,
) -> MonitorSummary:
    with database.session() as session:
        profile_record = session.scalar(
            select(SearchProfileRecord).where(SearchProfileRecord.slug == profile_slug)
        )
        if profile_record is None:
            raise RuntimeError(f"Unknown profile: {profile_slug}. Run seed-profile first.")
        profile = spec_from_record(profile_record)
        profile_id = profile_record.id
        query = (
            select(SourceSubscription.id)
            .where(
                SourceSubscription.profile_id == profile_id,
                SourceSubscription.status == "approved",
                SourceSubscription.monitor_enabled.is_(True),
            )
            .order_by(SourceSubscription.relevance_score.desc(), SourceSubscription.id)
        )
        if source_limit is not None:
            query = query.limit(source_limit)
        subscription_ids = list(session.scalars(query))
        run_id = start_run(session, "monitor", profile_slug).id

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
        "messages_examined": 0,
        "signals_created": 0,
        "leads_created_or_updated": 0,
        "sources_failed": 0,
    }
    per_source = messages_per_source or settings.monitor_messages_per_source
    cutoff = datetime.now(UTC) - timedelta(days=settings.discovery_message_lookback_days)

    try:
        await client.connect()
        if not await client.is_user_authorized():
            raise RuntimeError("Telegram is not authorized. Run: uv run leadfinder auth-qr")

        for subscription_id in subscription_ids:
            with database.session() as session:
                subscription = session.get(SourceSubscription, subscription_id)
                if subscription is None:
                    continue
                source = session.get(ChatSource, subscription.source_id)
                if source is None:
                    continue
                source_id = source.id
                cursor = subscription.last_scanned_message_id
                source_snapshot = source

            try:
                entity = await resolve_source_entity(client, source_snapshot)
                participant_count, participant_count_refreshed = (
                    await participant_count_snapshot(client, entity, source_snapshot)
                )
                iterator = (
                    client.iter_messages(entity, min_id=cursor, reverse=True, limit=per_source)
                    if cursor
                    else client.iter_messages(entity, limit=per_source)
                )
                highest_message_id = cursor or 0
                async for message in iterator:
                    if message.date and message.date < cutoff and not cursor:
                        break
                    highest_message_id = max(highest_message_id, message.id)
                    counters["messages_examined"] += 1
                    text = (message.raw_text or "").strip()
                    if not text or message.out:
                        continue
                    sender = await message.get_sender()
                    if not isinstance(sender, types.User) or sender.bot:
                        continue

                    result = classifier.classify(
                        profile,
                        MessageContext(
                            text=text,
                            source_language=source_snapshot.language,
                            query=(
                                f"Telegram source: {source_snapshot.title}. "
                                f"{profile.classifier_prompt}"
                            ),
                        ),
                    )
                    if result.final_score < settings.signal_store_threshold:
                        continue
                    facts = extract_message_facts(profile, text, message.date)
                    facts.update(result.extracted_data)

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
                            author_phone_visible=sender.phone,
                            author_is_bot=bool(sender.bot),
                            keyword_score=result.keyword_score,
                            embedding_score=result.embedding_score,
                            llm_score=result.llm_score,
                            final_score=result.final_score,
                            classification_reasons=list(result.reasons),
                            extracted_data=facts,
                            status="new" if result.is_candidate else "possible",
                        )
                        session.add(signal)
                        session.flush()
                        counters["signals_created"] += 1
                        lead = None
                        if result.is_candidate and settings.auto_create_leads:
                            lead = create_or_update_lead_from_signal(session, signal)
                            if lead is not None:
                                counters["leads_created_or_updated"] += 1
                        enqueue_signal_notifications(session, signal, lead)

                with database.session() as session:
                    subscription = session.get(SourceSubscription, subscription_id)
                    source = session.get(ChatSource, source_id)
                    if subscription is not None:
                        if highest_message_id:
                            subscription.last_scanned_message_id = highest_message_id
                        subscription.last_scanned_at = utc_now()
                        subscription.last_error = None
                    if source is not None:
                        if source.permission_status != "readable_join_request":
                            source.permission_status = "accessible"
                        source.last_scanned_message_id = highest_message_id or cursor
                        source.last_seen_at = utc_now()
                        if participant_count is not None:
                            source.participant_count = participant_count
                        if participant_count_refreshed:
                            source.participant_count_updated_at = utc_now()
                counters["sources_scanned"] += 1
            except FloodWaitError:
                raise
            except Exception as exc:
                counters["sources_failed"] += 1
                with database.session() as session:
                    subscription = session.get(SourceSubscription, subscription_id)
                    if subscription is not None:
                        subscription.last_error = str(exc)[:1000]
                        subscription.last_scanned_at = utc_now()

        summary = MonitorSummary(**counters)
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
