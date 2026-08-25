from __future__ import annotations

import hmac
import html
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from leadfinder.config import Settings
from leadfinder.db import Database
from leadfinder.models import (
    AuditEvent,
    ChatSource,
    Lead,
    LeadSignal,
    NotificationBotState,
    NotificationOutbox,
    NotificationSubscriber,
    Signal,
    utc_now,
)
from leadfinder.services import review_signal


class TelegramBotAPIError(RuntimeError):
    def __init__(self, status_code: int, description: str):
        super().__init__(f"Telegram Bot API error {status_code}: {description[:240]}")
        self.status_code = status_code
        self.description = description


class TelegramBotAPI:
    """Small Bot API client that never includes the token in raised errors."""

    def __init__(
        self,
        token: str,
        *,
        client: httpx.Client | None = None,
        timeout_seconds: float = 20.0,
    ):
        self._client = client or httpx.Client(
            base_url=f"https://api.telegram.org/bot{token}",
            timeout=timeout_seconds,
        )

    def _post(self, method: str, payload: dict[str, object]) -> object:
        try:
            response = self._client.post(f"/{method}", json=payload)
        except httpx.RequestError:
            raise TelegramBotAPIError(0, "request failed") from None
        try:
            body = response.json()
        except ValueError:
            body = {}
        if not response.is_success or not body.get("ok"):
            description = str(body.get("description") or "request failed")
            raise TelegramBotAPIError(response.status_code, description)
        return body.get("result")

    def get_me(self) -> dict[str, object]:
        result = self._post("getMe", {})
        return result if isinstance(result, dict) else {}

    def get_updates(self, offset: int) -> list[dict[str, object]]:
        result = self._post(
            "getUpdates",
            {
                "offset": offset,
                "limit": 100,
                "timeout": 0,
                "allowed_updates": ["message", "callback_query"],
            },
        )
        return result if isinstance(result, list) else []

    def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        reply_markup: dict[str, object] | None = None,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        result = self._post("sendMessage", payload)
        return result if isinstance(result, dict) else {}

    def delete_message(self, chat_id: int, message_id: int) -> None:
        self._post("deleteMessage", {"chat_id": chat_id, "message_id": message_id})

    def answer_callback(self, callback_id: str, text: str) -> None:
        self._post(
            "answerCallbackQuery",
            {"callback_query_id": callback_id, "text": text, "show_alert": False},
        )

    def clear_buttons(self, chat_id: int, message_id: int) -> None:
        self._post(
            "editMessageReplyMarkup",
            {"chat_id": chat_id, "message_id": message_id, "reply_markup": {}},
        )


@dataclass(frozen=True, slots=True)
class BotPollSummary:
    updates: int = 0
    subscriptions_activated: int = 0
    reviews_applied: int = 0
    errors: int = 0


@dataclass(frozen=True, slots=True)
class DeliverySummary:
    attempted: int = 0
    sent: int = 0
    failed: int = 0


def bot_api_from_settings(settings: Settings) -> TelegramBotAPI | None:
    if settings.telegram_notification_bot_token is None:
        return None
    return TelegramBotAPI(
        settings.telegram_notification_bot_token.get_secret_value()
    )


def _audit(
    session: Session,
    action: str,
    entity_type: str,
    entity_id: int | None,
    details: dict[str, object] | None = None,
) -> None:
    session.add(
        AuditEvent(
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            details=details or {},
        )
    )


def _subscriber_for_chat(
    session: Session,
    chat_id: int,
    user: dict[str, object] | None = None,
) -> NotificationSubscriber:
    subscriber = session.scalar(
        select(NotificationSubscriber).where(
            NotificationSubscriber.telegram_chat_id == chat_id
        )
    )
    if subscriber is None:
        subscriber = NotificationSubscriber(telegram_chat_id=chat_id, active=False)
        session.add(subscriber)
        session.flush()
    if user:
        subscriber.telegram_username = str(user.get("username") or "") or None
        names = [str(user.get(key) or "").strip() for key in ("first_name", "last_name")]
        subscriber.display_name = " ".join(name for name in names if name) or None
    subscriber.last_seen_at = utc_now()
    return subscriber


