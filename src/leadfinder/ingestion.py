from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select

from leadfinder.classification import HybridClassifier, MessageContext
from leadfinder.config import Settings
from leadfinder.db import Database
from leadfinder.extraction import extract_message_facts
from leadfinder.freshness import FreshnessBand, assess_freshness
from leadfinder.models import ChatSource, SearchProfileRecord, Signal, utc_now
from leadfinder.notifications import enqueue_signal_notifications
from leadfinder.repository import spec_from_record
from leadfinder.services import create_or_update_lead_from_signal, upsert_subscription

SUPPORTED_PLATFORMS = {
    "telegram",
    "instagram",
    "forum",
    "website",
    "whatsapp",
    "facebook",
    "reddit",
    "discord",
    "other",
}


@dataclass(frozen=True, slots=True)
class NormalizedMessage:
    platform: str
    source_external_id: str
    source_title: str
    message_external_id: str
    text: str
    source_url: str | None = None
    message_url: str | None = None
    source_kind: str = "community"
    published_at: datetime | None = None
    author_external_id: str | None = None
    author_username: str | None = None
    author_display_name: str | None = None
    language: str | None = None


@dataclass(frozen=True, slots=True)
class IngestionResult:
    source_id: int
    source_status: str
    signal_id: int | None = None
    lead_id: int | None = None
    final_score: float | None = None
    duplicate: bool = False
    classification_skipped: bool = False


def _stable_numeric_id(namespace: str, value: str, *, negative: bool = False) -> int:
    digest = hashlib.sha256(f"{namespace}\0{value}".encode()).digest()
    numeric = int.from_bytes(digest[:8], "big") & ((1 << 62) - 1)
    numeric = numeric or 1
    return -numeric if negative else numeric


def ingest_message(
    settings: Settings,
    database: Database,
    profile_slug: str,
    classifier: HybridClassifier,
    message: NormalizedMessage,
) -> IngestionResult:
    platform = message.platform.casefold().strip()
    if platform not in SUPPORTED_PLATFORMS:
        raise ValueError(f"Unsupported platform: {message.platform}")
    if not message.text.strip():
        raise ValueError("Message text must not be empty")

    synthetic_source_id = _stable_numeric_id(
        f"source:{platform}", message.source_external_id, negative=True
    )
    synthetic_message_id = _stable_numeric_id(
        f"message:{platform}:{message.source_external_id}", message.message_external_id
    )

    with database.session() as session:
        profile_record = session.scalar(
            select(SearchProfileRecord).where(SearchProfileRecord.slug == profile_slug)
        )
        if profile_record is None:
            raise RuntimeError(f"Unknown profile: {profile_slug}")
        profile = spec_from_record(profile_record)

        source = session.scalar(
            select(ChatSource).where(
                ChatSource.platform == platform,
                ChatSource.external_source_id == message.source_external_id,
            )
        )
        if source is None:
            source = ChatSource(
                telegram_chat_id=synthetic_source_id,
                platform=platform,
                external_source_id=message.source_external_id,
                source_url=message.source_url,
                title=message.source_title,
                kind=message.source_kind,
                is_public=True,
                discovery_method="connector_ingest",
                permission_status="public",
            )
            session.add(source)
            session.flush()
        else:
            source.title = message.source_title or source.title
            source.source_url = message.source_url or source.source_url
            source.kind = message.source_kind or source.kind
            source.last_seen_at = utc_now()

        subscription = upsert_subscription(session, profile_record.id, source.id)
        source_id = source.id
        source_status = subscription.status
        if subscription.status != "approved" or not subscription.monitor_enabled:
            return IngestionResult(
                source_id=source_id,
                source_status=source_status,
                classification_skipped=True,
            )

        existing = session.scalar(
            select(Signal).where(
                Signal.profile_id == profile_record.id,
                Signal.source_id == source.id,
                Signal.telegram_message_id == synthetic_message_id,
            )
        )
        if existing is not None:
            lead = existing.lead_links[0].lead if existing.lead_links else None
            freshness = assess_freshness(
                settings,
                existing.message_date,
                existing.extracted_data,
            )
            if (
                lead is None
                and settings.auto_create_leads
                and existing.status == "new"
                and freshness.automatic_lead_eligible
            ):
                lead = create_or_update_lead_from_signal(session, existing)
            return IngestionResult(
                source_id=source_id,
                source_status=source_status,
                signal_id=existing.id,
                lead_id=lead.id if lead else None,
                final_score=existing.final_score,
                duplicate=True,
            )

        result = classifier.classify(
            profile,
            MessageContext(
                text=message.text.strip(),
                query=f"Source: {message.source_title}. {profile.classifier_prompt}",
                language=message.language,
                source_language=source.language,
            ),
        )
        if result.final_score < settings.signal_store_threshold:
            return IngestionResult(
                source_id=source_id,
                source_status=source_status,
                final_score=result.final_score,
            )

        # An unknown publication date must stay unknown. Treating it as "now" would
        # allow an arbitrarily old connector item to bypass the freshness gate.
        published_at = message.published_at
        facts = extract_message_facts(profile, message.text, published_at)
        facts.update(result.extracted_data)
        facts.update(
            {
                "platform": platform,
                "source_external_id": message.source_external_id,
                "message_external_id": message.message_external_id,
            }
        )
        freshness = assess_freshness(settings, published_at, facts)
        author_numeric_id = (
            _stable_numeric_id(
                f"author:{platform}", message.author_external_id
            )
            if message.author_external_id
            else None
        )
        signal = Signal(
            profile_id=profile_record.id,
            source_id=source.id,
            telegram_message_id=synthetic_message_id,
            external_message_id=message.message_external_id,
            message_date=published_at,
            permalink=message.message_url,
            text=message.text[: settings.max_signal_text_length],
            author_user_id=author_numeric_id,
            author_external_id=message.author_external_id,
            author_username=message.author_username,
            author_display_name=message.author_display_name,
            author_is_bot=False,
            keyword_score=result.keyword_score,
            embedding_score=result.embedding_score,
            llm_score=result.llm_score,
            final_score=result.final_score,
            classification_reasons=list(result.reasons),
            extracted_data=facts,
            status=(
                "new"
                if result.is_candidate
                and freshness.band in {FreshnessBand.HOT, FreshnessBand.ACTIVE}
                else "possible"
            ),
        )
        session.add(signal)
        session.flush()
        lead = (
            create_or_update_lead_from_signal(session, signal)
            if result.is_candidate
            and settings.auto_create_leads
            and freshness.automatic_lead_eligible
            else None
        )
        enqueue_signal_notifications(
            session,
            signal,
            lead,
            settings=settings,
        )
        return IngestionResult(
            source_id=source_id,
            source_status=source_status,
            signal_id=signal.id,
            lead_id=lead.id if lead else None,
            final_score=result.final_score,
        )
