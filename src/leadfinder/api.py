from __future__ import annotations

import asyncio
import os
import secrets
from contextlib import asynccontextmanager
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Query, status
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import aliased

from leadfinder.backfill import run_backfill
from leadfinder.classification import build_classifier
from leadfinder.config import Settings, get_settings
from leadfinder.db import Database
from leadfinder.discovery import run_discovery
from leadfinder.exporter import export_leads, export_signals, export_sources
from leadfinder.ingestion import SUPPORTED_PLATFORMS, NormalizedMessage, ingest_message
from leadfinder.languages import (
    language_filter_description,
    normalize_language_code,
    normalize_language_filters,
)
from leadfinder.models import (
    ChatSource,
    Lead,
    LeadSignal,
    RunLog,
    SearchProfileRecord,
    Signal,
    SourceSubscription,
    WorkflowState,
)
from leadfinder.monitor import run_monitor
from leadfinder.notifications import (
    deliver_pending_notifications,
    notification_status,
    send_test_notification,
)
from leadfinder.profiles import JETSKI_MIAMI, SearchProfileSpec
from leadfinder.reclassification import reclassify_pending_signals
from leadfinder.repository import upsert_profile
from leadfinder.services import review_signal, set_subscription_status, update_lead_status
from leadfinder.sources import add_public_source, sync_account_dialogs
from leadfinder.workflows import start_passive_monitor, stop_passive_monitor

STATIC_DIR = Path(__file__).parent / "static"
EXPORT_DIR = Path("/tmp/leadfinder-exports") if os.getenv("VERCEL") else Path("exports")
_job_lock = asyncio.Lock()
_job_state: dict[str, object] = {"status": "idle"}


class SourceStatusUpdate(BaseModel):
    status: Literal["candidate", "approved", "paused", "rejected"]


class AdminLoginInput(BaseModel):
    access_key: SecretStr = Field(min_length=1, max_length=512)


class ManualSourceInput(BaseModel):
    profile: str = "jetski-miami"
    username: str = Field(min_length=2, max_length=500)


class SignalReview(BaseModel):
    status: Literal["qualified", "rejected"]
    note: str = Field(default="", max_length=2000)


class LeadUpdate(BaseModel):
    status: Literal["new", "reviewed", "contactable", "contacted", "won", "lost"] | None = None


class ProfileInput(BaseModel):
    slug: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=100)
    name: str = Field(min_length=1, max_length=255)
    description: str = ""
    services: list[str] = Field(min_length=1)
    locations: list[str] = Field(min_length=1)
    intents: list[str] = Field(min_length=1)
    languages: list[str] = Field(default_factory=list)
    negative_terms: list[str] = Field(default_factory=list)
    positive_examples: list[str] = Field(default_factory=list)
    classifier_prompt: str = Field(min_length=1)
    query_limit: int = Field(default=120, ge=1, le=500)

    @field_validator("languages")
    @classmethod
    def validate_languages(cls, value: list[str]) -> list[str]:
        return list(normalize_language_filters(value))


class JobRequest(BaseModel):
    profile: str = "jetski-miami"
    max_queries: int = Field(default=10, ge=1, le=100)
    per_query: int = Field(default=20, ge=1, le=100)
    source_limit: int | None = Field(default=None, ge=1, le=1000)
    messages_per_source: int | None = Field(default=None, ge=1, le=5000)
    lookback_days: int = Field(default=180, ge=1, le=3650)
    messages_per_term: int = Field(default=50, ge=1, le=500)
    max_service_terms: int = Field(default=12, ge=1, le=100)
    signal_limit: int | None = Field(default=None, ge=1, le=10000)


class WorkflowStartInput(BaseModel):
    profile: str = "jetski-miami"
    interval_seconds: Literal[60] = 60


