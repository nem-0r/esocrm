"""Фоновая докачка файлов клиентов.

Файл больше 20 МБ не держит сообщение: оно появляется в CRM сразу с пометкой
«загружается», а файл докачивается здесь. Сюда же попадают файлы, которые не
скачались с первого раза (сеть, устаревшая ссылка Telegram).

Задание живёт в самой строке `attachments` (status = pending, meta.source), а
не в памяти процесса: перезапуск шлюза посреди загрузки ничего не теряет —
следующий заход подхватит то же вложение. Попыток пять, с растущей паузой;
потом — видимая причина вместо бесконечного «загружается».
"""

import asyncio
import contextlib
import logging
import os
import shutil
import tempfile
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath

from sqlalchemy import Integer, select

from app.core import storage
from app.core.db import SessionLocal
from app.gateway import governor
from app.gateway.provider import MediaGone, get_provider
from app.models import Attachment, AttachmentStatus, Message, TelegramAccount
from app.realtime.events import emit_to_conversation
from app.services.message_service import to_out

log = logging.getLogger("astra.gateway.backfill")

MAX_ATTEMPTS = 5
# Пауза после неудачной попытки: 30 с, 2 мин, 10 мин, 30 мин.
RETRY_DELAYS = (30, 120, 600, 1800)
IDLE_SECONDS = 5.0
BATCH = 3


async def _due(account_ids: list[int], max_size: int | None = None) -> list[int]:
    """Вложения аккаунтов этого процесса, которые пора докачать.

    `max_size` — когда на диске тесно, берём только небольшие файлы (`governor`):
    крупные подождут, пока место появится.
    """
    if not account_ids:
        return []
    now = datetime.now(UTC).isoformat()
    async with SessionLocal() as db:
        stmt = select(Attachment.id).where(
            Attachment.status == AttachmentStatus.PENDING.value,
            Attachment.deleted_at.is_(None),
            Attachment.meta["source"]["account_id"].astext.cast(Integer).in_(account_ids),
            (Attachment.meta["next_at"].astext.is_(None))
            | (Attachment.meta["next_at"].astext <= now),
        )
        if max_size is not None:
            stmt = stmt.where(Attachment.size_bytes <= max_size)
        rows = await db.execute(stmt.order_by(Attachment.id).limit(BATCH))
        return [row[0] for row in rows.all()]


async def _publish(attachment: Attachment) -> None:
    async with SessionLocal() as db:
        message = await db.get(Message, attachment.message_id)
        if message is None:
            return
        await emit_to_conversation(
            db,
            attachment.conversation_id,
            "message.updated",
            {"message": to_out(message).model_dump(mode="json")},
        )


async def fetch_one(attachment_id: int) -> None:
    """Докачать одно вложение. Любой исход записывается в строку вложения."""
    async with SessionLocal() as db:
        attachment = await db.get(Attachment, attachment_id, with_for_update={"skip_locked": True})
        if attachment is None or attachment.status != AttachmentStatus.PENDING.value:
            return
        meta = dict(attachment.meta or {})
        # Дошла очередь — «ждёт места на диске» больше не про этот файл.
        meta.pop("paused", None)
        source = meta.get("source") or {}
        account = await db.get(TelegramAccount, int(source.get("account_id") or 0))
        chat_id = source.get("chat_id")
        tg_message_id = source.get("tg_message_id")
        if account is None or chat_id is None or tg_message_id is None:
            attachment.status = AttachmentStatus.FAILED.value
            meta["error"] = "Не осталось данных, откуда докачать файл"
            attachment.meta = meta
            await db.commit()
            return
        # Длинную загрузку не держим в открытой транзакции: сначала отмечаем
        # попытку, потом качаем, потом записываем итог отдельной транзакцией.
        meta["attempts"] = int(meta.get("attempts") or 0) + 1
        attempt = meta["attempts"]
        delay = RETRY_DELAYS[min(attempt - 1, len(RETRY_DELAYS) - 1)]
        meta["next_at"] = (datetime.now(UTC) + timedelta(seconds=delay)).isoformat()
        attachment.meta = meta
        file_name = attachment.file_name
        await db.commit()

    workdir = tempfile.mkdtemp(prefix="astra-fetch-")
    error: str | None = None
    final = False
    key: str | None = None
    size = 0
    try:
        path = os.path.join(workdir, "file")
        size = await get_provider().download_media(account, int(chat_id), int(tg_message_id), path)
        now = datetime.now(UTC)
        suffix = PurePosixPath(file_name).suffix.lower()[:16]
        key = f"incoming/{now:%Y}/{now:%m}/{uuid.uuid4().hex}{suffix}"
        await storage.put_file(key, path, attachment.mime_type or "application/octet-stream")
    except MediaGone as exc:
        error, final = str(exc), True
    except Exception as exc:  # noqa: BLE001 — причина уходит менеджеру
        log.exception("Не докачал вложение %s (попытка %s)", attachment_id, attempt)
        error = str(exc) or type(exc).__name__
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    async with SessionLocal() as db:
        attachment = await db.get(Attachment, attachment_id)
        if attachment is None:
            return
        meta = dict(attachment.meta or {})
        if error is None and key is not None:
            attachment.storage_key = key
            attachment.size_bytes = size
            attachment.status = AttachmentStatus.READY.value
            meta.pop("next_at", None)
            meta.pop("error", None)
        elif final or attempt >= MAX_ATTEMPTS:
            attachment.status = AttachmentStatus.FAILED.value
            meta["error"] = error or "Не удалось скачать файл"
        else:
            meta["error"] = error
        attachment.meta = meta
        await db.commit()
        if attachment.status != AttachmentStatus.PENDING.value:
            await _publish(attachment)


async def backfill_loop(
    stop: asyncio.Event, held_account_ids: Callable[[], Awaitable[list[int]]]
) -> None:
    while not stop.is_set():
        try:
            # На диске мало места или сервер занят — фоновая докачка ждёт: файлы остаются
            # в очереди и скачаются сами, когда станет можно (governor).
            limit = governor.backfill_limit()
            due = [] if limit == 0 else await _due(await held_account_ids(), limit)
            for attachment_id in due:
                if stop.is_set():
                    break
                await fetch_one(attachment_id)
        except Exception:
            log.exception("Ошибка в цикле докачки файлов")
            due = []
        if not due:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=IDLE_SECONDS)
