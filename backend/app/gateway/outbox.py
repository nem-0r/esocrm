"""Очередь исходящих: строка `outbox` → отправка в Telegram.

Два вида заданий:

- **отправка** — текст и файлы сообщения CRM. Файлы берутся из хранилища во
  временный каталог (не в память: видео до 200 МБ), разбиваются на отправки
  Telegram (`send_plan`) и уходят;
- **пересылка** — настоящая пересылка Telegram внутри одного аккаунта. Все
  сообщения одной пересылки уходят одним запросом (альбомы остаются альбомами).
  Если Telegram пересылку не принял по причине, которую повтор не исправит
  (исходное удалено, пересылка запрещена), задание само превращается в отправку
  копии — в нём заранее лежат текст и файлы.

Ни одна плохая строка не должна уронить цикл — ошибка логируется, строка
получает понятную причину, цикл идёт дальше.
"""

import asyncio
import contextlib
import logging
import os
import shutil
import tempfile
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import storage
from app.core.config import settings
from app.core.db import SessionLocal
from app.gateway.provider import ForwardImpossible, PermanentFailure, get_provider
from app.gateway.send_plan import OutgoingFile
from app.models import (
    ActorKind,
    Attachment,
    AttachmentStatus,
    Conversation,
    Message,
    MessageStatus,
    Outbox,
    OutboxStatus,
    TelegramAccount,
)
from app.realtime.events import emit_to_conversation
from app.services.audit import log_event
from app.services.message_service import attachment_kind, to_out

log = logging.getLogger("astra.gateway.outbox")

OUTBOX_IDLE_POLL_SECONDS = 1.0
# Таблица бэкоффа по номеру попытки: 1-я — 5с, 2-я — 30с. При MAX_ATTEMPTS=3
# третья неудача сразу проваливает строку, третье значение (120с) — задел
# на случай, если порог попыток когда-нибудь увеличат.
BACKOFF_SECONDS = (5, 30, 120)
MAX_ATTEMPTS = 3
DEMO_READ_DELAY_SECONDS = 2
# Файл ещё докачивается из Telegram — ждём столько и пробуем снова, попытку
# не тратим: это не сбой, а очередь.
FILE_NOT_READY_WAIT = 20

_background_tasks: set[asyncio.Task[None]] = set()

# Аккаунты под запретом Telegram (FloodWait): до срока их очередь не трогаем
# вовсе — и сообщения, поставленные уже во время запрета, тоже. Аккаунт
# обслуживает один процесс шлюза, поэтому памяти процесса достаточно; после
# перезапуска срок переживают отложенные строки очереди в базе.
_account_paused_until: dict[int, datetime] = {}


def _account_paused(account_id: int) -> bool:
    until = _account_paused_until.get(account_id)
    if until is None:
        return False
    if until <= datetime.now(UTC):
        _account_paused_until.pop(account_id, None)
        return False
    return True


class SendImpossible(RuntimeError):
    """Отправить нечем: файла нет в CRM. Повтор не поможет."""


class FileNotReady(RuntimeError):
    """Файл ещё докачивается из Telegram — отправка подождёт."""


async def publish_update(db: AsyncSession, conversation_id: int, message: Message) -> None:
    await emit_to_conversation(
        db, conversation_id, "message.updated", {"message": to_out(message).model_dump(mode="json")}
    )


# ------------------------------------------------------------ файлы


async def load_files(
    db: AsyncSession, attachment_ids: list[int], workdir: str
) -> list[OutgoingFile]:
    """Вложения → временные файлы с метаданными для отправки."""
    if not attachment_ids:
        return []
    rows = await db.execute(select(Attachment).where(Attachment.id.in_(attachment_ids)))
    by_id = {a.id: a for a in rows.scalars().all()}
    files: list[OutgoingFile] = []
    for attachment_id in attachment_ids:
        attachment = by_id.get(attachment_id)
        if attachment is None or attachment.deleted_at is not None:
            continue
        if attachment.status == AttachmentStatus.PENDING.value:
            raise FileNotReady(attachment.file_name)
        if attachment.status != AttachmentStatus.READY.value or not attachment.storage_key:
            raise SendImpossible(
                f"Файла «{attachment.file_name}» нет в CRM — отправить его можно только "
                "из Telegram"
            )
        folder = os.path.join(workdir, str(attachment.id))
        os.makedirs(folder, exist_ok=True)
        # Имя на диске — короткое и латинское: русское имя в 200 символов не
        # влезло бы в лимит файловой системы. Настоящее имя уходит атрибутом.
        suffix = os.path.splitext(attachment.file_name)[1].lower()[:16]
        path = os.path.join(folder, f"file{suffix}")
        try:
            await storage.download_to_file(attachment.storage_key, path)
        except storage.ObjectNotFound as exc:
            raise SendImpossible(
                f"Файл «{attachment.file_name}» пропал из хранилища — загрузите его заново"
            ) from exc
        thumb_path = None
        if attachment.thumb_key:
            candidate = os.path.join(folder, "thumb.jpg")
            with contextlib.suppress(storage.ObjectNotFound):
                await storage.download_to_file(attachment.thumb_key, candidate)
                thumb_path = candidate
        extra = attachment.meta or {}
        files.append(
            OutgoingFile(
                path=path,
                file_name=attachment.file_name,
                mime_type=attachment.mime_type,
                kind=attachment_kind(attachment),
                width=attachment.width,
                height=attachment.height,
                duration_sec=attachment.duration_sec,
                waveform=bytes(attachment.waveform) if attachment.waveform else None,
                thumb_path=thumb_path,
                title=extra.get("title"),
                performer=extra.get("performer"),
            )
        )
    return files


