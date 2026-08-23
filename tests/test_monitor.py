import asyncio
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import func, select
from telethon import types

from leadfinder.classification import HybridClassifier
from leadfinder.config import Settings
from leadfinder.db import Database
from leadfinder.models import ChatSource, Lead, Signal, SourceSubscription
from leadfinder.monitor import run_monitor
from leadfinder.profiles import JETSKI_MIAMI
from leadfinder.repository import upsert_profile
from leadfinder.services import set_subscription_status, upsert_subscription


class FakeMessage:
    id = 501
    date = datetime(2026, 8, 21, 12, tzinfo=UTC)
    raw_text = "Where can I rent two jet skis in Miami tomorrow for 4 people?"
    out = False

    async def get_sender(self):
        return types.User(id=42, first_name="Buyer", username="buyer42")


class FakeTelegramClient:
    def __init__(self):
        self.connected = False

    async def connect(self):
        self.connected = True

    async def disconnect(self):
        self.connected = False

    async def is_user_authorized(self):
        return True

    async def get_entity(self, _entity):
        return object()

    def iter_messages(self, _entity, **kwargs):
        async def generate():
            if (kwargs.get("min_id") or 0) < FakeMessage.id:
                yield FakeMessage()

        return generate()


def test_monitor_persists_cursor_signal_and_lead(tmp_path: Path, monkeypatch) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'monitor.db'}",
        discovery_message_lookback_days=365,
        auto_create_leads=True,
    )
    database = Database(settings)
    database.create_all()
    with database.session() as session:
        profile = upsert_profile(session, JETSKI_MIAMI, query_limit=3)
        source = ChatSource(
            telegram_chat_id=-100999,
            username="miami_group",
            title="Miami Group",
            kind="group",
            permission_status="readable_join_request",
        )
        session.add(source)
        session.flush()
        subscription = upsert_subscription(session, profile.id, source.id)
        set_subscription_status(session, subscription, "approved")
        subscription_id = subscription.id

    monkeypatch.setattr("leadfinder.monitor.create_client", lambda _settings: FakeTelegramClient())
    summary = asyncio.run(
        run_monitor(settings, database, "jetski-miami", HybridClassifier())
    )
    assert summary.sources_scanned == 1
    assert summary.signals_created == 1
    assert summary.leads_created_or_updated == 1

    with database.session() as session:
        assert session.scalar(select(func.count()).select_from(Signal)) == 1
        assert session.scalar(select(func.count()).select_from(Lead)) == 1
        subscription = session.get(SourceSubscription, subscription_id)
        assert subscription is not None
        assert subscription.last_scanned_message_id == FakeMessage.id
        assert subscription.source.permission_status == "readable_join_request"

    second = asyncio.run(
        run_monitor(settings, database, "jetski-miami", HybridClassifier())
    )
    assert second.signals_created == 0
    with database.session() as session:
        assert session.scalar(select(func.count()).select_from(Signal)) == 1
