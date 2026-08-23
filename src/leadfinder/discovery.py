from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import case, func, select
from telethon import functions, types, utils
from telethon.errors import FloodWaitError

from leadfinder.classification import HybridClassifier, MessageContext
from leadfinder.config import Settings
from leadfinder.db import Database
from leadfinder.models import (
    ChatSource,
    DiscoveryHit,
    DiscoveryQuery,
    SearchProfileRecord,
    utc_now,
)
from leadfinder.repository import spec_from_record
from leadfinder.services import (
    acquire_job_lease,
    finish_run,
    release_job_lease,
    start_run,
    upsert_subscription,
)
from leadfinder.telegram_gateway import create_client


@dataclass(frozen=True, slots=True)
class DiscoverySummary:
    queries_run: int = 0
    sources_created: int = 0
    hits_created: int = 0
    messages_examined: int = 0


def _chat_kind(entity: types.Chat | types.Channel) -> str:
    if isinstance(entity, types.Chat):
        return "group"
    if entity.megagroup:
        return "group"
    return "channel"


def upsert_source(
    session,
    entity: types.Chat | types.Channel,
    discovery_method: str,
) -> tuple[ChatSource, bool]:
    telegram_chat_id = utils.get_peer_id(entity)
    source = session.scalar(
        select(ChatSource).where(ChatSource.telegram_chat_id == telegram_chat_id)
    )
    created = source is None
    if source is None:
        source = ChatSource(
            telegram_chat_id=telegram_chat_id,
            platform="telegram",
            external_source_id=str(telegram_chat_id),
            title=getattr(entity, "title", str(telegram_chat_id)),
        )
        session.add(source)
        session.flush()

    source.title = getattr(entity, "title", source.title)
    source.platform = "telegram"
    source.external_source_id = str(telegram_chat_id)
    source.username = getattr(entity, "username", None)
    source.kind = _chat_kind(entity)
    source.is_public = bool(source.username)
    source.participant_count = getattr(entity, "participants_count", None)
    if source.participant_count is not None:
        source.participant_count_updated_at = utc_now()
    source.discovery_method = discovery_method
    source.last_seen_at = utc_now()
    return source, created


def message_permalink(source: ChatSource, message_id: int) -> str | None:
    if source.username:
        return f"https://t.me/{source.username}/{message_id}"
    return None


