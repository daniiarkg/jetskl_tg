from pathlib import Path

from sqlalchemy import func, select

from leadfinder.config import Settings
from leadfinder.db import Database
from leadfinder.models import (
    ChatSource,
    Lead,
    NotificationOutbox,
    NotificationSubscriber,
    Signal,
)
from leadfinder.notifications import (
    deliver_pending_notifications,
    enqueue_signal_notifications,
    poll_bot_updates,
)
from leadfinder.profiles import JETSKI_MIAMI
from leadfinder.repository import upsert_profile


class FakeBotAPI:
    def __init__(self, updates: list[dict[str, object]] | None = None):
        self.updates = updates or []
        self.sent: list[dict[str, object]] = []
        self.deleted: list[tuple[int, int]] = []
        self.answers: list[tuple[str, str]] = []
        self.cleared: list[tuple[int, int]] = []

    def get_me(self) -> dict[str, object]:
        return {"username": "leadfinder_test_bot"}

    def get_updates(self, offset: int) -> list[dict[str, object]]:
        return [item for item in self.updates if int(item["update_id"]) >= offset]

    def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        reply_markup: dict[str, object] | None = None,
    ) -> dict[str, object]:
        self.sent.append(
            {"chat_id": chat_id, "text": text, "reply_markup": reply_markup}
        )
        return {"message_id": len(self.sent)}

    def delete_message(self, chat_id: int, message_id: int) -> None:
        self.deleted.append((chat_id, message_id))

    def answer_callback(self, callback_id: str, text: str) -> None:
        self.answers.append((callback_id, text))

    def clear_buttons(self, chat_id: int, message_id: int) -> None:
        self.cleared.append((chat_id, message_id))


def _database_with_signal(tmp_path: Path) -> tuple[Database, int]:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'notifications.db'}")
    database = Database(settings)
    database.create_all()
    with database.session() as session:
        profile = upsert_profile(session, JETSKI_MIAMI, query_limit=3)
        source = ChatSource(
            telegram_chat_id=-10012345,
            title="Русские в Майами",
            username="russian_miami_test",
            language="ru",
        )
        session.add(source)
        session.flush()
        signal = Signal(
            profile_id=profile.id,
            source_id=source.id,
            telegram_message_id=99,
            permalink="https://t.me/russian_miami_test/99",
            text="Подскажите, где арендовать jetski в Майами?",
            author_user_id=777,
            author_username="buyer777",
            final_score=0.82,
            status="possible",
            extracted_data={"language": "ru", "location": "Miami"},
        )
        session.add(signal)
        session.flush()
        signal_id = signal.id
    return database, signal_id


def test_start_access_key_delivery_and_inline_approval(tmp_path: Path) -> None:
    database, signal_id = _database_with_signal(tmp_path)
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'notifications.db'}",
        telegram_notification_bot_token="test-token",
        telegram_notification_access_key="secret-access",
    )
    api = FakeBotAPI(
        [
            {
                "update_id": 1,
                "message": {
                    "message_id": 10,
                    "chat": {"id": 500},
                    "from": {"id": 500, "username": "operator"},
                    "text": "/start",
                },
            },
            {
                "update_id": 2,
                "message": {
                    "message_id": 11,
                    "chat": {"id": 500},
                    "from": {"id": 500, "username": "operator"},
                    "text": "secret-access",
                },
            },
        ]
    )

    poll = poll_bot_updates(settings, database, api)
    assert poll.subscriptions_activated == 1
    assert (500, 11) in api.deleted
    with database.session() as session:
        subscriber = session.scalar(select(NotificationSubscriber))
        assert subscriber is not None and subscriber.active
        assert session.scalar(select(func.count()).select_from(NotificationOutbox)) == 1

    delivery = deliver_pending_notifications(settings, database, api)
    assert delivery.sent == 1
    notification = api.sent[-1]
    assert "Потенциальный jetski-лид" in str(notification["text"])
    assert notification["reply_markup"] is not None

    api.updates.append(
        {
            "update_id": 3,
            "callback_query": {
                "id": "callback-1",
                "from": {"id": 500, "username": "operator"},
                "data": f"signal:qualified:{signal_id}",
                "message": {"message_id": 20, "chat": {"id": 500}},
            },
        }
    )
    second_poll = poll_bot_updates(settings, database, api)
    assert second_poll.reviews_applied == 1
    with database.session() as session:
        signal = session.get(Signal, signal_id)
        assert signal is not None and signal.status == "qualified"
        assert session.scalar(select(func.count()).select_from(Lead)) == 1


def test_enqueue_is_idempotent_for_active_subscriber(tmp_path: Path) -> None:
    database, signal_id = _database_with_signal(tmp_path)
    with database.session() as session:
        subscriber = NotificationSubscriber(telegram_chat_id=501, active=True)
        session.add(subscriber)
        signal = session.get(Signal, signal_id)
        assert signal is not None
        assert enqueue_signal_notifications(session, signal) == 1
        session.flush()
        assert enqueue_signal_notifications(session, signal) == 0

    with database.session() as session:
        assert session.scalar(select(func.count()).select_from(NotificationOutbox)) == 1
