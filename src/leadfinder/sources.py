from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from sqlalchemy import select
from telethon import types
from telethon.errors import FloodWaitError

from leadfinder.config import Settings
from leadfinder.db import Database
from leadfinder.discovery import upsert_source
from leadfinder.models import SearchProfileRecord
from leadfinder.services import (
    acquire_job_lease,
    finish_run,
    release_job_lease,
    start_run,
    upsert_subscription,
)
from leadfinder.telegram_gateway import create_client


@dataclass(frozen=True, slots=True)
class DialogSyncSummary:
    dialogs_examined: int = 0
    groups_found: int = 0
    sources_created: int = 0


@dataclass(frozen=True, slots=True)
class AddedSource:
    subscription_id: int
    source_id: int
    title: str
    username: str | None


async def add_public_source(
    settings: Settings,
    database: Database,
    profile_slug: str,
    username: str,
) -> AddedSource:
    normalized = username.strip().removeprefix("https://t.me/").lstrip("@").split("/")[0]
    if not normalized:
        raise RuntimeError("Telegram username is empty")
    with database.session() as session:
        profile = session.scalar(
            select(SearchProfileRecord).where(SearchProfileRecord.slug == profile_slug)
        )
        if profile is None:
            raise RuntimeError(f"Unknown profile: {profile_slug}")
        profile_id = profile.id

    client = create_client(settings)
    lease_owner = uuid4().hex
    with database.session() as session:
        lease_acquired = acquire_job_lease(session, "telegram_session", lease_owner)
    if not lease_acquired:
        raise RuntimeError("Another Telegram job is already using this account")
    try:
        await client.connect()
        if not await client.is_user_authorized():
            raise RuntimeError("Telegram is not authorized. Run: uv run leadfinder auth-qr")
        entity = await client.get_entity(normalized)
        if not isinstance(entity, (types.Chat, types.Channel)):
            raise RuntimeError("The Telegram username does not refer to a group")
        if isinstance(entity, types.Channel) and not entity.megagroup:
            raise RuntimeError("Channels are not monitoring sources; provide a group username")
        with database.session() as session:
            source, _created = upsert_source(session, entity, "manual")
            source.permission_status = "readable"
            subscription = upsert_subscription(session, profile_id, source.id)
            return AddedSource(
                subscription_id=subscription.id,
                source_id=source.id,
                title=source.title,
                username=source.username,
            )
    finally:
        try:
            await client.disconnect()
        finally:
            with database.session() as session:
                release_job_lease(session, "telegram_session", lease_owner)


async def sync_account_dialogs(
    settings: Settings,
    database: Database,
    profile_slug: str,
    limit: int | None = None,
) -> DialogSyncSummary:
    with database.session() as session:
        profile = session.scalar(
            select(SearchProfileRecord).where(SearchProfileRecord.slug == profile_slug)
        )
        if profile is None:
            raise RuntimeError(f"Unknown profile: {profile_slug}. Run seed-profile first.")
        profile_id = profile.id
        run_id = start_run(session, "sync_dialogs", profile_slug).id

    client = create_client(settings)
    lease_owner = uuid4().hex
    with database.session() as session:
        lease_acquired = acquire_job_lease(session, "telegram_session", lease_owner)
    if not lease_acquired:
        with database.session() as session:
            finish_run(session, run_id, "failed", {}, "Another Telegram job is running")
        raise RuntimeError("Another Telegram job is already using this account")
    dialogs_examined = groups_found = sources_created = 0
    try:
        await client.connect()
        if not await client.is_user_authorized():
            raise RuntimeError("Telegram is not authorized. Run: uv run leadfinder auth-qr")
        async for dialog in client.iter_dialogs(limit=limit):
            dialogs_examined += 1
            entity = dialog.entity
            if not isinstance(entity, (types.Chat, types.Channel)):
                continue
            if isinstance(entity, types.Channel) and not entity.megagroup:
                continue
            groups_found += 1
            with database.session() as session:
                source, created = upsert_source(session, entity, "account.dialogs")
                source.permission_status = "accessible"
                upsert_subscription(session, profile_id, source.id)
                sources_created += int(created)
        summary = DialogSyncSummary(dialogs_examined, groups_found, sources_created)
        with database.session() as session:
            finish_run(
                session,
                run_id,
                "completed",
                {
                    "dialogs_examined": dialogs_examined,
                    "groups_found": groups_found,
                    "sources_created": sources_created,
                },
            )
        return summary
    except FloodWaitError as exc:
        error = f"Telegram rate limit: retry after {exc.seconds} seconds"
        with database.session() as session:
            finish_run(session, run_id, "rate_limited", {"retry_after_seconds": exc.seconds}, error)
        raise RuntimeError(error) from exc
    except Exception as exc:
        with database.session() as session:
            finish_run(session, run_id, "failed", {}, str(exc))
        raise
    finally:
        try:
            await client.disconnect()
        finally:
            with database.session() as session:
                release_job_lease(session, "telegram_session", lease_owner)