def _lead_id_for_signal(session: Session, signal_id: int) -> int | None:
    return session.scalar(
        select(LeadSignal.lead_id)
        .where(LeadSignal.signal_id == signal_id)
        .order_by(LeadSignal.is_primary.desc(), LeadSignal.id)
        .limit(1)
    )


def _enqueue_for_subscriber(
    session: Session,
    subscriber: NotificationSubscriber,
    signal: Signal,
    lead_id: int | None,
) -> bool:
    idempotency_key = f"signal:{signal.id}:subscriber:{subscriber.id}"
    existing = session.scalar(
        select(NotificationOutbox.id).where(
            NotificationOutbox.idempotency_key == idempotency_key
        )
    )
    if existing is not None:
        return False
    session.add(
        NotificationOutbox(
            subscriber_id=subscriber.id,
            lead_id=lead_id,
            signal_id=signal.id,
            idempotency_key=idempotency_key,
            status="pending",
        )
    )
    return True


def enqueue_signal_notifications(
    session: Session,
    signal: Signal,
    lead: Lead | None = None,
) -> int:
    """Insert outbox rows in the same transaction as the signal."""
    subscribers = list(
        session.scalars(
            select(NotificationSubscriber).where(
                NotificationSubscriber.active.is_(True)
            )
        )
    )
    return sum(
        _enqueue_for_subscriber(session, subscriber, signal, lead.id if lead else None)
        for subscriber in subscribers
    )


def _enqueue_open_signals_for_subscriber(
    session: Session,
    subscriber: NotificationSubscriber,
) -> int:
    signals = list(
        session.scalars(
            select(Signal)
            .where(Signal.status.in_(("new", "possible")))
            .order_by(Signal.id.desc())
            .limit(100)
        )
    )
    return sum(
        _enqueue_for_subscriber(
            session,
            subscriber,
            signal,
            _lead_id_for_signal(session, signal.id),
        )
        for signal in signals
    )


def _is_locked(subscriber: NotificationSubscriber, now: datetime) -> bool:
    locked_until = subscriber.locked_until
    if locked_until is None:
        return False
    if locked_until.tzinfo is None:
        locked_until = locked_until.replace(tzinfo=UTC)
    return locked_until > now


def _connected_message(*, already_active: bool = False) -> str:
    heading = "✅ Уже подключено" if already_active else "✅ Подключено"
    return (
        f"{heading}\n\n"
        "Бот привязан к Leadfinder. Новые потенциальные лиды будут приходить "
        "сюда с кнопками подтверждения и отклонения.\n\n"
        "Статус можно проверить командой /status."
    )


def _process_access_key(
    settings: Settings,
    database: Database,
    api: TelegramBotAPI,
    chat_id: int,
    message_id: int,
    supplied_key: str,
    user: dict[str, object],
) -> bool:
    expected = settings.telegram_notification_access_key
    activated = False
    response_text: str
    with database.session() as session:
        subscriber = _subscriber_for_chat(session, chat_id, user)
        now = datetime.now(UTC)
        if _is_locked(subscriber, now):
            response_text = "Слишком много попыток. Повторите позже."
        elif expected is None:
            subscriber.awaiting_access_key = False
            response_text = "Ключ подписки ещё не настроен администратором."
        elif hmac.compare_digest(
            supplied_key.strip(), expected.get_secret_value()
        ):
            subscriber.active = True
            subscriber.awaiting_access_key = False
            subscriber.failed_access_attempts = 0
            subscriber.locked_until = None
            queued = _enqueue_open_signals_for_subscriber(session, subscriber)
            _audit(
                session,
                "notification.subscriber_activated",
                "notification_subscriber",
                subscriber.id,
                {"queued_open_signals": queued},
            )
            response_text = _connected_message()
            activated = True
        else:
            subscriber.failed_access_attempts += 1
            if subscriber.failed_access_attempts >= 5:
                subscriber.awaiting_access_key = False
                subscriber.locked_until = now + timedelta(minutes=15)
                response_text = "Неверный ключ. Доступ заблокирован на 15 минут."
            else:
                subscriber.awaiting_access_key = True
                remaining = 5 - subscriber.failed_access_attempts
                response_text = f"Неверный ключ. Осталось попыток: {remaining}."

    # The access-key message should not remain in the bot chat when Telegram permits deletion.
    try:
        api.delete_message(chat_id, message_id)
    except TelegramBotAPIError:
        pass
    api.send_message(chat_id, response_text)
    return activated