async def run_discovery(
    settings: Settings,
    database: Database,
    profile_slug: str,
    max_queries: int,
    per_query: int,
    classifier: HybridClassifier,
) -> DiscoverySummary:
    with database.session() as session:
        profile_record = session.scalar(
            select(SearchProfileRecord).where(SearchProfileRecord.slug == profile_slug)
        )
        if profile_record is None:
            raise RuntimeError(f"Unknown profile: {profile_slug}. Run seed-profile first.")
        profile = spec_from_record(profile_record)
        profile_id = profile_record.id
        query_ids = list(
            session.scalars(
                select(DiscoveryQuery.id)
                .where(
                    DiscoveryQuery.profile_id == profile_record.id,
                    DiscoveryQuery.active.is_(True),
                )
                .order_by(
                    case((DiscoveryQuery.query_type == "source", 0), else_=1),
                    DiscoveryQuery.last_run_at.asc().nulls_first(),
                    DiscoveryQuery.id,
                )
                .limit(max_queries)
            )
        )
        run_id = start_run(session, "discovery", profile_slug).id

    client = create_client(settings)
    lease_owner = uuid4().hex
    with database.session() as session:
        lease_acquired = acquire_job_lease(session, "telegram_session", lease_owner)
    if not lease_acquired:
        with database.session() as session:
            finish_run(session, run_id, "failed", {}, "Another Telegram job is running")
        raise RuntimeError("Another Telegram job is already using this account")
    queries_run = sources_created = hits_created = messages_examined = 0
    cutoff = datetime.now(UTC) - timedelta(days=settings.discovery_message_lookback_days)
    sampled_source_ids: set[int] = set()

    async def process_message(message, entity, query_id: int, query_text: str) -> None:
        nonlocal sources_created, hits_created, messages_examined
        messages_examined += 1
        if message.date and message.date < cutoff:
            return
        text = (message.raw_text or "").strip()
        if not text:
            return
        result = classifier.classify(
            profile,
            MessageContext(text=text, query=query_text),
        )
        if result.final_score < settings.signal_store_threshold:
            return
        with database.session() as session:
            source, created = upsert_source(session, entity, "message_evidence")
            source.permission_status = "readable"
            sources_created += int(created)
            existing = session.scalar(
                select(DiscoveryHit).where(
                    DiscoveryHit.source_id == source.id,
                    DiscoveryHit.telegram_message_id == message.id,
                    DiscoveryHit.query_id == query_id,
                )
            )
            hit_created = existing is None
            if hit_created:
                session.add(
                    DiscoveryHit(
                        source_id=source.id,
                        query_id=query_id,
                        telegram_message_id=message.id,
                        message_date=message.date,
                        text_excerpt=text[:600],
                        permalink=message_permalink(source, message.id),
                        author_user_id=message.sender_id,
                        keyword_score=result.keyword_score,
                        embedding_score=result.embedding_score,
                        llm_score=result.llm_score,
                        final_score=result.final_score,
                    )
                )
                hits_created += 1
            upsert_subscription(
                session,
                profile_id,
                source.id,
                relevance_score=result.final_score,
                evidence_increment=int(hit_created),
            )
            source.relevance_score = max(source.relevance_score, result.final_score)

    try:
        await client.connect()
        if not await client.is_user_authorized():
            raise RuntimeError("Telegram is not authorized. Run: uv run leadfinder auth-qr")
        for query_id in query_ids:
            with database.session() as session:
                query_record = session.get(DiscoveryQuery, query_id)
                if query_record is None:
                    continue
                query_text = query_record.query

            # Peer discovery finds candidate public groups even before message search.
            found = await client(
                functions.contacts.SearchRequest(
                    q=query_text,
                    limit=per_query,
                    broadcasts=False,
                )
            )
            group_entities: list[types.Chat | types.Channel] = []
            with database.session() as session:
                for entity in found.chats:
                    if not isinstance(entity, (types.Chat, types.Channel)):
                        continue
                    if _chat_kind(entity) != "group":
                        continue
                    source, created = upsert_source(session, entity, "contacts.search")
                    upsert_subscription(session, profile_id, source.id)
                    sources_created += int(created)
                    group_entities.append(entity)

            # Public group title search does not prove relevance. Sample recent posts
            # for evidence without joining the group or sending anything.
            for entity in group_entities:
                peer_id = utils.get_peer_id(entity)
                if peer_id in sampled_source_ids:
                    continue
                sampled_source_ids.add(peer_id)
                try:
                    async for message in client.iter_messages(
                        entity,
                        limit=settings.discovery_source_sample_messages,
                    ):
                        if message.date and message.date < cutoff:
                            break
                        await process_message(message, entity, query_id, query_text)
                except FloodWaitError:
                    raise
                except Exception as exc:
                    with database.session() as session:
                        source = session.scalar(
                            select(ChatSource).where(
                                ChatSource.telegram_chat_id == peer_id
                            )
                        )
                        if source is not None:
                            source.permission_status = "inaccessible"
                            subscription = upsert_subscription(
                                session, profile_id, source.id
                            )
                            subscription.last_error = str(exc)[:1000]

            # Global message search supplies evidence and author intent.
            async for message in client.iter_messages(
                entity=None,
                search=query_text,
                limit=per_query,
            ):
                entity = await message.get_chat()
                if not isinstance(entity, (types.Chat, types.Channel)):
                    continue
                if _chat_kind(entity) != "group":
                    continue
                await process_message(message, entity, query_id, query_text)

            with database.session() as session:
                query_record = session.get(DiscoveryQuery, query_id)
                if query_record is not None:
                    query_record.last_run_at = utc_now()
            queries_run += 1

        with database.session() as session:
            source_scores = session.execute(
                select(
                    DiscoveryHit.source_id,
                    func.max(DiscoveryHit.final_score),
                ).group_by(DiscoveryHit.source_id)
            )
            for source_id, max_score in source_scores:
                source = session.get(ChatSource, source_id)
                if source is not None:
                    source.relevance_score = float(max_score or 0.0)
        summary = DiscoverySummary(
            queries_run=queries_run,
            sources_created=sources_created,
            hits_created=hits_created,
            messages_examined=messages_examined,
        )
        with database.session() as session:
            finish_run(
                session,
                run_id,
                "completed",
                {
                    "queries_run": queries_run,
                    "sources_created": sources_created,
                    "hits_created": hits_created,
                    "messages_examined": messages_examined,
                },
            )
        return summary
    except FloodWaitError as exc:
        error = f"Telegram rate limit: retry after {exc.seconds} seconds"
        with database.session() as session:
            finish_run(
                session,
                run_id,
                "rate_limited",
                {
                    "queries_run": queries_run,
                    "sources_created": sources_created,
                    "hits_created": hits_created,
                    "messages_examined": messages_examined,
                    "retry_after_seconds": exc.seconds,
                },
                error,
            )
        raise RuntimeError(error) from exc
    except Exception as exc:
        with database.session() as session:
            finish_run(
                session,
                run_id,
                "failed",
                {
                    "queries_run": queries_run,
                    "sources_created": sources_created,
                    "hits_created": hits_created,
                    "messages_examined": messages_examined,
                },
                str(exc),
            )
        raise
    finally:
        try:
            await client.disconnect()
        finally:
            with database.session() as session:
                release_job_lease(session, "telegram_session", lease_owner)
