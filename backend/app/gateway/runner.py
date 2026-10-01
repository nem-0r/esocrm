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

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.command_bus import serve as serve_commands
from app.core.config import settings
from app.core.db import SessionLocal
from app.gateway import backfill, handlers, lease, load, outbox, topology
from app.gateway.provider import get_provider
from app.models import (
    TelegramAccount,
)

log = logging.getLogger("astra.gateway")

# Ссылки на фоновые задачи (подъём и остановка сессий) — без них asyncio может
# собрать задачу мусором до её завершения.
_background_tasks: set[asyncio.Task[None]] = set()

# Какие аккаунты держит этот процесс прямо сейчас. Канал команд спрашивает набор
# на каждую команду: аренда переезжает, и отвечать за чужой аккаунт нельзя.
_held: set[int] = set()


def _worker_id(hostname: str) -> str:
    """Случайный хвост нарочно: если бы id переживал перезапуск процесса (например,
    был завязан на номер слота), heartbeat при старте продлил бы аренду аккаунтов,
    которые числятся за этим id с прошлой жизни процесса — раньше, чем этот же
    процесс успеет поднять для них настоящую сессию Telethon (`_start_accounts`
    вызывается только для только что ЗАНЯТЫХ в этом заходе, а не для уже
    числящихся). Аккаунт выглядел бы «удержанным» без единого живого соединения.
    Случайный id гарантирует, что после падения процесса его старые аккаунты
    для новой попытки — чужие, и достаются через обычный `claim_accounts` вместе
    с настоящим стартом сессии."""
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


async def _stop_accounts(account_ids: set[int]) -> None:
    """Отпустить сессии аккаунтов, чью аренду забрал другой воркер.

    Без этого прежний процесс продолжает держать открытое MTProto-соединение
    (а если шёл вход по QR — ещё и фоновую задачу ожидания сканирования) на
    аккаунт, за который формально больше не отвечает, пока новый воркер
    поднимает своё соединение параллельно. Два живых сеанса с одного
    Telegram-аккаунта — ровно то, что резко повышает риск блокировки.
    """
    provider = get_provider()
    async with SessionLocal() as db:
        rows = await db.execute(select(TelegramAccount).where(TelegramAccount.id.in_(account_ids)))
        for account in rows.scalars().all():
            try:
                await provider.stop(account)
            except Exception:
                log.exception("Не удалось освободить сессию аккаунта %s", account.id)


async def _wait_for_siblings(worker_id: str, stop: asyncio.Event) -> None:
    """Дождаться, пока поднимутся соседние процессы контейнера (но не дольше
    `gateway_settle_seconds`), — иначе первый, кто успел, видит себя единственным
    живым и забирает все аккаунты, а остальные ядра простаивают."""
    expected = topology.expected_workers()
    if expected <= 1:
        return
    loop = asyncio.get_running_loop()
    deadline = loop.time() + settings.gateway_settle_seconds
    while not stop.is_set() and loop.time() < deadline:
        async with SessionLocal() as db:
            alive = await lease.live_workers(db)
        if alive >= expected:
            return
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=1.0)
    log.info("Шлюз %s: часть соседних процессов не отозвалась за срок, иду дальше", worker_id)


async def _lease_loop(worker_id: str, hostname: str, stop: asyncio.Event) -> None:
    async with SessionLocal() as db:
        await lease.register_worker(db, worker_id, hostname, settings.gateway_capacity)
    log.info("Шлюз %s зарегистрирован, вместимость %s", worker_id, settings.gateway_capacity)
    await _wait_for_siblings(worker_id, stop)

    while not stop.is_set():
        try:
            async with SessionLocal() as db:
                await lease.heartbeat(db, worker_id)
                capacity = load.effective_capacity(settings.gateway_capacity)
                if capacity < settings.gateway_capacity:
                    log.info(
                        "Шлюз %s придерживает рост: хост занят, лимит на этот заход %s из %s",
                        worker_id,
                        capacity,
                        settings.gateway_capacity,
                    )
                claimed = await lease.claim_accounts(db, worker_id, capacity)
            # Набор пересобираем из базы, а не копим: аккаунт могли отобрать,
            # отключить или удалить, и тогда команды по нему не наши.
            new_held = await handlers.held_account_ids(worker_id)
            lost = _held - new_held
            _held.clear()
            _held.update(new_held)
            # Не await здесь: подключение (или отключение) одного аккаунта
            # может тянуться, если сеть до Telegram нестабильна, и держать на
            # этом весь цикл — значит задержать heartbeat остальным уже
            # арендованным аккаунтам, рискуя не продлить их аренду вовремя
            # и отдать другому воркеру без всякой причины со стороны Telegram.
            if lost:
                log.info("Шлюз %s потерял аккаунты: %s", worker_id, lost)
                task = asyncio.create_task(_stop_accounts(lost))
                _background_tasks.add(task)
                task.add_done_callback(_background_tasks.discard)
            if claimed:
                log.info("Шлюз %s принял аккаунты: %s", worker_id, claimed)
                task = asyncio.create_task(_start_accounts(claimed))
                _background_tasks.add(task)
                task.add_done_callback(_background_tasks.discard)
        except Exception:
            log.exception("Ошибка в цикле аренды")
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=settings.gateway_heartbeat_seconds)


async def _held_now() -> list[int]:
    """Аккаунты, которые держит этот процесс — прямо из базы: аренда переезжает."""
    return sorted(_held)


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
            handlers.write_inbound,
            handlers.write_read_receipt,
            handlers.write_status,
            handlers.write_qr_session,
            handlers.write_deleted,
            handlers.write_inbox_read,
        )

    async def held_from_db() -> list[int]:
        async with SessionLocal() as db:
            return await _held_account_ids(db, worker_id)

    try:
        await asyncio.gather(
            _lease_loop(worker_id, hostname, stop),
            outbox.outbox_loop(worker_id, stop, held_from_db),
            backfill.backfill_loop(stop, _held_now),
            serve_commands(handlers.handle, lambda account_id: account_id in _held, stop),
        )
    finally:
        log.info("Шлюз %s останавливается, освобождаю аккаунты", worker_id)
        async with SessionLocal() as db:
            await lease.release_all(db, worker_id)