def _process_message_update(
    settings: Settings,
    database: Database,
    api: TelegramBotAPI,
    message: dict[str, object],
) -> bool:
    chat = message.get("chat")
    user = message.get("from")
    if not isinstance(chat, dict) or not isinstance(user, dict):
        return False
    chat_id = int(chat.get("id") or 0)
    message_id = int(message.get("message_id") or 0)
    text = str(message.get("text") or "").strip()
    if not chat_id or not text:
        return False

    command, _, argument = text.partition(" ")
    command = command.split("@", 1)[0].casefold()
    if command == "/start":
        if argument.strip():
            return _process_access_key(
                settings, database, api, chat_id, message_id, argument, user
            )
        with database.session() as session:
            subscriber = _subscriber_for_chat(session, chat_id, user)
            if subscriber.active:
                response = _connected_message(already_active=True)
            elif _is_locked(subscriber, datetime.now(UTC)):
                response = "Слишком много попыток. Повторите позже."
            else:
                subscriber.awaiting_access_key = True
                response = "Отправьте ключ доступа отдельным сообщением."
        api.send_message(chat_id, response)
        return False

    if command == "/status":
        with database.session() as session:
            subscriber = _subscriber_for_chat(session, chat_id, user)
            active = subscriber.active
        response = (
            _connected_message(already_active=True)
            if active
            else "❌ Не подключено\n\nОтправьте /start и затем ключ доступа."
        )
        api.send_message(chat_id, response)
        return False

    if command == "/stop":
        with database.session() as session:
            subscriber = _subscriber_for_chat(session, chat_id, user)
            subscriber.active = False
            subscriber.awaiting_access_key = False
            _audit(
                session,
                "notification.subscriber_deactivated",
                "notification_subscriber",
                subscriber.id,
            )
        api.send_message(chat_id, "🔕 Отключено\n\nДля подключения отправьте /start.")
        return False

    with database.session() as session:
        subscriber = _subscriber_for_chat(session, chat_id, user)
        awaiting = subscriber.awaiting_access_key
    if awaiting:
        return _process_access_key(
            settings, database, api, chat_id, message_id, text, user
        )

    api.send_message(
        chat_id,
        "Отправьте /start, чтобы подключить уведомления, или /status для проверки.",
    )
    return False


def _process_callback_update(
    database: Database,
    api: TelegramBotAPI,
    callback: dict[str, object],
) -> bool:
    callback_id = str(callback.get("id") or "")
    user = callback.get("from")
    message = callback.get("message")
    data = str(callback.get("data") or "")
    if not callback_id or not isinstance(user, dict) or not isinstance(message, dict):
        return False
    chat = message.get("chat")
    if not isinstance(chat, dict):
        return False
    chat_id = int(chat.get("id") or 0)
    message_id = int(message.get("message_id") or 0)
    parts = data.split(":")
    if len(parts) != 3 or parts[0] != "signal" or parts[1] not in {"qualified", "rejected"}:
        api.answer_callback(callback_id, "Неизвестное действие")
        return False

    with database.session() as session:
        subscriber = session.scalar(
            select(NotificationSubscriber).where(
                NotificationSubscriber.telegram_chat_id == chat_id,
                NotificationSubscriber.active.is_(True),
            )
        )
        if subscriber is None:
            response = "Нет доступа"
            applied = False
        else:
            try:
                signal_id = int(parts[2])
            except ValueError:
                signal_id = 0
            signal = session.get(Signal, signal_id)
            if signal is None:
                response = "Сигнал не найден"
                applied = False
            elif signal.status in {"qualified", "rejected"}:
                response = "Решение уже принято"
                applied = False
            else:
                lead = review_signal(
                    session,
                    signal,
                    parts[1],
                    "Решение принято через Telegram-бота",
                )
                response = (
                    "✅ Лид подтверждён" if parts[1] == "qualified" else "❌ Сигнал отклонён"
                )
                if lead is not None:
                    response += f" · lead #{lead.id}"
                applied = True

    api.answer_callback(callback_id, response)
    if applied:
        try:
            api.clear_buttons(chat_id, message_id)
        except TelegramBotAPIError:
            pass
        api.send_message(chat_id, response)
    return applied


