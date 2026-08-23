from __future__ import annotations

import asyncio
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from getpass import getpass
from pathlib import Path

import qrcode
from telethon import TelegramClient
from telethon.errors import (
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    PhoneNumberInvalidError,
    SessionPasswordNeededError,
)
from telethon.sessions import StringSession

from leadfinder.config import Settings


def create_client(settings: Settings) -> TelegramClient:
    api_id, api_hash = settings.require_telegram_credentials()
    if settings.telegram_session_string is not None:
        return TelegramClient(
            StringSession(settings.telegram_session_string.get_secret_value()),
            api_id,
            api_hash,
        )
    session_path = settings.telegram_session_path.expanduser()
    session_path.parent.mkdir(parents=True, exist_ok=True)
    return TelegramClient(str(session_path), api_id, api_hash)


async def export_authorized_session_string(settings: Settings) -> str:
    """Return a portable MTProto session without printing or persisting it."""
    client = create_client(settings)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise RuntimeError("Telegram session is not authorized")
        return StringSession.save(client.session)
    finally:
        await client.disconnect()


async def authorize_with_qr(
    settings: Settings,
    output_path: Path,
    timeout_seconds: int = 60,
) -> str:
    client = create_client(settings)
    await client.connect()
    try:
        if await client.is_user_authorized():
            me = await client.get_me()
            return me.username or str(me.id)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        qr_login = await client.qr_login()
        deadline = asyncio.get_running_loop().time() + timeout_seconds

        while True:
            qrcode.make(qr_login.url).save(output_path)
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise RuntimeError("QR authorization timed out; run auth-qr again")

            token_ttl = (qr_login.expires - datetime.now(UTC)).total_seconds()
            token_wait = max(1.0, min(remaining, token_ttl - 2.0))
            try:
                await qr_login.wait(timeout=token_wait)
                break
            except SessionPasswordNeededError:
                if not sys.stdin.isatty():
                    raise RuntimeError(
                        "QR was accepted, but Telegram requires the 2FA password. "
                        "Run auth-qr in a local terminal so the password can be entered securely."
                    ) from None
                password = getpass("Telegram 2FA password: ")
                await client.sign_in(password=password)
                break
            except TimeoutError as exc:
                if asyncio.get_running_loop().time() >= deadline:
                    raise RuntimeError("QR authorization timed out; run auth-qr again") from exc
                await qr_login.recreate()

        me = await client.get_me()
        return me.username or str(me.id)
    finally:
        await client.disconnect()
        output_path.unlink(missing_ok=True)


async def authorize_with_code(
    settings: Settings,
    phone_number: str,
    code_provider: Callable[[], str],
    password_provider: Callable[[], str] | None = None,
) -> str:
    """Authorize locally using a Telegram login code without persisting credentials."""
    client = create_client(settings)
    await client.connect()
    try:
        if await client.is_user_authorized():
            me = await client.get_me()
            return me.username or str(me.id)

        phone = phone_number.strip()
        if not phone:
            raise RuntimeError("Telegram phone number must not be empty")
        try:
            sent_code = await client.send_code_request(phone)
        except PhoneNumberInvalidError:
            raise RuntimeError("Telegram rejected the phone number") from None

        code = code_provider().replace(" ", "").replace("-", "").strip()
        if not code:
            raise RuntimeError("Telegram login code must not be empty")
        try:
            await client.sign_in(
                phone=phone,
                code=code,
                phone_code_hash=sent_code.phone_code_hash,
            )
        except PhoneCodeInvalidError:
            raise RuntimeError("Telegram login code is invalid") from None
        except PhoneCodeExpiredError:
            raise RuntimeError("Telegram login code expired; run auth-code again") from None
        except SessionPasswordNeededError:
            if password_provider is None:
                raise RuntimeError(
                    "Telegram requires the 2FA password; run auth-code in a local terminal"
                ) from None
            password = password_provider()
            if not password:
                raise RuntimeError("Telegram 2FA password must not be empty") from None
            await client.sign_in(password=password)

        me = await client.get_me()
        return me.username or str(me.id)
    finally:
        await client.disconnect()


async def authorized_account(settings: Settings) -> str | None:
    client = create_client(settings)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            return None
        me = await client.get_me()
        return me.username or str(me.id)
    finally:
        await client.disconnect()