# ------------------------------------------------------------ неудачи


async def _fail(
    db: AsyncSession,
    pairs: list[tuple[Outbox, Message]],
    error: str,
    *,
    final: bool = False,
    wait_seconds: int | None = None,
) -> None:
    """Неудача для одной строки или целой пересылки.

    `wait_seconds` — Telegram назвал срок (FloodWait) или файл ещё качается:
    ждём, попытку не тратим. `final` — повтор бессмыслен, сразу ошибка.
    """
    now = datetime.now(UTC)
    for outbox, message in pairs:
        outbox.error_text = error
        outbox.locked_by = None
        if wait_seconds is not None:
            outbox.status = OutboxStatus.PENDING
            outbox.next_attempt_at = now + timedelta(seconds=wait_seconds)
            continue
        outbox.attempts = MAX_ATTEMPTS if final else outbox.attempts + 1
        if outbox.attempts >= MAX_ATTEMPTS:
            outbox.status = OutboxStatus.FAILED
            message.status = MessageStatus.FAILED
            message.error_text = error
            log.warning("Сообщение %s не отправлено: %s", message.id, error)
        else:
            delay = BACKOFF_SECONDS[min(outbox.attempts - 1, len(BACKOFF_SECONDS) - 1)]
            outbox.status = OutboxStatus.PENDING
            outbox.next_attempt_at = now + timedelta(seconds=delay)
            log.info("Повтор отправки сообщения %s через %sс: %s", message.id, delay, error)
    await db.commit()
    if wait_seconds is None:
        for outbox, message in pairs:
            await publish_update(db, outbox.conversation_id, message)


async def _handle_exception(
    db: AsyncSession,
    pairs: list[tuple[Outbox, Message]],
    conversation: Conversation,
    exc: BaseException,
) -> None:
    from app.gateway.mtproto_provider import ClientBlocked, RetryAfter

    if isinstance(exc, RetryAfter):
        # Telegram назвал срок. Повтор раньше срока продлевает запрет,
        # поэтому просто переносим отправку и не тратим попытку. Запрет — на
        # весь аккаунт, а не на одно сообщение: до срока ждёт вся его очередь.
        # Иначе шлюз тут же ударился бы о тот же запрет следующим сообщением.
        log.warning("Отправка отложена на %s с по требованию Telegram", exc.seconds)
        account_id = pairs[0][0].account_id
        until = datetime.now(UTC) + timedelta(seconds=exc.seconds)
        _account_paused_until[account_id] = until
        await db.execute(
            update(Outbox)
            .where(
                Outbox.account_id == account_id,
                Outbox.status == OutboxStatus.PENDING,
                Outbox.next_attempt_at < until,
            )
            .values(next_attempt_at=until)
        )
        await _fail(db, pairs, str(exc), wait_seconds=exc.seconds)
        return
    if isinstance(exc, FileNotReady):
        await _fail(
            db,
            pairs,
            f"Файл «{exc}» ещё загружается из Telegram — отправка подождёт",
            wait_seconds=FILE_NOT_READY_WAIT,
        )
        return
    if isinstance(exc, ClientBlocked):
        # Блокировка не снимется сама за секунды — гонять бэкофф бессмысленно,
        # сразу финальный отказ. Флаг на диалоге предупредит менеджера в
        # интерфейсе, прежде чем он попробует написать снова.
        conversation.is_blocked_by_client = True
        await _fail(db, pairs, str(exc), final=True)
        from app.services import conversation_service

        detail = await conversation_service.build_detail(db, conversation.id)
        await conversation_service.emit_updated(db, detail)
        return
    if isinstance(exc, (PermanentFailure, SendImpossible)):
        await _fail(db, pairs, str(exc), final=True)
        return
    await _fail(db, pairs, str(exc) or type(exc).__name__)


