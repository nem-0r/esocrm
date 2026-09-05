"""Цикл шлюза: аренда аккаунтов и разбор очереди исходящих.

Два независимых цикла на процесс:

- аренда — раз в `gateway_heartbeat_seconds` продлевает отметку и забирает
  свободные аккаунты (docs/07-architecture.md §2);
- очередь `outbox` — разбирается непрерывно, чтобы клиент в интерфейсе не
  ждал часиками лишние секунды (docs/07-architecture.md §3).

Ни одна плохая строка не должна уронить цикл целиком — ошибка на строке
логируется и цикл продолжает со следующей.
"""

import asyncio
import contextlib
import logging
import signal
import socket
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import storage
from app.core.command_bus import serve as serve_commands
from app.core.config import settings
from app.core.db import SessionLocal
from app.gateway import handlers, lease
from app.gateway.provider import get_provider
from app.models import (
    ActorKind,
    Attachment,
    Conversation,
    Message,
    MessageStatus,
    Outbox,
    OutboxStatus,
    TelegramAccount,
)
from app.realtime.events import emit_to_conversation
from app.services.audit import log_event
from app.services.message_service import to_out

log = logging.getLogger("astra.gateway")

OUTBOX_IDLE_POLL_SECONDS = 1.0
# Таблица бэкоффа по номеру попытки: 1-я — 5с, 2-я — 30с. При MAX_ATTEMPTS=3
# третья неудача сразу проваливает строку, третье значение (120с) — задел
# на случай, если порог попыток когда-нибудь увеличат.
BACKOFF_SECONDS = (5, 30, 120)
MAX_ATTEMPTS = 3
DEMO_READ_DELAY_SECONDS = 2

# Ссылки на фоновые задачи имитации «прочитано» — без них asyncio может
# собрать задачу мусором до её завершения.
_background_tasks: set[asyncio.Task[None]] = set()

# Какие аккаунты держит этот процесс прямо сейчас. Канал команд спрашивает набор
# на каждую команду: аренда переезжает, и отвечать за чужой аккаунт нельзя.
_held: set[int] = set()


def _worker_id(hostname: str) -> str:
    return f"{hostname}-{uuid.uuid4().hex[:8]}"


async def _held_account_ids(db: AsyncSession, worker_id: str) -> list[int]:
    rows = await db.execute(
        select(TelegramAccount.id).where(
            TelegramAccount.worker_id == worker_id,
            TelegramAccount.is_active.is_(True),
            TelegramAccount.deleted_at.is_(None),
        )
    )
    return [r[0] for r in rows.all()]


async def _start_accounts(account_ids: list[int]) -> None:
    """Поднять сессию у только что арендованных аккаунтов."""
    provider = get_provider()
    _held.update(account_ids)
    async with SessionLocal() as db:
        rows = await db.execute(select(TelegramAccount).where(TelegramAccount.id.in_(account_ids)))
        for account in rows.scalars().all():
            try:
                await provider.start(account)
            except Exception:
                log.exception("Не удалось поднять сессию аккаунта %s", account.id)
                continue
            # Пока процесс был выключен (например, при выкатке новой версии),
            # живые сообщения могли прийти и остаться незамеченными — сессия
            # не помнит, докуда досмотрена лента. Догоняем в фоне, не задерживая
            # подключение остальных арендованных аккаунтов.
            task = asyncio.create_task(handlers.catch_up(account.id))
            _background_tasks.add(task)
            task.add_done_callback(_background_tasks.discard)


async def _lease_loop(worker_id: str, hostname: str, stop: asyncio.Event) -> None:
    async with SessionLocal() as db:
        await lease.register_worker(db, worker_id, hostname, settings.gateway_capacity)
    log.info("Шлюз %s зарегистрирован, вместимость %s", worker_id, settings.gateway_capacity)

    while not stop.is_set():
        try:
            async with SessionLocal() as db:
                await lease.heartbeat(db, worker_id)
                claimed = await lease.claim_accounts(db, worker_id, settings.gateway_capacity)
            # Набор пересобираем из базы, а не копим: аккаунт могли отобрать,
            # отключить или удалить, и тогда команды по нему не наши.
            _held.clear()
            _held.update(await handlers.held_account_ids(worker_id))
            if claimed:
                log.info("Шлюз %s принял аккаунты: %s", worker_id, claimed)
                await _start_accounts(claimed)
        except Exception:
            log.exception("Ошибка в цикле аренды")
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=settings.gateway_heartbeat_seconds)


