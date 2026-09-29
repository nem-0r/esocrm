"""Синхронизация CRM ↔ Telegram через демо-шлюз.

Демо-шлюз вбрасывает события так, будто они пришли из Telegram: дальше путь
тот же, что у боевого (приёмник шлюза → inbound_service → база → события).
Разбор самих объектов Telethon проверяют модульные тесты (tests/unit).

- голосовое клиента сохраняется с волной и длительностью;
- контакт, «переслано от» — видны, а не пустой пузырь;
- правка клиента обновляет текст; «правка» без текста (реакция) — нет;
- удаление в Telegram помечается, сообщение остаётся;
- прочитали на телефоне — в CRM непрочитанное гаснет;
- большой файл докачивается в фоне; удалённый — понятная причина;
- альбом, отправленный из CRM, при подтяжке истории не задваивается.

Запуск: docker compose exec -T api python -m tests.verify_sync
"""

import asyncio
import base64
import sys
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from app.core import command_bus
from app.core.db import SessionLocal
from app.models import (
    Attachment,
    Client,
    Conversation,
    Message,
    TelegramAccount,
    TelegramPeer,
)
from tests.support import ADMIN, BASE, Checks, login, until

PROBE_TG_ID = 770_000_202
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000100000001080600000"
    "01f15c4890000000a4944415478da6360000002000155ff2ba00000000049454e44ae426082"
)


async def _cleanup() -> None:
    async with SessionLocal() as db:
        conv_ids = [
            row[0]
            for row in (
                await db.execute(
                    select(Conversation.id)
                    .join(Client, Client.id == Conversation.client_id)
                    .where(Client.telegram_id == PROBE_TG_ID)
                )
            ).all()
        ]
        for conv_id in conv_ids:
            await db.execute(Message.__table__.delete().where(Message.conversation_id == conv_id))
            await db.execute(Conversation.__table__.delete().where(Conversation.id == conv_id))
        await db.execute(TelegramPeer.__table__.delete().where(TelegramPeer.tg_user_id == PROBE_TG_ID))
        await db.execute(Client.__table__.delete().where(Client.telegram_id == PROBE_TG_ID))
        await db.commit()


async def _message(conversation_id: int, tg_id: int) -> Message | None:
    async with SessionLocal() as db:
        return await db.scalar(
            select(Message).where(
                Message.conversation_id == conversation_id, Message.tg_message_id == tg_id
            )
        )


async def _attachment_of(conversation_id: int, tg_id: int) -> Attachment | None:
    async with SessionLocal() as db:
        return await db.scalar(
            select(Attachment)
            .join(Message, Message.id == Attachment.message_id)
            .where(Message.conversation_id == conversation_id, Message.tg_message_id == tg_id)
        )


