"""Аренда аккаунтов процессом шлюза — см. docs/07-architecture.md §2.

Процесс пишет себя в `gateway_workers`, каждые `gateway_heartbeat_seconds`
продлевает свою отметку и аренду своих аккаунтов на `gateway_lease_seconds`,
а свободные (аренда истекла или её не было) забирает через
`select ... for update skip locked` — так два процесса не возьмут один
аккаунт одновременно. Процесс пропал — аренда истекла — аккаунт подхватывает
соседний и поднимает сессию из базы.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, or_, select, update
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


async def heartbeat(
    db: AsyncSession, worker_id: str, hostname: str | None = None, capacity: int | None = None
) -> None:
    """Отметка процесса и продление аренды его аккаунтов.

    Если строки процесса в `gateway_workers` нет (её убрали уборкой или восстановлением
    базы), она создаётся заново: иначе живой процесс считался бы мёртвым — «живых нет»
    ломает справедливую долю аккаунтов и показывается руководителю как «шлюз не запущен».
    """
    now = datetime.now(UTC)
    lease_until = now + timedelta(seconds=settings.gateway_lease_seconds)
    if hostname is not None:
        stmt = pg_insert(GatewayWorker).values(
            id=worker_id,
            hostname=hostname,
            capacity=capacity or settings.gateway_capacity,
            heartbeat_at=now,
            started_at=now,
        )
        await db.execute(
            stmt.on_conflict_do_update(
                index_elements=[GatewayWorker.id], set_={"heartbeat_at": now}
            )
        )
    else:
        await db.execute(
            update(GatewayWorker).where(GatewayWorker.id == worker_id).values(heartbeat_at=now)
        )
    await db.execute(
        update(TelegramAccount)
        .where(TelegramAccount.worker_id == worker_id, TelegramAccount.deleted_at.is_(None))
        .values(lease_until=lease_until)
    )
    await db.commit()


def fair_limit(total_accounts: int, live_workers: int, rank: int = 0) -> int:
    """Справедливая доля: сколько аккаунтов этому процессу держать, чтобы они
    разошлись по всем живым процессам поровну.

    Делим нацело, а остаток (`total % live`) раздаём первым по рангу: 16 аккаунтов
    на 3 процесса — это 6 + 5 + 5, а не 16 + 0 + 0. Ранг — место процесса в списке
    живых, упорядоченном по id, поэтому все процессы, не договариваясь, приходят к
    одному и тому же разбиению: сумма долей всегда равна числу аккаунтов.

    Без этого первый же процесс, пришедший за арендой, забирал всё до своей
    вместимости (25), и при 10–16 аккаунтах остальные ядра простаивали: подгрузка
    истории всех аккаунтов шла на одном. Вместимость осталась потолком безопасности,
    а работу делит эта доля.
    """
    live = max(1, live_workers)
    base, extra = divmod(max(0, total_accounts), live)
    return base + (1 if rank < extra else 0)


async def live_worker_ids(db: AsyncSession) -> list[str]:
    """Процессы шлюза, отметившиеся живыми за срок аренды, по возрастанию id."""
    fresh_after = datetime.now(UTC) - timedelta(seconds=settings.gateway_lease_seconds)
    rows = await db.execute(
        select(GatewayWorker.id)
        .where(GatewayWorker.heartbeat_at > fresh_after)
        .order_by(GatewayWorker.id)
    )
    return [row[0] for row in rows.all()]


async def live_workers(db: AsyncSession) -> int:
    """Сколько процессов шлюза живы."""
    return len(await live_worker_ids(db))


async def fair_share(db: AsyncSession, worker_id: str) -> int:
    """`fair_limit` по текущим данным базы: все рабочие аккаунты и все живые процессы."""
    total = await db.scalar(
        select(func.count())
        .select_from(TelegramAccount)
        .where(TelegramAccount.is_active.is_(True), TelegramAccount.deleted_at.is_(None))
    )
    alive = await live_worker_ids(db)
    # Процесса нет в списке (только что записался, отметка ещё не прошла) — он
    # последний по рангу: взять свою долю успеет и на следующем заходе аренды.
    rank = alive.index(worker_id) if worker_id in alive else len(alive)
    return fair_limit(int(total or 0), max(1, len(alive)), rank)


async def _held_count(db: AsyncSession, worker_id: str) -> int:
    rows = await db.execute(
        select(TelegramAccount.id).where(
            TelegramAccount.worker_id == worker_id,
            TelegramAccount.is_active.is_(True),
            TelegramAccount.deleted_at.is_(None),
        )
    )
    return len(rows.all())


async def claim_accounts(
    db: AsyncSession, worker_id: str, capacity: int, batch: int | None = None
) -> list[int]:
    """Забрать свободные аккаунты сверх уже удержанных, но не больше вместимости
    и не больше справедливой доли (`fair_share`): так аккаунты расходятся по
    всем живым процессам, а не оседают на первом пришедшем."""
    limit = min(capacity, await fair_share(db, worker_id))
    remaining = limit - await _held_count(db, worker_id)
    if batch is not None:
        # Пачками: десять аккаунтов, подключённых разом, — и нагрузка на память, и
        # десять входов в Telegram за секунды (D-21 требует вводить постепенно).
        remaining = min(remaining, batch)
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
    """При остановке процесса — освободить аккаунты сразу, не дожидаясь протухания аренды.

    И снять с себя «живость»: остановленный процесс не должен ещё полминуты
    считаться живым — от числа живых зависит справедливая доля, и после быстрого
    перезапуска контейнера старые строки занижали бы её для новых процессов.
    """
    await db.execute(
        update(TelegramAccount)
        .where(TelegramAccount.worker_id == worker_id)
        .values(worker_id=None, lease_until=None)
    )
    await db.execute(
        update(GatewayWorker)
        .where(GatewayWorker.id == worker_id)
        .values(heartbeat_at=datetime(1970, 1, 1, tzinfo=UTC))
    )
    await db.commit()