def poll_bot_updates(
    settings: Settings,
    database: Database,
    api: TelegramBotAPI | None = None,
) -> BotPollSummary:
    api = api or bot_api_from_settings(settings)
    if api is None:
        return BotPollSummary()

    with database.session() as session:
        state = session.get(NotificationBotState, "telegram")
        offset = (state.last_update_id + 1) if state is not None else 0

    identity = api.get_me()
    bot_username = str(identity.get("username") or "") or None
    updates = api.get_updates(offset)
    activated = reviews = errors = 0
    last_update_id = offset - 1
    for update in updates:
        update_id = int(update.get("update_id") or 0)
        last_update_id = max(last_update_id, update_id)
        try:
            message = update.get("message")
            callback = update.get("callback_query")
            if isinstance(message, dict):
                activated += int(
                    _process_message_update(settings, database, api, message)
                )
            elif isinstance(callback, dict):
                reviews += int(_process_callback_update(database, api, callback))
        except Exception:
            errors += 1

    with database.session() as session:
        state = session.get(NotificationBotState, "telegram")
        if state is None:
            state = NotificationBotState(key="telegram")
            session.add(state)
        if last_update_id >= 0:
            state.last_update_id = max(state.last_update_id or 0, last_update_id)
        state.bot_username = bot_username

    return BotPollSummary(
        updates=len(updates),
        subscriptions_activated=activated,
        reviews_applied=reviews,
        errors=errors,
    )


def _notification_text(signal: Signal, source: ChatSource, dashboard_url: str) -> str:
    author = signal.author_display_name or signal.author_username or signal.author_user_id or "—"
    if signal.author_username:
        author_html = (
            f'<a href="https://t.me/{html.escape(signal.author_username, quote=True)}">'
            f"@{html.escape(signal.author_username)}</a>"
        )
    else:
        author_html = html.escape(str(author))
    message_text = html.escape(signal.text[:1200])
    source_text = html.escape(source.title)
    message_date = signal.message_date
    if message_date is None:
        message_date_text = "—"
    else:
        if message_date.tzinfo is None:
            message_date = message_date.replace(tzinfo=UTC)
        message_date_text = message_date.astimezone(UTC).strftime("%d.%m.%Y %H:%M UTC")
    status_text = "высокая уверенность" if signal.status == "new" else "нужна проверка"
    links: list[str] = []
    if signal.permalink:
        links.append(
            f'<a href="{html.escape(signal.permalink, quote=True)}">Открыть сообщение</a>'
        )
    if dashboard_url:
        links.append(
            f'<a href="{html.escape(dashboard_url, quote=True)}">Открыть панель</a>'
        )
    links_line = " · ".join(links)
    return (
        "🎯 <b>Потенциальный jetski-лид</b>\n"
        f"Статус: {status_text}\n"
        f"Score: {signal.final_score:.2f}\n"
        f"Автор: {author_html}\n"
        f"Группа: {source_text}\n\n"
        f"Дата сообщения: {message_date_text}\n\n"
        f"{message_text}\n\n"
        f"{links_line}"
    ).strip()


