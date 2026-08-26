from datetime import UTC, datetime
from pathlib import Path

from telethon import types

from leadfinder.config import Settings
from leadfinder.db import Database
from leadfinder.discovery import message_permalink, upsert_source
from leadfinder.models import ChatSource


def test_message_permalink_supports_public_and_private_supergroups() -> None:
    public = ChatSource(
        telegram_chat_id=-1001234567890,
        title="Public group",
        username="public_group",
    )
    private = ChatSource(
        telegram_chat_id=-1001629943702,
        title="Private group",
    )

    assert message_permalink(public, 42) == "https://t.me/public_group/42"
    assert message_permalink(private, 12947) == "https://t.me/c/1629943702/12947"


def test_upsert_source_keeps_public_group_url_and_metadata(tmp_path: Path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'test.db'}")
    database = Database(settings)
    database.create_all()
    entity = types.Channel(
        id=1647224772,
        title="Miami public group",
        photo=types.ChatPhotoEmpty(),
        date=datetime.now(UTC),
        megagroup=True,
        username="MiamiHotChat",
        participants_count=4859,
    )

    with database.session() as session:
        source, created = upsert_source(session, entity, "manual")
        source_id = source.id

    with database.session() as session:
        source = session.get(ChatSource, source_id)

        assert created is True
        assert source is not None
        assert source.source_url == "https://t.me/MiamiHotChat"
        assert source.is_public is True
        assert source.kind == "group"
        assert source.participant_count == 4859
        assert source.participant_count_updated_at is not None
