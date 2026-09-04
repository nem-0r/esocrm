"""Аренда аккаунтов процессом шлюза — см. docs/07-architecture.md §2.

Процесс пишет себя в `gateway_workers`, каждые `gateway_heartbeat_seconds`
продлевает свою отметку и аренду своих аккаунтов на `gateway_lease_seconds`,
а свободные (аренда истекла или её не было) забирает через
`select ... for update skip locked` — так два процесса не возьмут один
аккаунт одновременно. Процесс пропал — аренда истекла — аккаунт подхватывает
соседний и поднимает сессию из базы.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import GatewayWorker, TelegramAccount


async def register_worker(db: AsyncSession, worker_id: str, hostname: str, capacity: int) -> None:
    """Первая запись о процессе. Перезапуск с тем же id — обновляет строку, не дублирует."""
    now = datetime.now(UTC)
    stmt = pg_insert(GatewayWorker).values(
        id=worker_id, hostname=hostname, capacity=capacity, heartbeat_at=now, started_at=now
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[GatewayWorker.id],
        set_={"hostname": hostname, "capacity": capacity, "heartbeat_at": now},
    )
    await db.execute(stmt)
    await db.commit()


async def heartbeat(db: AsyncSession, worker_id: str) -> None:
    """Отметка процесса и продление аренды его аккаунтов."""
    now = datetime.now(UTC)
    lease_until = now + timedelta(seconds=settings.gateway_lease_seconds)
    await db.execute(
        update(GatewayWorker).where(GatewayWorker.id == worker_id).values(heartbeat_at=now)
    )
    await db.execute(
        update(TelegramAccount)
        .where(TelegramAccount.worker_id == worker_id, TelegramAccount.deleted_at.is_(None))
        .values(lease_until=lease_until)
    )
    await db.commit()


async def _held_count(db: AsyncSession, worker_id: str) -> int:
    rows = await db.execute(
        select(TelegramAccount.id).where(
            TelegramAccount.worker_id == worker_id,
            TelegramAccount.is_active.is_(True),
            TelegramAccount.deleted_at.is_(None),
        )
    )
    return len(rows.all())


async def claim_accounts(db: AsyncSession, worker_id: str, capacity: int) -> list[int]:
    """Забрать свободные аккаунты сверх уже удержанных, но не больше вместимости."""
    remaining = capacity - await _held_count(db, worker_id)
    if remaining <= 0:
        return []

    now = datetime.now(UTC)
    free = await db.execute(
        select(TelegramAccount.id)
        .where(
            TelegramAccount.is_active.is_(True),
            TelegramAccount.deleted_at.is_(None),
            or_(TelegramAccount.lease_until.is_(None), TelegramAccount.lease_until < now),
        )
        .with_for_update(skip_locked=True)
        .limit(remaining)
    )
    ids = [row[0] for row in free.all()]
    if not ids:
        await db.commit()
        return []

    lease_until = now + timedelta(seconds=settings.gateway_lease_seconds)
    await db.execute(
        update(TelegramAccount)
        .where(TelegramAccount.id.in_(ids))
        .values(worker_id=worker_id, lease_until=lease_until)
    )
    await db.commit()
    return ids


async def release_all(db: AsyncSession, worker_id: str) -> None:
    """При остановке процесса — освободить аккаунты сразу, не дожидаясь протухания аренды."""
    await db.execute(
        update(TelegramAccount)
        .where(TelegramAccount.worker_id == worker_id)
        .values(worker_id=None, lease_until=None)
    )
    await db.commit()
