import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select
from telethon import types

from leadfinder.backfill import run_backfill
from leadfinder.classification.types import ClassificationResult
from leadfinder.config import Settings
from leadfinder.db import Database
from leadfinder.models import ChatSource, NotificationOutbox, NotificationSubscriber, Signal
from leadfinder.profiles import JETSKI_MIAMI
from leadfinder.repository import upsert_profile
from leadfinder.services import set_subscription_status, upsert_subscription


class FakeClassifier:
    def classify(self, _profile, _context) -> ClassificationResult:
        return ClassificationResult(
            is_candidate=True,
            final_score=0.91,
            keyword_score=0.8,
            embedding_score=0.88,
            llm_score=0.96,
            reasons=("gemini:buyer",),
            extracted_data={"intent": "rent", "location": "Miami", "language": "ru"},
        )


class FakeMessage:
    id = 701
    date = datetime.now(UTC) - timedelta(days=1)
    raw_text = "Подскажите, где взять гидроцикл в аренду в Майами?"
    out = False

    async def get_sender(self):
        return types.User(id=42, first_name="Buyer", username="buyer42")


class FakeTelegramClient:
    async def connect(self):
        return None

    async def disconnect(self):
        return None

    async def is_user_authorized(self):
        return True

    async def get_entity(self, _entity):
        return object()

    def iter_messages(self, _entity, **_kwargs):
        async def generate():
            yield FakeMessage()

        return generate()


@pytest.mark.parametrize(
    ("notifications_enabled", "expected_outbox"),
    [(False, 0), (True, 1)],
)
def test_backfill_notifications_require_explicit_opt_in(
    tmp_path: Path,
    monkeypatch,
    notifications_enabled: bool,
    expected_outbox: int,
) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'backfill.db'}",
        backfill_notifications_enabled=notifications_enabled,
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
        )
        session.add(source)
        session.flush()
        subscription = upsert_subscription(session, profile.id, source.id)
        set_subscription_status(session, subscription, "approved")
        session.add(NotificationSubscriber(telegram_chat_id=500, active=True))

    monkeypatch.setattr(
        "leadfinder.backfill.create_client",
        lambda _settings: FakeTelegramClient(),
    )
    summary = asyncio.run(
        run_backfill(
            settings,
            database,
            "jetski-miami",
            FakeClassifier(),
            lookback_days=30,
            messages_per_term=5,
            max_service_terms=1,
        )
    )

    assert summary.signals_created == 1
    with database.session() as session:
        assert session.scalar(select(func.count()).select_from(Signal)) == 1
        assert (
            session.scalar(select(func.count()).select_from(NotificationOutbox))
            == expected_outbox
        )
