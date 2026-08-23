from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utc_now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class SearchProfileRecord(Base):
    __tablename__ = "search_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text, default="")
    services: Mapped[list[str]] = mapped_column(JSON, default=list)
    locations: Mapped[list[str]] = mapped_column(JSON, default=list)
    intents: Mapped[list[str]] = mapped_column(JSON, default=list)
    languages: Mapped[list[str]] = mapped_column(JSON, default=list)
    negative_terms: Mapped[list[str]] = mapped_column(JSON, default=list)
    positive_examples: Mapped[list[str]] = mapped_column(JSON, default=list)
    classifier_prompt: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    queries: Mapped[list[DiscoveryQuery]] = relationship(
        back_populates="profile", cascade="all, delete-orphan"
    )
    subscriptions: Mapped[list[SourceSubscription]] = relationship(
        back_populates="profile", cascade="all, delete-orphan"
    )
    signals: Mapped[list[Signal]] = relationship(back_populates="profile")


class DiscoveryQuery(Base):
    __tablename__ = "discovery_queries"
    __table_args__ = (UniqueConstraint("profile_id", "query", name="uq_profile_query"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(
        ForeignKey("search_profiles.id", ondelete="CASCADE"), index=True
    )
    query: Mapped[str] = mapped_column(String(500))
    language: Mapped[str | None] = mapped_column(String(20), nullable=True)
    query_type: Mapped[str] = mapped_column(String(30), default="generated")
    active: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    profile: Mapped[SearchProfileRecord] = relationship(back_populates="queries")
    hits: Mapped[list[DiscoveryHit]] = relationship(back_populates="query")


class ChatSource(Base):
    __tablename__ = "chat_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_chat_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    platform: Mapped[str] = mapped_column(String(30), default="telegram", index=True)
    external_source_id: Mapped[str | None] = mapped_column(
        String(500), nullable=True, index=True
    )
    source_url: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(500))
    about: Mapped[str | None] = mapped_column(Text, nullable=True)
    kind: Mapped[str] = mapped_column(String(30), default="unknown")
    is_public: Mapped[bool] = mapped_column(Boolean, default=False)
    participant_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    participant_count_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    language: Mapped[str | None] = mapped_column(String(20), nullable=True)
    discovery_method: Mapped[str] = mapped_column(String(50), default="telegram_search")
    status: Mapped[str] = mapped_column(String(30), default="candidate", index=True)
    permission_status: Mapped[str] = mapped_column(String(30), default="unknown")
    relevance_score: Mapped[float] = mapped_column(Float, default=0.0)
    spam_score: Mapped[float] = mapped_column(Float, default=0.0)
    last_scanned_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    hits: Mapped[list[DiscoveryHit]] = relationship(
        back_populates="source", cascade="all, delete-orphan"
    )
    leads: Mapped[list[Lead]] = relationship(back_populates="source")
    subscriptions: Mapped[list[SourceSubscription]] = relationship(
        back_populates="source", cascade="all, delete-orphan"
    )
    signals: Mapped[list[Signal]] = relationship(
        back_populates="source", cascade="all, delete-orphan"
    )


class DiscoveryHit(Base):
    __tablename__ = "discovery_hits"
    __table_args__ = (
        UniqueConstraint(
            "source_id",
            "telegram_message_id",
            "query_id",
            name="uq_source_message_query",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(
        ForeignKey("chat_sources.id", ondelete="CASCADE"), index=True
    )
    query_id: Mapped[int] = mapped_column(
        ForeignKey("discovery_queries.id", ondelete="CASCADE"), index=True
    )
    telegram_message_id: Mapped[int] = mapped_column(BigInteger)
    message_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    text_excerpt: Mapped[str] = mapped_column(Text, default="")
    permalink: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    author_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    keyword_score: Mapped[float] = mapped_column(Float, default=0.0)
    embedding_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    llm_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    final_score: Mapped[float] = mapped_column(Float, default=0.0, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    source: Mapped[ChatSource] = relationship(back_populates="hits")
    query: Mapped[DiscoveryQuery] = relationship(back_populates="hits")


class Lead(Base):
    __tablename__ = "leads"
    __table_args__ = (
        UniqueConstraint("source_id", "telegram_user_id", name="uq_source_telegram_user"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int | None] = mapped_column(
        ForeignKey("chat_sources.id", ondelete="SET NULL"), nullable=True, index=True
    )
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    platform: Mapped[str] = mapped_column(String(30), default="telegram", index=True)
    external_user_id: Mapped[str | None] = mapped_column(String(500), nullable=True, index=True)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    language: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    phone_origin: Mapped[str | None] = mapped_column(String(50), nullable=True)
    intent: Mapped[str | None] = mapped_column(String(100), nullable=True)
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    event_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    party_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(30), default="new", index=True)
    consent_to_call: Mapped[bool] = mapped_column(Boolean, default=False)
    consent_recorded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    source: Mapped[ChatSource | None] = relationship(back_populates="leads")
    signal_links: Mapped[list[LeadSignal]] = relationship(
        back_populates="lead", cascade="all, delete-orphan"
    )
    consent_events: Mapped[list[ConsentEvent]] = relationship(
        back_populates="lead", cascade="all, delete-orphan"
    )
    notification_items: Mapped[list[NotificationOutbox]] = relationship(
        back_populates="lead", passive_deletes=True
    )


class SourceSubscription(Base):
    """A profile-specific, explicitly approved Telegram monitoring source."""

    __tablename__ = "source_subscriptions"
    __table_args__ = (
        UniqueConstraint("profile_id", "source_id", name="uq_profile_source"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(
        ForeignKey("search_profiles.id", ondelete="CASCADE"), index=True
    )
    source_id: Mapped[int] = mapped_column(
        ForeignKey("chat_sources.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(String(30), default="candidate", index=True)
    monitor_enabled: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    relevance_score: Mapped[float] = mapped_column(Float, default=0.0)
    evidence_count: Mapped[int] = mapped_column(Integer, default=0)
    last_scanned_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    last_scanned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    profile: Mapped[SearchProfileRecord] = relationship(back_populates="subscriptions")
    source: Mapped[ChatSource] = relationship(back_populates="subscriptions")


class Signal(Base):
    """A relevant Telegram message kept for review before or alongside a lead."""

    __tablename__ = "signals"
    __table_args__ = (
        UniqueConstraint(
            "profile_id",
            "source_id",
            "telegram_message_id",
            name="uq_signal_profile_source_message",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(
        ForeignKey("search_profiles.id", ondelete="CASCADE"), index=True
    )
    source_id: Mapped[int] = mapped_column(
        ForeignKey("chat_sources.id", ondelete="CASCADE"), index=True
    )
    telegram_message_id: Mapped[int] = mapped_column(BigInteger)
    external_message_id: Mapped[str | None] = mapped_column(
        String(500), nullable=True, index=True
    )
    message_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    permalink: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    text: Mapped[str] = mapped_column(Text)
    author_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    author_external_id: Mapped[str | None] = mapped_column(
        String(500), nullable=True, index=True
    )
    author_username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    author_display_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    author_phone_visible: Mapped[str | None] = mapped_column(String(50), nullable=True)
    author_is_bot: Mapped[bool] = mapped_column(Boolean, default=False)
    keyword_score: Mapped[float] = mapped_column(Float, default=0.0)
    embedding_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    llm_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    final_score: Mapped[float] = mapped_column(Float, default=0.0, index=True)
    classification_reasons: Mapped[list[str]] = mapped_column(JSON, default=list)
    extracted_data: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(30), default="new", index=True)
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    profile: Mapped[SearchProfileRecord] = relationship(back_populates="signals")
    source: Mapped[ChatSource] = relationship(back_populates="signals")
    lead_links: Mapped[list[LeadSignal]] = relationship(
        back_populates="signal", cascade="all, delete-orphan"
    )
    notification_items: Mapped[list[NotificationOutbox]] = relationship(
        back_populates="signal", cascade="all, delete-orphan"
    )


class LeadSignal(Base):
    __tablename__ = "lead_signals"
    __table_args__ = (UniqueConstraint("lead_id", "signal_id", name="uq_lead_signal"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lead_id: Mapped[int] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), index=True
    )
    signal_id: Mapped[int] = mapped_column(
        ForeignKey("signals.id", ondelete="CASCADE"), index=True
    )
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    lead: Mapped[Lead] = relationship(back_populates="signal_links")
    signal: Mapped[Signal] = relationship(back_populates="lead_links")


class ConsentEvent(Base):
    __tablename__ = "consent_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lead_id: Mapped[int] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), index=True
    )
    channel: Mapped[str] = mapped_column(String(30), default="phone")
    granted: Mapped[bool] = mapped_column(Boolean)
    evidence: Mapped[str] = mapped_column(Text, default="")
    recorded_by: Mapped[str] = mapped_column(String(100), default="operator")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    lead: Mapped[Lead] = relationship(back_populates="consent_events")


class NotificationSubscriber(Base):
    __tablename__ = "notification_subscribers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_chat_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    telegram_username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    awaiting_access_key: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    failed_access_attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    paired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    notification_items: Mapped[list[NotificationOutbox]] = relationship(
        back_populates="subscriber", cascade="all, delete-orphan"
    )


class NotificationBotState(Base):
    __tablename__ = "notification_bot_state"

    key: Mapped[str] = mapped_column(String(50), primary_key=True)
    last_update_id: Mapped[int] = mapped_column(BigInteger, default=0)
    bot_username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class NotificationOutbox(Base):
    __tablename__ = "notification_outbox"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_notification_idempotency_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    subscriber_id: Mapped[int] = mapped_column(
        ForeignKey("notification_subscribers.id", ondelete="CASCADE"), index=True
    )
    lead_id: Mapped[int | None] = mapped_column(
        ForeignKey("leads.id", ondelete="SET NULL"), nullable=True, index=True
    )
    signal_id: Mapped[int] = mapped_column(
        ForeignKey("signals.id", ondelete="CASCADE"), index=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True)
    status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, index=True
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    subscriber: Mapped[NotificationSubscriber] = relationship(
        back_populates="notification_items"
    )
    lead: Mapped[Lead | None] = relationship(back_populates="notification_items")
    signal: Mapped[Signal] = relationship(back_populates="notification_items")


class RunLog(Base):
    __tablename__ = "run_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_type: Mapped[str] = mapped_column(String(30), index=True)
    profile_slug: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(30), default="running", index=True)
    counters: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    action: Mapped[str] = mapped_column(String(100), index=True)
    entity_type: Mapped[str] = mapped_column(String(50), index=True)
    entity_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    details: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class JobLease(Base):
    """Cross-process lease that prevents concurrent use of one MTProto session."""

    __tablename__ = "job_leases"

    name: Mapped[str] = mapped_column(String(100), primary_key=True)
    owner: Mapped[str] = mapped_column(String(100), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class WorkflowState(Base):
    """Durable control/heartbeat row for the Vercel passive monitor."""

    __tablename__ = "workflow_states"

    name: Mapped[str] = mapped_column(String(100), primary_key=True)
    run_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    generation: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    profile_slug: Mapped[str] = mapped_column(String(100), default="jetski-miami")
    interval_seconds: Mapped[int] = mapped_column(Integer, default=60)
    desired_running: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(30), default="starting", index=True)
    last_cycle_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_cycle_finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