# ------------------------------------------------------------ отправка


async def _claim(db: AsyncSession, outbox: Outbox, worker_id: str) -> None:
    outbox.status = OutboxStatus.SENDING
    outbox.locked_by = worker_id
    outbox.locked_until = datetime.now(UTC) + timedelta(seconds=settings.gateway_lease_seconds)


async def _process_send(
    db: AsyncSession,
    outbox: Outbox,
    message: Message,
    account: TelegramAccount,
    conversation: Conversation,
) -> list[Message]:
    provider = get_provider()
    workdir = tempfile.mkdtemp(prefix="astra-out-")
    try:
        attachment_ids = list((outbox.payload or {}).get("attachment_ids") or [])
        files = await load_files(db, attachment_ids, workdir)
        result = await provider.send_message(
            account,
            conversation.tg_chat_id,
            (outbox.payload or {}).get("text"),
            message.random_id or 0,
            files,
        )
    except Exception as exc:  # noqa: BLE001 — любая причина должна дойти до менеджера
        await _handle_exception(db, [(outbox, message)], conversation, exc)
        return []
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    now = datetime.now(UTC)
    if result.tg_message_id is not None:
        message.tg_message_id = result.tg_message_id
    message.tg_extra_ids = list(result.extra_ids) or None
    message.status = MessageStatus.SENT
    message.sent_at = now
    message.error_text = None
    outbox.status = OutboxStatus.DONE
    outbox.error_text = None
    await log_event(
        db,
        action="message.sent_by_gateway",
        entity_type="message",
        entity_id=message.id,
        actor_kind=ActorKind.GATEWAY,
        after={"tg_message_id": result.tg_message_id, "extra_ids": list(result.extra_ids)},
    )
    await db.commit()
    await publish_update(db, outbox.conversation_id, message)
    return [message]


async def _process_forward(
    worker_id: str,
    db: AsyncSession,
    outbox: Outbox,
    message: Message,
    account: TelegramAccount,
    conversation: Conversation,
    spec: dict[str, Any],
) -> list[Message]:
    """Настоящая пересылка Telegram: все сообщения одной пересылки — одним запросом."""
    rows = [outbox]
    batch_id = spec.get("batch_id")
    if batch_id:
        siblings = (
            await db.execute(
                select(Outbox)
                .where(
                    Outbox.conversation_id == outbox.conversation_id,
                    Outbox.id != outbox.id,
                    Outbox.status == OutboxStatus.PENDING,
                    Outbox.payload["forward"]["batch_id"].astext == str(batch_id),
                )
                .order_by(Outbox.id)
                .with_for_update(skip_locked=True)
            )
        ).scalars().all()
        rows.extend(siblings)
    pairs: list[tuple[Outbox, Message]] = []
    source_ids: list[int] = []
    for row in rows:
        row_message = message if row is outbox else await db.get(Message, row.message_id)
        if row_message is None:
            row.status = OutboxStatus.FAILED
            row.error_text = "Сообщение не найдено"
            continue
        await _claim(db, row, worker_id)
        pairs.append((row, row_message))
        source_ids.extend(int(value) for value in row.payload["forward"]["tg_message_ids"])
    await db.flush()

    provider = get_provider()
    try:
        new_ids = await provider.forward_messages(
            account,
            conversation.tg_chat_id,
            int(spec["from_chat_id"]),
            source_ids,
            bool(spec.get("drop_author")),
            message.random_id or 0,
        )
    except ForwardImpossible as exc:
        # Переслать «по-настоящему» нельзя — отправим копией: текст и файлы
        # для неё положены в задание заранее. Попытку не тратим.
        log.info("Пересылка невозможна (%s) — отправляю копией", exc)
        now = datetime.now(UTC)
        for row, row_message in pairs:
            payload = dict(row.payload or {})
            payload.pop("forward", None)
            payload["forward_fallback"] = str(exc)[:200]
            row.payload = payload
            row.status = OutboxStatus.PENDING
            row.next_attempt_at = now
            row.locked_by = None
            # Уходит копия, а не пересылка Telegram: подписи «Переслано» у клиента
            # нет, и сообщение можно править — пометка в CRM должна это знать.
            meta = dict(row_message.meta or {})
            if meta.get("forwarded"):
                meta["forwarded"] = {**meta["forwarded"], "native": False, "hide_sender": True}
                row_message.meta = meta
        await db.commit()
        return []
    except Exception as exc:  # noqa: BLE001
        await _handle_exception(db, pairs, conversation, exc)
        return []

    now = datetime.now(UTC)
    offset = 0
    for row, row_message in pairs:
        count = len(row.payload["forward"]["tg_message_ids"])
        part = new_ids[offset : offset + count]
        offset += count
        if part:
            row_message.tg_message_id = part[-1]
            row_message.tg_extra_ids = part[:-1] or None
        row_message.status = MessageStatus.SENT
        row_message.sent_at = now
        row_message.error_text = None
        row.status = OutboxStatus.DONE
        row.error_text = None
    await log_event(
        db,
        action="message.forwarded_by_gateway",
        entity_type="conversation",
        entity_id=conversation.id,
        actor_kind=ActorKind.GATEWAY,
        after={"messages": [m.id for _, m in pairs], "tg_message_ids": new_ids},
    )
    await db.commit()
    for _, row_message in pairs:
        await publish_update(db, conversation.id, row_message)
    return [m for _, m in pairs]


