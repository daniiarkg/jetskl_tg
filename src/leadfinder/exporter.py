from __future__ import annotations

import csv
from pathlib import Path

from sqlalchemy import select

from leadfinder.db import Database
from leadfinder.models import (
    ChatSource,
    Lead,
    LeadSignal,
    SearchProfileRecord,
    Signal,
    SourceSubscription,
)


def _prepare_output(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def export_sources(database: Database, path: Path) -> int:
    _prepare_output(path)
    fields = [
        "profile",
        "platform",
        "external_source_id",
        "source_url",
        "telegram_chat_id",
        "username",
        "title",
        "kind",
        "is_public",
        "participant_count",
        "status",
        "monitor_enabled",
        "permission_status",
        "relevance_score",
        "evidence_count",
        "spam_score",
        "discovery_method",
        "discovered_at",
        "last_seen_at",
    ]
    with database.session() as session, path.open("w", encoding="utf-8-sig", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=fields)
        writer.writeheader()
        count = 0
        query = (
            select(SourceSubscription, ChatSource, SearchProfileRecord)
            .join(ChatSource, ChatSource.id == SourceSubscription.source_id)
            .join(SearchProfileRecord, SearchProfileRecord.id == SourceSubscription.profile_id)
            .order_by(SourceSubscription.relevance_score.desc())
        )
        for subscription, source, profile in session.execute(query):
            row = {
                "profile": profile.slug,
                **{
                    field: getattr(source, field)
                    for field in fields
                    if hasattr(source, field)
                },
                "status": subscription.status,
                "monitor_enabled": subscription.monitor_enabled,
                "relevance_score": subscription.relevance_score,
                "evidence_count": subscription.evidence_count,
            }
            writer.writerow(row)
            count += 1
    return count


def export_leads(database: Database, path: Path) -> int:
    _prepare_output(path)
    fields = [
        "id",
        "platform",
        "external_user_id",
        "source_title",
        "source_url",
        "source_telegram_chat_id",
        "telegram_user_id",
        "telegram_profile_url",
        "username",
        "display_name",
        "language",
        "phone",
        "phone_origin",
        "intent",
        "location",
        "event_date",
        "party_size",
        "confidence",
        "status",
        "consent_to_call",
        "message_text",
        "message_permalink",
        "message_date",
        "created_at",
    ]
    with database.session() as session, path.open("w", encoding="utf-8-sig", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=fields)
        writer.writeheader()
        count = 0
        query = (
            select(Lead, ChatSource)
            .outerjoin(ChatSource, ChatSource.id == Lead.source_id)
            .order_by(Lead.created_at.desc())
        )
        for lead, source in session.execute(query):
            signal = session.scalar(
                select(Signal)
                .join(LeadSignal, LeadSignal.signal_id == Signal.id)
                .where(LeadSignal.lead_id == lead.id)
                .order_by(LeadSignal.is_primary.desc(), Signal.message_date.desc())
                .limit(1)
            )
            row = {
                field: getattr(lead, field)
                for field in fields
                if hasattr(lead, field)
            }
            row.update(
                {
                    "source_title": source.title if source else None,
                    "source_url": source.source_url if source else None,
                    "source_telegram_chat_id": source.telegram_chat_id if source else None,
                    "telegram_profile_url": (
                        f"https://t.me/{lead.username}"
                        if lead.platform == "telegram" and lead.username
                        else None
                    ),
                    "message_text": signal.text if signal else None,
                    "message_permalink": signal.permalink if signal else None,
                    "message_date": signal.message_date if signal else None,
                }
            )
            if not lead.consent_to_call:
                row["phone"] = ""
            writer.writerow(row)
            count += 1
    return count


def export_signals(database: Database, path: Path) -> int:
    _prepare_output(path)
    fields = [
        "id",
        "profile",
        "platform",
        "source_title",
        "source_url",
        "source_username",
        "telegram_message_id",
        "external_message_id",
        "message_date",
        "permalink",
        "text",
        "author_user_id",
        "author_external_id",
        "author_username",
        "author_display_name",
        "keyword_score",
        "embedding_score",
        "llm_score",
        "final_score",
        "language",
        "status",
        "classification_reasons",
        "extracted_data",
    ]
    with database.session() as session, path.open("w", encoding="utf-8-sig", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=fields)
        writer.writeheader()
        query = (
            select(Signal, ChatSource, SearchProfileRecord)
            .join(ChatSource, ChatSource.id == Signal.source_id)
            .join(SearchProfileRecord, SearchProfileRecord.id == Signal.profile_id)
            .where(Signal.status != "rejected")
            .order_by(Signal.created_at.desc())
        )
        count = 0
        for signal, source, profile in session.execute(query):
            writer.writerow(
                {
                    "id": signal.id,
                    "profile": profile.slug,
                    "platform": source.platform,
                    "source_title": source.title,
                    "source_url": source.source_url,
                    "source_username": source.username,
                    "telegram_message_id": signal.telegram_message_id,
                    "external_message_id": signal.external_message_id,
                    "message_date": signal.message_date,
                    "permalink": signal.permalink,
                    "text": signal.text,
                    "author_user_id": signal.author_user_id,
                    "author_external_id": signal.author_external_id,
                    "author_username": signal.author_username,
                    "author_display_name": signal.author_display_name,
                    "keyword_score": signal.keyword_score,
                    "embedding_score": signal.embedding_score,
                    "llm_score": signal.llm_score,
                    "final_score": signal.final_score,
                    "language": (signal.extracted_data or {}).get("language"),
                    "status": signal.status,
                    "classification_reasons": " | ".join(signal.classification_reasons or []),
                    "extracted_data": signal.extracted_data,
                }
            )
            count += 1
    return count