async def run() -> int:  # noqa: PLR0915
    c = Checks("Синхронизация CRM ↔ Telegram")
    await _cleanup()
    async with SessionLocal() as db:
        account = await db.scalar(
            select(TelegramAccount)
            .where(TelegramAccount.deleted_at.is_(None), TelegramAccount.is_active.is_(True))
            .order_by(TelegramAccount.id)
        )
        account_id = account.id
    now = datetime.now(UTC)

    async def incoming(tg_id: int, **extra) -> None:  # noqa: ANN003
        payload = {
            "tg_user_id": PROBE_TG_ID,
            "access_hash": 5151,
            "first_name": "Синхрон",
            "tg_message_id": tg_id,
            "date": extra.pop("date", now).isoformat(),
            **extra,
        }
        await command_bus.call(account_id, "demo_incoming", payload, timeout=20)
        await asyncio.sleep(0.3)

    c.section("Первое сообщение")
    await incoming(910001, text="Здравствуйте", date=now - timedelta(minutes=20))
    async with SessionLocal() as db:
        conv = await db.scalar(
            select(Conversation)
            .join(Client, Client.id == Conversation.client_id)
            .where(Client.telegram_id == PROBE_TG_ID, Conversation.account_id == account_id)
        )
    if not c.check("диалог заведён", conv is not None):
        return c.finish()
    conv_id, chat_id = conv.id, conv.tg_chat_id

    c.section("Голосовое клиента")
    wave = [0, 4, 9, 17, 31, 22, 11, 3] * 12 + [5, 5, 5, 5]
    await incoming(
        910002,
        media_kind="voice",
        attachments=[
            {
                "kind": "voice",
                "file_name": "voice-910002.ogg",
                "mime_type": "audio/ogg",
                "status": "ready",
                "duration_sec": 5,
                "waveform": wave,
                "body_b64": base64.b64encode(b"OggS-demo-voice").decode(),
            }
        ],
    )
    voice = await _message(conv_id, 910002)
    att = await _attachment_of(conv_id, 910002)
    c.check("сообщение — голосовое", voice is not None and voice.kind.value == "voice")
    c.check(
        "волна и длительность сохранены",
        att is not None and list(att.waveform or b"") == wave and att.duration_sec == 5,
    )

    c.section("Контакт и «переслано от»")
    await incoming(
        910003,
        text="📇 Контакт: Анна Ли, +79990001122",
        meta={"contact": {"first_name": "Анна", "last_name": "Ли", "phone": "+79990001122", "tg_user_id": 42}},
    )
    await incoming(
        910004,
        text="Посмотрите, что мне прислали",
        meta={"forwarded_from": {"name": "Мария", "date": (now - timedelta(days=1)).isoformat()}},
    )
    admin = await login(ADMIN)
    try:
        page = (await admin.get(f"{BASE}/conversations/{conv_id}/messages", params={"limit": 50})).json()
        by_tg = {}
        async with SessionLocal() as db:
            for item in page["items"]:
                row = await db.get(Message, item["id"])
                by_tg[row.tg_message_id] = item
        contact = by_tg.get(910003, {})
        c.check(
            "контакт — текстом и карточкой",
            contact.get("text", "").startswith("📇") and (contact.get("meta") or {}).get("contact", {}).get("phone") == "+79990001122",
            contact.get("meta"),
        )
        forwarded = by_tg.get(910004, {})
        c.check(
            "пересланное подписано «от Мария»",
            ((forwarded.get("meta") or {}).get("forwarded_from") or {}).get("name") == "Мария",
            forwarded.get("meta"),
        )

        c.section("Правки клиента")
        await incoming(910001, text="Здравствуйте! Исправила", is_edit=True, edit_date=now.isoformat())
        first = await _message(conv_id, 910001)
        c.check("текст обновлён", first is not None and first.text == "Здравствуйте! Исправила", first.text if first else None)
        c.check("отмечено «изменено»", first is not None and first.edited_at is not None)
        edited_at = first.edited_at if first else None
        await incoming(910001, text=None, is_edit=True)
        again = await _message(conv_id, 910001)
        c.check("«правка» без текста (реакция) текст не стирает", again is not None and again.text == "Здравствуйте! Исправила")
        c.check("и не двигает отметку правки", again is not None and again.edited_at == edited_at)

        c.section("Удаление в Telegram")
        await command_bus.call(account_id, "demo_delete", {"tg_message_ids": [910004]}, timeout=20)
        await asyncio.sleep(0.3)
        gone = await _message(conv_id, 910004)
        c.check(
            "сообщение осталось, но помечено «удалено в Telegram»",
            gone is not None and bool((gone.meta or {}).get("deleted_in_telegram_at")),
            gone.meta if gone else None,
        )

        c.section("Прочитано на телефоне")
        await admin.post(f"{BASE}/conversations/{conv_id}/read")
        await incoming(910010, text="Ещё вопрос", date=now - timedelta(minutes=2))
        await incoming(910011, text="И ещё", date=now - timedelta(minutes=1))
        async with SessionLocal() as db:
            unread = (await db.get(Conversation, conv_id)).unread_count
        c.check("два непрочитанных", unread == 2, unread)
        await command_bus.call(account_id, "demo_read_inbox", {"chat_id": chat_id, "max_id": 910010}, timeout=20)
        await asyncio.sleep(0.3)
        async with SessionLocal() as db:
            unread = (await db.get(Conversation, conv_id)).unread_count
        c.check("прочитали первое на телефоне — осталось одно", unread == 1, unread)
        await command_bus.call(account_id, "demo_read_inbox", {"chat_id": chat_id, "max_id": 910011}, timeout=20)
        await asyncio.sleep(0.3)
        async with SessionLocal() as db:
            conv = await db.get(Conversation, conv_id)
        c.check("прочитали всё — непрочитанных нет", conv.unread_count == 0, conv.unread_count)
        c.check("напоминание «ждёт ответа» погасло", conv.awaiting_seen_at is not None)
        c.check("а сам клиент по-прежнему ждёт ответа", conv.awaiting_reply_since is not None)

        c.section("Фоновая докачка")
        source = {"account_id": account_id, "chat_id": chat_id, "tg_message_id": 910020}
        await incoming(
            910020,
            media_kind="video",
            attachments=[
                {"kind": "video", "file_name": "big.mp4", "mime_type": "video/mp4",
                 "status": "pending", "size": 30 * 1024 * 1024, "source": source}
            ],
        )
        pending = await _attachment_of(conv_id, 910020)
        # Докачку подхватывает шлюз сразу, на быстром стенде она успевает закончиться
        # до этой проверки — важно, что сообщение уже видно и файл не потерян.
        c.check(
            "сообщение появилось сразу, файл «загружается» или уже загружен",
            pending is not None and pending.status in ("pending", "ready"),
            pending.status if pending else None,
        )
        ready = await until(lambda: _ready(conv_id, 910020), 25)
        c.check("файл докачан в фоне", bool(ready), ready)
        await incoming(
            910021,
            media_kind="document",
            attachments=[
                {"kind": "document", "file_name": "lost.pdf", "mime_type": "application/pdf",
                 "status": "pending", "size": 25 * 1024 * 1024,
                 "source": {"account_id": account_id, "chat_id": chat_id, "tg_message_id": 0}}
            ],
        )
        failed = await until(lambda: _status(conv_id, 910021, "failed"), 25)
        c.check("удалённое в Telegram — «не удалось» с причиной, а не вечная загрузка", bool(failed), failed)

        await incoming(
            910022,
            media_kind="video",
            attachments=[
                {"kind": "video", "file_name": "huge.mp4", "mime_type": "video/mp4",
                 "status": "too_large", "size": 900 * 1024 * 1024}
            ],
        )
        huge = await _attachment_of(conv_id, 910022)
        c.check("слишком большой — виден, но не скачивается", huge is not None and huge.status == "too_large")
        if huge is not None:
            r = await admin.get(f"{BASE}/files/{huge.id}")
            c.check("и понятно, где открыть", r.status_code == 404 and "Telegram" in r.text, r.text[:120])

        c.section("Альбом из CRM не задваивается при подтяжке истории")
        uploads = []
        for index in range(2):
            r = await admin.post(
                f"{BASE}/files/upload", files={"file": (f"a{index}.png", PNG, "image/png")}
            )
            uploads.append(r.json())
        r = await admin.post(f"{BASE}/conversations/{conv_id}/messages", json={"uploads": uploads})
        message_id = r.json().get("id")
        sent = await until(lambda: _extra_ids(message_id), 20)
        c.check("альбом отправлен, запомнено 2 номера", bool(sent) and len(sent) == 2, sent)
        if sent:
            async with SessionLocal() as db:
                before = await db.scalar(select(func.count()).select_from(Message).where(Message.conversation_id == conv_id))
            await incoming(sent[0], outgoing=True, text=None, live=False)
            async with SessionLocal() as db:
                after = await db.scalar(select(func.count()).select_from(Message).where(Message.conversation_id == conv_id))
            c.check("номер первого фото из истории — не новое сообщение", after == before, (before, after))
    finally:
        await admin.aclose()
        await _cleanup()
    return c.finish()


async def _ready(conversation_id: int, tg_id: int):  # noqa: ANN202
    att = await _attachment_of(conversation_id, tg_id)
    return (att.status, att.storage_key) if att and att.status == "ready" and att.storage_key else None


async def _status(conversation_id: int, tg_id: int, status: str):  # noqa: ANN202
    att = await _attachment_of(conversation_id, tg_id)
    if att and att.status == status:
        return (att.meta or {}).get("error") or status
    return None


async def _extra_ids(message_id: int):  # noqa: ANN202
    async with SessionLocal() as db:
        message = await db.get(Message, message_id)
        if message is None or message.tg_message_id is None:
            return None
        return [*(message.tg_extra_ids or []), message.tg_message_id]


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