class IngestMessageInput(BaseModel):
    profile: str = "jetski-miami"
    platform: str = Field(min_length=2, max_length=30)
    source_external_id: str = Field(min_length=1, max_length=500)
    source_title: str = Field(min_length=1, max_length=500)
    source_url: str | None = Field(default=None, max_length=2000)
    source_kind: str = Field(default="community", min_length=1, max_length=30)
    message_external_id: str = Field(min_length=1, max_length=500)
    message_url: str | None = Field(default=None, max_length=2000)
    text: str = Field(min_length=1, max_length=50000)
    published_at: datetime | None = None
    author_external_id: str | None = Field(default=None, max_length=500)
    author_username: str | None = Field(default=None, max_length=255)
    author_display_name: str | None = Field(default=None, max_length=500)
    language: str | None = Field(default=None, min_length=2, max_length=20)

    @model_validator(mode="after")
    def validate_platform(self) -> IngestMessageInput:
        if self.platform.casefold().strip() not in SUPPORTED_PLATFORMS:
            raise ValueError(f"Unsupported platform: {self.platform}")
        if self.language is not None:
            language = normalize_language_code(self.language)
            if language is None:
                raise ValueError("Language hint must be an ISO language code, for example en or ru")
            self.language = language
        return self


def _database() -> Database:
    return Database(get_settings())


def _iso(value) -> str | None:
    return value.isoformat() if value is not None else None


