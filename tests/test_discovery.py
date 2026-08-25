from leadfinder.discovery import message_permalink
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
