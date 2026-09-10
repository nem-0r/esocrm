"""Фоновые задачи.

Первая задача — перевод просроченных сделок в статус «истекла». Она нужна
не для красоты: на доске «истекла» есть в статусной модели, а в разделе оплат
есть фильтр «Истекло». Без этой задачи фильтр на живых данных не показал бы
ничего никогда — сделка с прошедшим сроком так и висела бы в «ждёт оплаты».

Задача идемпотентна: повторный запуск на тех же данных ничего не меняет,
потому что выбираются только сделки в статусе «ждёт оплаты».

Вторая — уборка мёртвых строк в `gateway_workers`. Процесс шлюза пишет туда
себя со случайным id при каждом старте (`app/gateway/runner.py`, намеренно
случайным — см. комментарий там) и никогда не удаляет строку сам: ни при
обычной остановке, ни тем более при падении. С супервизором в
`app/gateway/worker.py`, который сам поднимает несколько процессов на
контейнер и переживший падение процесс заменяет новым за секунды, эти
строки накапливаются быстрее, чем раньше, — без уборки таблица росла бы
без предела. Удалять безопасно ориентируясь только на heartbeat_at: живой
процесс обновляет его каждые `gateway_heartbeat_seconds` (по умолчанию 10),
так что молчание намного дольше этого — однозначно мёртвый процесс, а не
временная заминка.
"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import delete, select

from app.core.db import SessionLocal
from app.models import ActorKind, Deal, DealEvent, DealEventKind, DealStatus, GatewayWorker, User
from app.realtime.events import emit_to_conversation
from app.schemas.deal import detail_payload
from app.services.audit import log_event

log = logging.getLogger("astra.scheduler")

# Раз в минуту: срок сделки считается днями, минутная точность избыточна с запасом,
# но дешева и убирает ощущение «статус меняется когда-то потом».
EXPIRE_EVERY_SECONDS = 60

# Раз в час — уборка мёртвых строк шлюза не срочная, в отличие от истечения сделок.
CLEANUP_WORKERS_EVERY_SECONDS = 3600
# Насколько старый heartbeat считать однозначно мёртвым процессом: на два порядка
# больше обычного интервала (10с) — с огромным запасом на паузы GC, перегрузку
# хоста и что угодно ещё, лишь бы не задеть реально живой процесс.
STALE_WORKER_AFTER_SECONDS = 3600


async def expire_overdue_deals() -> int:
    """Перевести просроченные сделки в «истекла». Возвращает число переведённых."""
    now = datetime.now(UTC)
    moved = 0

    async with SessionLocal() as db:
        rows = await db.execute(
            select(Deal).where(
                Deal.status == DealStatus.AWAITING,
                Deal.expires_at.isnot(None),
                Deal.expires_at < now,
            )
        )
        deals = list(rows.scalars().all())
        if not deals:
            return 0

        for deal in deals:
            before = {"status": DealStatus.AWAITING.value}
            deal.status = DealStatus.EXPIRED
            # Автор события — система, а не последний менеджер: в журнале должно
            # быть видно, что срок вышел сам, а не кто-то нажал кнопку.
            db.add(
                DealEvent(
                    deal_id=deal.id,
                    actor_id=None,
                    kind=DealEventKind.EXPIRED,
                    comment="Срок действия сделки истёк",
                )
            )
            await log_event(
                db,
                action="deal.expire",
                entity_type="deal",
                entity_id=deal.id,
                actor=None,
                actor_kind=ActorKind.SYSTEM,
                before=before,
                after={"status": DealStatus.EXPIRED.value},
            )
            moved += 1

        await db.commit()

        # Открытые вкладки должны увидеть смену статуса без перезагрузки.
        for deal in deals:
            await db.refresh(deal)
            sold_by = await db.get(User, deal.sold_by_id)
            events = await db.execute(
                select(DealEvent, User)
                .outerjoin(User, User.id == DealEvent.actor_id)
                .where(DealEvent.deal_id == deal.id)
                .order_by(DealEvent.created_at, DealEvent.id)
            )
            await emit_to_conversation(
                db,
                deal.conversation_id,
                "deal.updated",
                {"deal": detail_payload(deal, sold_by, list(events.all()))},
            )

    log.info("Истекли по сроку: %s сделок", moved)
    return moved


async def cleanup_stale_gateway_workers() -> int:
    """Удалить строки шлюза, чей процесс точно не подаёт признаков жизни.

    Не трогает аренду аккаунтов — та живёт в `telegram_accounts.worker_id`/
    `lease_until` и гаснет сама по протуханию, независимо от этой таблицы.
    Возвращает число удалённых строк.
    """
    cutoff = datetime.now(UTC) - timedelta(seconds=STALE_WORKER_AFTER_SECONDS)
    async with SessionLocal() as db:
        result = await db.execute(delete(GatewayWorker).where(GatewayWorker.heartbeat_at < cutoff))
        await db.commit()
        return result.rowcount or 0


async def _safe_expire() -> None:
    """Ошибка в одном прогоне не должна останавливать планировщик."""
    try:
        await expire_overdue_deals()
    except Exception:
        log.exception("Не удалось перевести просроченные сделки")


async def _safe_cleanup_workers() -> None:
    try:
        removed = await cleanup_stale_gateway_workers()
        if removed:
            log.info("Убрано мёртвых строк шлюза: %s", removed)
    except Exception:
        log.exception("Не удалось убрать мёртвые строки шлюза")


async def start_scheduler() -> None:
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(
        _safe_expire,
        "interval",
        seconds=EXPIRE_EVERY_SECONDS,
        id="expire_overdue_deals",
        # Если процесс стоял, не нужно догонять пропущенные запуски —
        # задача идемпотентна, достаточно одного прогона.
        coalesce=True,
        max_instances=1,
    )
    scheduler.add_job(
        _safe_cleanup_workers,
        "interval",
        seconds=CLEANUP_WORKERS_EVERY_SECONDS,
        id="cleanup_stale_gateway_workers",
        coalesce=True,
        max_instances=1,
    )
    scheduler.start()
    # Один прогон сразу, не дожидаясь первого интервала.
    await _safe_expire()
    await _safe_cleanup_workers()
    log.info("Планировщик запущен: истечение сделок каждые %s с", EXPIRE_EVERY_SECONDS)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("истекло:", asyncio.run(expire_overdue_deals()))