def deliver_pending_notifications(
    settings: Settings,
    database: Database,
    api: TelegramBotAPI | None = None,
) -> DeliverySummary:
    api = api or bot_api_from_settings(settings)
    if api is None:
        return DeliverySummary()
    now = datetime.now(UTC)
    with database.session() as session:
        item_ids = list(
            session.scalars(
                select(NotificationOutbox.id)
                .where(
                    NotificationOutbox.status == "pending",
                    NotificationOutbox.next_attempt_at <= now,
                    NotificationOutbox.attempts < settings.notification_max_attempts,
                )
                .order_by(NotificationOutbox.id)
                .limit(settings.notification_delivery_batch)
            )
        )

    sent = failed = 0
    for item_id in item_ids:
        with database.session() as session:
            item = session.get(NotificationOutbox, item_id)
            if item is None or item.status != "pending":
                continue
            subscriber = session.get(NotificationSubscriber, item.subscriber_id)
            signal = session.get(Signal, item.signal_id)
            if subscriber is None or signal is None or not subscriber.active:
                item.status = "failed"
                item.last_error = "subscriber inactive or signal unavailable"
                failed += 1
                continue
            source = session.get(ChatSource, signal.source_id)
            if source is None:
                item.status = "failed"
                item.last_error = "source unavailable"
                failed += 1
                continue
            chat_id = subscriber.telegram_chat_id
            text = _notification_text(signal, source, settings.dashboard_public_url)
            reply_markup = {
                "inline_keyboard": [
                    [
                        {
                            "text": "✅ Подтвердить лид",
                            "callback_data": f"signal:qualified:{signal.id}",
                        },
                        {
                            "text": "❌ Отклонить",
                            "callback_data": f"signal:rejected:{signal.id}",
                        },
                    ]
                ]
            }

        try:
            api.send_message(chat_id, text, reply_markup=reply_markup)
        except TelegramBotAPIError as exc:
            with database.session() as session:
                item = session.get(NotificationOutbox, item_id)
                if item is None:
                    continue
                item.attempts += 1
                item.last_error = exc.description[:500]
                terminal = item.attempts >= settings.notification_max_attempts
                if exc.status_code == 403:
                    terminal = True
                    subscriber = session.get(NotificationSubscriber, item.subscriber_id)
                    if subscriber is not None:
                        subscriber.active = False
                if terminal:
                    item.status = "failed"
                else:
                    delay_seconds = min(3600, 30 * (2**item.attempts))
                    item.next_attempt_at = datetime.now(UTC) + timedelta(
                        seconds=delay_seconds
                    )
            failed += 1
            continue

        with database.session() as session:
            item = session.get(NotificationOutbox, item_id)
            if item is not None:
                item.status = "sent"
                item.attempts += 1
                item.last_error = None
                item.sent_at = utc_now()
        sent += 1

    return DeliverySummary(attempted=len(item_ids), sent=sent, failed=failed)


def send_test_notification(
    settings: Settings,
    database: Database,
    api: TelegramBotAPI | None = None,
) -> DeliverySummary:
    api = api or bot_api_from_settings(settings)
    if api is None:
        raise RuntimeError("TELEGRAM_NOTIFICATION_BOT_TOKEN is not configured")
    with database.session() as session:
        chat_ids = list(
            session.scalars(
                select(NotificationSubscriber.telegram_chat_id).where(
                    NotificationSubscriber.active.is_(True)
                )
            )
        )
    sent = failed = 0
    for chat_id in chat_ids:
        try:
            api.send_message(
                chat_id,
                "✅ <b>Тест Leadfinder</b>\nУведомления о потенциальных лидах работают.",
            )
            sent += 1
        except TelegramBotAPIError:
            failed += 1
    return DeliverySummary(attempted=len(chat_ids), sent=sent, failed=failed)


def notification_status(settings: Settings, database: Database) -> dict[str, object]:
    with database.session() as session:
        state = session.get(NotificationBotState, "telegram")
        active = session.scalar(
            select(func.count())
            .select_from(NotificationSubscriber)
            .where(NotificationSubscriber.active.is_(True))
        ) or 0
        pending = session.scalar(
            select(func.count())
            .select_from(NotificationOutbox)
            .where(NotificationOutbox.status == "pending")
        ) or 0
        failed = session.scalar(
            select(func.count())
            .select_from(NotificationOutbox)
            .where(NotificationOutbox.status == "failed")
        ) or 0
        return {
            "bot_configured": settings.telegram_notification_bot_token is not None,
            "access_key_configured": settings.telegram_notification_access_key is not None,
            "bot_username": state.bot_username if state is not None else None,
            "active_subscribers": active,
            "pending_notifications": pending,
            "failed_notifications": failed,
        }