async def process_outbox_row(worker_id: str, db: AsyncSession, outbox: Outbox) -> None:
    await _claim(db, outbox, worker_id)
    await db.flush()

    message = await db.get(Message, outbox.message_id)
    account = await db.get(TelegramAccount, outbox.account_id)
    conversation = await db.get(Conversation, outbox.conversation_id)
    if message is None or account is None or conversation is None:
        outbox.status = OutboxStatus.FAILED
        outbox.error_text = "Сообщение, аккаунт или диалог не найдены"
        await db.commit()
        return

    spec = (outbox.payload or {}).get("forward")
    if spec:
        sent = await _process_forward(worker_id, db, outbox, message, account, conversation, spec)
    else:
        sent = await _process_send(db, outbox, message, account, conversation)

    if settings.demo_mode:
        for item in sent:
            task = asyncio.create_task(_simulate_read(item.id, outbox.conversation_id))
            _background_tasks.add(task)
            task.add_done_callback(_background_tasks.discard)


async def _simulate_read(message_id: int, conversation_id: int) -> None:
    """Только демо-режим: имитирует `updateReadHistoryOutbox` — клиент «прочитал»
    сообщение примерно через 2 секунды после отправки, чтобы в интерфейсе было
    видно состояние из двух галочек ещё до подключения настоящего Telegram.
    """
    await asyncio.sleep(DEMO_READ_DELAY_SECONDS)
    try:
        async with SessionLocal() as db:
            message = await db.get(Message, message_id)
            if message is None or message.status != MessageStatus.SENT:
                return
            message.read_at = datetime.now(UTC)
            message.status = MessageStatus.READ
            await db.commit()
            await publish_update(db, conversation_id, message)
    except Exception:
        log.exception("Не удалось имитировать прочтение сообщения %s", message_id)


# ------------------------------------------------------------ цикл


async def _claim_one_outbox(db: AsyncSession, account_ids: list[int]) -> Outbox | None:
    now = datetime.now(UTC)
    row = await db.execute(
        select(Outbox)
        .where(
            Outbox.account_id.in_(account_ids),
            Outbox.status == OutboxStatus.PENDING,
            Outbox.next_attempt_at <= now,
        )
        # По порядку внутри диалога. Сообщение, которое ждёт повтора (сбой,
        # файл ещё докачивается), следующие не держит — как и в самом Telegram:
        # текст, отправленный во время загрузки большого видео, приходит раньше
        # него. Запрет Telegram (FloodWait) откладывает всю очередь аккаунта
        # целиком (_handle_exception) — тогда порядок сохраняется.
        .order_by(Outbox.conversation_id, Outbox.id)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    return row.scalars().first()


async def drain_account_outbox(worker_id: str, account_id: int, stop: asyncio.Event) -> bool:
    """Разобрать очередь исходящих одного аккаунта до конца. True — было что слать."""
    processed_any = False
    while not stop.is_set() and not _account_paused(account_id):
        async with SessionLocal() as db:
            outbox = await _claim_one_outbox(db, [account_id])
            if outbox is None:
                break
            processed_any = True
            try:
                await process_outbox_row(worker_id, db, outbox)
            except Exception:
                log.exception("Ошибка отправки outbox %s", outbox.id)
                await db.rollback()
    return processed_any


async def outbox_loop(
    worker_id: str, stop: asyncio.Event, held_account_ids: Any
) -> None:
    """Очередь на аккаунт своя и разбирается параллельно с остальными: крупное
    вложение у одного не держит готовые сообщения совсем другого аккаунта."""
    while not stop.is_set():
        account_ids = await held_account_ids()
        processed_any = False
        if account_ids:
            results = await asyncio.gather(
                *(drain_account_outbox(worker_id, account_id, stop) for account_id in account_ids)
            )
            processed_any = any(results)
        if not processed_any:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=OUTBOX_IDLE_POLL_SECONDS)