def _require_admin_key(
    settings: Annotated[Settings, Depends(get_settings)],
    x_admin_key: Annotated[str | None, Header()] = None,
) -> None:
    configured = settings.effective_admin_api_key
    if configured is None:
        return
    if x_admin_key is None or not secrets.compare_digest(
        x_admin_key,
        configured.get_secret_value(),
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid admin key")


async def _execute_job(kind: str, payload: JobRequest) -> None:
    global _job_state
    async with _job_lock:
        _job_state = {"status": "running", "kind": kind, "profile": payload.profile}
        settings = get_settings()
        database = Database(settings)
        try:
            if kind == "discover":
                summary = await run_discovery(
                    settings,
                    database,
                    payload.profile,
                    payload.max_queries,
                    payload.per_query,
                    build_classifier(settings),
                )
            elif kind == "monitor":
                summary = await run_monitor(
                    settings,
                    database,
                    payload.profile,
                    build_classifier(settings),
                    payload.source_limit,
                    payload.messages_per_source,
                )
            elif kind == "backfill":
                summary = await run_backfill(
                    settings,
                    database,
                    payload.profile,
                    build_classifier(settings),
                    payload.lookback_days,
                    payload.messages_per_term,
                    payload.max_service_terms,
                    payload.source_limit,
                )
            elif kind == "reclassify":
                summary = reclassify_pending_signals(
                    settings,
                    database,
                    payload.profile,
                    build_classifier(settings),
                    payload.signal_limit,
                )
            elif kind == "sync-dialogs":
                summary = await sync_account_dialogs(settings, database, payload.profile)
            else:
                raise RuntimeError(f"Unknown job: {kind}")
            try:
                notification_result: dict[str, object] = asdict(
                    deliver_pending_notifications(settings, database)
                )
            except RuntimeError as exc:
                notification_result = {"error": str(exc)}
            _job_state = {
                "status": "completed",
                "kind": kind,
                "result": asdict(summary) if is_dataclass(summary) else str(summary),
                "notifications": notification_result,
            }
        except Exception as exc:
            _job_state = {"status": "failed", "kind": kind, "error": str(exc)}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    database = _database()
    database.create_all()
    with database.session() as session:
        upsert_profile(session, JETSKI_MIAMI, get_settings().discovery_max_queries)
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="Telegram Leadfinder",
        version="1.3.0",
        description="Multi-source demand discovery with source-linked lead review",
        lifespan=lifespan,
    )

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def dashboard() -> HTMLResponse:
        return HTMLResponse((STATIC_DIR / "index.html").read_text(encoding="utf-8"))

    @app.get("/health")
    def health() -> dict[str, str]:
        with _database().session() as session:
            session.scalar(select(func.count()).select_from(SearchProfileRecord))
        return {"status": "ok"}

    @app.get("/api/auth/status")
    def auth_status() -> dict[str, bool]:
        return {"required": get_settings().effective_admin_api_key is not None}

    @app.post("/api/auth/login")
    def auth_login(payload: AdminLoginInput) -> dict[str, bool]:
        configured = get_settings().effective_admin_api_key
        if configured is None:
            return {"ok": True}
        if not secrets.compare_digest(
            payload.access_key.get_secret_value(),
            configured.get_secret_value(),
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid access key",
            )
        return {"ok": True}

    @app.get("/api/stats", dependencies=[Depends(_require_admin_key)])
    def stats() -> dict[str, int]:
        with _database().session() as session:
            return {
                "profiles": session.scalar(
                    select(func.count()).select_from(SearchProfileRecord)
                )
                or 0,
                "sources": session.scalar(select(func.count()).select_from(ChatSource)) or 0,
                "approved_sources": session.scalar(
                    select(func.count())
                    .select_from(SourceSubscription)
                    .where(SourceSubscription.status == "approved")
                )
                or 0,
                "signals": session.scalar(
                    select(func.count())
                    .select_from(Signal)
                    .where(Signal.status != "rejected")
                )
                or 0,
                "new_signals": session.scalar(
                    select(func.count())
                    .select_from(Signal)
                    .where(Signal.status.in_(("new", "possible")))
                )
                or 0,
                "leads": session.scalar(select(func.count()).select_from(Lead)) or 0,
            }

    @app.get("/api/profiles", dependencies=[Depends(_require_admin_key)])
    def profiles() -> list[dict[str, object]]:
        with _database().session() as session:
            records = list(
                session.scalars(
                    select(SearchProfileRecord).order_by(SearchProfileRecord.id)
                )
            )
            return [
                {
                    "id": item.id,
                    "slug": item.slug,
                    "name": item.name,
                    "description": item.description,
                    "services": item.services,
                    "locations": item.locations,
                    "intents": item.intents,
                    "languages": item.languages,
                    "language_filter": language_filter_description(item.languages),
                    "negative_terms": item.negative_terms,
                    "positive_examples": item.positive_examples,
                    "classifier_prompt": item.classifier_prompt,
                    "enabled": item.enabled,
                }
                for item in records
            ]

    @app.get("/api/notifications/status", dependencies=[Depends(_require_admin_key)])
    def get_notification_status() -> dict[str, object]:
        return notification_status(get_settings(), _database())

    @app.post("/api/notifications/test", dependencies=[Depends(_require_admin_key)])
    def test_notifications() -> dict[str, object]:
        try:
            return asdict(send_test_notification(get_settings(), _database()))
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/workflow/status", dependencies=[Depends(_require_admin_key)])
    def workflow_status() -> dict[str, object]:
        with _database().session() as session:
            state = session.get(WorkflowState, "passive-monitor")
            if state is None:
                return {
                    "desired_running": False,
                    "status": "stopped",
                    "profile": "jetski-miami",
                    "interval_seconds": 60,
                    "run_id": None,
                    "last_cycle_started_at": None,
                    "last_cycle_finished_at": None,
                    "last_error": None,
                }
            return {
                "desired_running": state.desired_running,
                "status": state.status,
                "profile": state.profile_slug,
                "interval_seconds": state.interval_seconds,
                "run_id": state.run_id,
                "last_cycle_started_at": _iso(state.last_cycle_started_at),
                "last_cycle_finished_at": _iso(state.last_cycle_finished_at),
                "last_error": state.last_error,
            }

    @app.post("/api/workflow/start", dependencies=[Depends(_require_admin_key)])
    async def start_workflow(payload: WorkflowStartInput) -> dict[str, object]:
        try:
            return await start_passive_monitor(
                get_settings(),
                _database(),
                payload.profile,
                payload.interval_seconds,
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/workflow/stop", dependencies=[Depends(_require_admin_key)])
    def stop_workflow() -> dict[str, object]:
        return stop_passive_monitor(_database())

    @app.post("/api/profiles", dependencies=[Depends(_require_admin_key)])
    def save_profile(payload: ProfileInput) -> dict[str, object]:
        spec = SearchProfileSpec(
            slug=payload.slug,
            name=payload.name,
            description=payload.description,
            services=tuple(payload.services),
            locations=tuple(payload.locations),
            intents=tuple(payload.intents),
            languages=tuple(payload.languages),
            negative_terms=tuple(payload.negative_terms),
            positive_examples=tuple(payload.positive_examples),
            classifier_prompt=payload.classifier_prompt,
        )
        with _database().session() as session:
            record = upsert_profile(session, spec, payload.query_limit)
            return {"id": record.id, "slug": record.slug, "name": record.name}

    @app.get("/api/sources", dependencies=[Depends(_require_admin_key)])
    def sources(
        profile: str = "jetski-miami",
        source_status: str | None = Query(default=None, alias="status"),
        platform: str | None = Query(default=None),
        limit: int = Query(default=200, ge=1, le=1000),
    ) -> list[dict[str, object]]:
        with _database().session() as session:
            query = (
                select(SourceSubscription, ChatSource)
                .join(ChatSource, ChatSource.id == SourceSubscription.source_id)
                .join(SearchProfileRecord, SearchProfileRecord.id == SourceSubscription.profile_id)
                .where(SearchProfileRecord.slug == profile)
                .order_by(SourceSubscription.relevance_score.desc(), SourceSubscription.id.desc())
                .limit(limit)
            )
            if source_status:
                query = query.where(SourceSubscription.status == source_status)
            if platform:
                query = query.where(ChatSource.platform == platform.casefold().strip())
            return [
                {
                    "subscription_id": subscription.id,
                    "source_id": source.id,
                    "telegram_chat_id": source.telegram_chat_id,
                    "platform": source.platform,
                    "external_source_id": source.external_source_id,
                    "source_url": source.source_url,
                    "username": source.username,
                    "title": source.title,
                    "kind": source.kind,
                    "is_public": source.is_public,
                    "participant_count": source.participant_count,
                    "participant_count_updated_at": _iso(
                        source.participant_count_updated_at
                    ),
                    "permission_status": source.permission_status,
                    "status": subscription.status,
                    "monitor_enabled": subscription.monitor_enabled,
                    "relevance_score": subscription.relevance_score,
                    "evidence_count": subscription.evidence_count,
                    "last_scanned_message_id": subscription.last_scanned_message_id,
                    "last_scanned_at": _iso(subscription.last_scanned_at),
                    "last_error": subscription.last_error,
                }
                for subscription, source in session.execute(query)
            ]

    @app.post("/api/sources/manual", dependencies=[Depends(_require_admin_key)])
    async def manual_source(payload: ManualSourceInput) -> dict[str, object]:
        try:
            result = await add_public_source(
                get_settings(), _database(), payload.profile, payload.username
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "subscription_id": result.subscription_id,
            "source_id": result.source_id,
            "title": result.title,
            "username": result.username,
        }

    @app.patch("/api/sources/{subscription_id}", dependencies=[Depends(_require_admin_key)])
    def change_source_status(
        subscription_id: int,
        payload: SourceStatusUpdate,
    ) -> dict[str, object]:
        with _database().session() as session:
            subscription = session.get(SourceSubscription, subscription_id)
            if subscription is None:
                raise HTTPException(status_code=404, detail="Source subscription not found")
            set_subscription_status(session, subscription, payload.status)
            return {
                "subscription_id": subscription.id,
                "status": subscription.status,
                "monitor_enabled": subscription.monitor_enabled,
            }

    @app.get("/api/signals", dependencies=[Depends(_require_admin_key)])
    def signals(
        profile: str = "jetski-miami",
        signal_status: str | None = Query(default=None, alias="status"),
        limit: int = Query(default=200, ge=1, le=1000),
    ) -> list[dict[str, object]]:
        with _database().session() as session:
            query = (
                select(Signal, ChatSource)
                .join(ChatSource, ChatSource.id == Signal.source_id)
                .join(SearchProfileRecord, SearchProfileRecord.id == Signal.profile_id)
                .where(SearchProfileRecord.slug == profile)
                .order_by(Signal.created_at.desc())
                .limit(limit)
            )
            if signal_status:
                query = query.where(Signal.status == signal_status)
            else:
                query = query.where(Signal.status != "rejected")
            return [
                {
                    "id": signal.id,
                    "platform": source.platform,
                    "source_title": source.title,
                    "source_username": source.username,
                    "telegram_message_id": signal.telegram_message_id,
                    "message_date": _iso(signal.message_date),
                    "permalink": signal.permalink,
                    "text": signal.text,
                    "author_user_id": signal.author_user_id,
                    "author_username": signal.author_username,
                    "author_display_name": signal.author_display_name,
                    "final_score": signal.final_score,
                    "reasons": signal.classification_reasons,
                    "language": (signal.extracted_data or {}).get("language"),
                    "extracted_data": signal.extracted_data,
                    "status": signal.status,
                    "review_note": signal.review_note,
                }
                for signal, source in session.execute(query)
            ]

    @app.patch("/api/signals/{signal_id}", dependencies=[Depends(_require_admin_key)])
    def change_signal_status(signal_id: int, payload: SignalReview) -> dict[str, object]:
        with _database().session() as session:
            signal = session.get(Signal, signal_id)
            if signal is None:
                raise HTTPException(status_code=404, detail="Signal not found")
            lead = review_signal(session, signal, payload.status, payload.note)
            return {
                "signal_id": signal.id,
                "status": signal.status,
                "lead_id": lead.id if lead else None,
            }

    @app.get("/api/leads", dependencies=[Depends(_require_admin_key)])
    def leads(
        lead_status: str | None = Query(default=None, alias="status"),
        limit: int = Query(default=200, ge=1, le=1000),
    ) -> list[dict[str, object]]:
        with _database().session() as session:
            primary_signal_id = (
                select(LeadSignal.signal_id)
                .join(Signal, Signal.id == LeadSignal.signal_id)
                .where(LeadSignal.lead_id == Lead.id)
                .order_by(
                    LeadSignal.is_primary.desc(),
                    Signal.message_date.desc(),
                    LeadSignal.id.desc(),
                )
                .limit(1)
                .correlate(Lead)
                .scalar_subquery()
            )
            primary_signal = aliased(Signal)
            query = (
                select(Lead, ChatSource, primary_signal)
                .outerjoin(ChatSource, ChatSource.id == Lead.source_id)
                .outerjoin(primary_signal, primary_signal.id == primary_signal_id)
                .order_by(Lead.created_at.desc())
                .limit(limit)
            )
            if lead_status:
                query = query.where(Lead.status == lead_status)
            return [
                {
                    "id": lead.id,
                    "source_title": source.title if source else None,
                    "telegram_user_id": lead.telegram_user_id,
                    "platform": lead.platform,
                    "external_user_id": lead.external_user_id,
                    "username": lead.username,
                    "display_name": lead.display_name,
                    "language": lead.language,
                    "intent": lead.intent,
                    "location": lead.location,
                    "event_date": _iso(lead.event_date),
                    "party_size": lead.party_size,
                    "confidence": lead.confidence,
                    "status": lead.status,
                    "message_text": signal.text if signal else None,
                    "message_permalink": signal.permalink if signal else None,
                    "message_date": _iso(signal.message_date) if signal else None,
                    "created_at": _iso(lead.created_at),
                }
                for lead, source, signal in session.execute(query)
            ]

    @app.patch("/api/leads/{lead_id}", dependencies=[Depends(_require_admin_key)])
    def change_lead(lead_id: int, payload: LeadUpdate) -> dict[str, object]:
        with _database().session() as session:
            lead = session.get(Lead, lead_id)
            if lead is None:
                raise HTTPException(status_code=404, detail="Lead not found")
            if payload.status:
                update_lead_status(session, lead, payload.status)
            return {"lead_id": lead.id, "status": lead.status}

    @app.get("/api/runs", dependencies=[Depends(_require_admin_key)])
    def runs(limit: int = Query(default=50, ge=1, le=500)) -> list[dict[str, object]]:
        with _database().session() as session:
            records = list(session.scalars(select(RunLog).order_by(RunLog.id.desc()).limit(limit)))
            return [
                {
                    "id": item.id,
                    "run_type": item.run_type,
                    "profile_slug": item.profile_slug,
                    "status": item.status,
                    "counters": item.counters,
                    "error": item.error,
                    "started_at": _iso(item.started_at),
                    "finished_at": _iso(item.finished_at),
                }
                for item in records
            ]

    @app.post("/api/ingest", dependencies=[Depends(_require_admin_key)])
    def ingest(payload: IngestMessageInput) -> dict[str, object]:
        """Normalize a message supplied by an authorized external connector."""
        try:
            result = ingest_message(
                get_settings(),
                _database(),
                payload.profile,
                build_classifier(get_settings()),
                NormalizedMessage(
                    platform=payload.platform,
                    source_external_id=payload.source_external_id,
                    source_title=payload.source_title,
                    source_url=payload.source_url,
                    source_kind=payload.source_kind,
                    message_external_id=payload.message_external_id,
                    message_url=payload.message_url,
                    text=payload.text,
                    published_at=payload.published_at,
                    author_external_id=payload.author_external_id,
                    author_username=payload.author_username,
                    author_display_name=payload.author_display_name,
                    language=payload.language,
                ),
            )
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return asdict(result)

    @app.get("/api/jobs/current", dependencies=[Depends(_require_admin_key)])
    def current_job() -> dict[str, object]:
        return dict(_job_state)

    @app.post("/api/jobs/{kind}", status_code=202, dependencies=[Depends(_require_admin_key)])
    async def launch_job(
        kind: Literal[
            "discover", "monitor", "backfill", "reclassify", "sync-dialogs"
        ],
        payload: JobRequest,
        background_tasks: BackgroundTasks,
    ) -> dict[str, object]:
        if _job_lock.locked():
            raise HTTPException(status_code=409, detail="Another Telegram job is already running")
        if _job_state.get("status") in {"accepted", "running"}:
            raise HTTPException(status_code=409, detail="Another Telegram job is already running")
        _job_state.update({"status": "accepted", "kind": kind, "profile": payload.profile})
        background_tasks.add_task(_execute_job, kind, payload)
        return {"accepted": True, "kind": kind}

    @app.get("/exports/leads.csv", dependencies=[Depends(_require_admin_key)])
    def download_leads() -> FileResponse:
        path = EXPORT_DIR / "leads.csv"
        export_leads(_database(), path)
        return FileResponse(path, media_type="text/csv", filename="leads.csv")

    @app.get("/exports/sources.csv", dependencies=[Depends(_require_admin_key)])
    def download_sources() -> FileResponse:
        path = EXPORT_DIR / "sources.csv"
        export_sources(_database(), path)
        return FileResponse(path, media_type="text/csv", filename="sources.csv")

    @app.get("/exports/signals.csv", dependencies=[Depends(_require_admin_key)])
    def download_signals() -> FileResponse:
        path = EXPORT_DIR / "signals.csv"
        export_signals(_database(), path)
        return FileResponse(path, media_type="text/csv", filename="signals.csv")

    return app


app = create_app()