async def _claim_one_outbox(db: AsyncSession, account_ids: list[int]) -> Outbox | None:
    now = datetime.now(UTC)
    row = await db.execute(
        select(Outbox)
        .where(
            Outbox.account_id.in_(account_ids),
            Outbox.status == OutboxStatus.PENDING,
            Outbox.next_attempt_at <= now,
        )
        # Строго по порядку внутри диалога — иначе сообщения придут вперемешку.
        .order_by(Outbox.conversation_id, Outbox.id)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    return row.scalars().first()


async def _publish_update(db: AsyncSession, conversation_id: int, message: Message) -> None:
    await emit_to_conversation(
        db, conversation_id, "message.updated", {"message": to_out(message).model_dump(mode="json")}
    )


async def _handle_failure(db: AsyncSession, outbox: Outbox, message: Message, error: str) -> None:
    outbox.attempts += 1
    if outbox.attempts >= MAX_ATTEMPTS:
        outbox.status = OutboxStatus.FAILED
        outbox.error_text = error
        message.status = MessageStatus.FAILED
        message.error_text = error
        log.warning(
            "Сообщение %s не отправлено после %s попыток: %s", message.id, outbox.attempts, error
        )
    else:
        delay = BACKOFF_SECONDS[min(outbox.attempts - 1, len(BACKOFF_SECONDS) - 1)]
        outbox.status = OutboxStatus.PENDING
        outbox.next_attempt_at = datetime.now(UTC) + timedelta(seconds=delay)
        outbox.error_text = error
        log.info("Повтор отправки сообщения %s через %sс: %s", message.id, delay, error)
    await db.commit()
    await _publish_update(db, outbox.conversation_id, message)


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
            await _publish_update(db, conversation_id, message)
    except Exception:
        log.exception("Не удалось имитировать прочтение сообщения %s", message_id)


async def _load_attachments(db: AsyncSession, attachment_ids: list[int]) -> list[dict]:
    """Тело вложений из хранилища — провайдеру нужны файлы, а не только их id."""
    if not attachment_ids:
        return []
    rows = await db.execute(select(Attachment).where(Attachment.id.in_(attachment_ids)))
    by_id = {a.id: a for a in rows.scalars().all()}
    items: list[dict] = []
    for aid in attachment_ids:
        attachment = by_id.get(aid)
        if attachment is None:
            continue
        body = await storage.get_object(attachment.storage_key)
        items.append(
            {
                "file_name": attachment.file_name,
                "mime_type": attachment.mime_type,
                "body": body,
            }
        )
    return items


async def _process_outbox_row(worker_id: str, db: AsyncSession, outbox: Outbox) -> None:
    outbox.status = OutboxStatus.SENDING
    outbox.locked_by = worker_id
    outbox.locked_until = datetime.now(UTC) + timedelta(seconds=settings.gateway_lease_seconds)
    await db.flush()

    message = await db.get(Message, outbox.message_id)
    account = await db.get(TelegramAccount, outbox.account_id)
    conversation = await db.get(Conversation, outbox.conversation_id)
    if message is None or account is None or conversation is None:
        outbox.status = OutboxStatus.FAILED
        outbox.error_text = "Сообщение, аккаунт или диалог не найдены"
        await db.commit()
        return

    provider = get_provider()
    try:
        attachments = await _load_attachments(db, outbox.payload.get("attachment_ids", []))
        result = await provider.send_message(
            account,
            conversation.tg_chat_id,
            outbox.payload.get("text"),
            message.random_id or 0,
            attachments,
        )
    except Exception as exc:
        from app.gateway.mtproto_provider import RetryAfter

        if isinstance(exc, RetryAfter):
            # Telegram назвал срок. Повтор раньше срока продлевает запрет,
            # поэтому просто переносим отправку и не тратим попытку.
            outbox.status = OutboxStatus.PENDING
            outbox.next_attempt_at = datetime.now(UTC) + timedelta(seconds=exc.seconds)
            outbox.error_text = str(exc)
            await db.commit()
            log.warning(
                "Отправка %s отложена на %s с по требованию Telegram",
                message.id,
                exc.seconds,
            )
            return
        await _handle_failure(db, outbox, message, str(exc))
        return

    now = datetime.now(UTC)
    message.tg_message_id = result.tg_message_id
    message.status = MessageStatus.SENT
    message.sent_at = now
    outbox.status = OutboxStatus.DONE
    await log_event(
        db,
        action="message.sent_by_gateway",
        entity_type="message",
        entity_id=message.id,
        actor_kind=ActorKind.GATEWAY,
        after={"tg_message_id": result.tg_message_id},
    )
    await db.commit()
    await _publish_update(db, outbox.conversation_id, message)

    if settings.demo_mode:
        task = asyncio.create_task(_simulate_read(message.id, outbox.conversation_id))
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)


async def _outbox_loop(worker_id: str, stop: asyncio.Event) -> None:
    while not stop.is_set():
        async with SessionLocal() as db:
            account_ids = await _held_account_ids(db, worker_id)

        processed_any = False
        if account_ids:
            while not stop.is_set():
                async with SessionLocal() as db:
                    outbox = await _claim_one_outbox(db, account_ids)
                    if outbox is None:
                        break
                    processed_any = True
                    try:
                        await _process_outbox_row(worker_id, db, outbox)
                    except Exception:
                        log.exception("Ошибка отправки outbox %s", outbox.id)
                        await db.rollback()

        if not processed_any:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=OUTBOX_IDLE_POLL_SECONDS)


async def run() -> None:
    """Точка входа шлюза: регистрация, аренда, разбор очереди, корректная остановка."""
    hostname = socket.gethostname()
    worker_id = _worker_id(hostname)
    stop = asyncio.Event()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)

    # Куда провайдер отдаёт полученное из Telegram. Ставим до аренды: аккаунт
    # может подняться сразу, и первое же входящее должно попасть в базу.
    provider = get_provider()
    if hasattr(provider, "set_sinks"):
        provider.set_sinks(
            handlers.write_inbound, handlers.write_read_receipt, handlers.write_status
        )

    try:
        await asyncio.gather(
            _lease_loop(worker_id, hostname, stop),
            _outbox_loop(worker_id, stop),
            serve_commands(handlers.handle, lambda account_id: account_id in _held, stop),
        )
    finally:
        log.info("Шлюз %s останавливается, освобождаю аккаунты", worker_id)
        async with SessionLocal() as db:
            await lease.release_all(db, worker_id)
