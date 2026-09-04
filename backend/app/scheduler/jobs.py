"""Фоновые задачи.

Пока задача одна — перевод просроченных сделок в статус «истекла». Она нужна
не для красоты: на доске «истекла» есть в статусной модели, а в разделе оплат
есть фильтр «Истекло». Без этой задачи фильтр на живых данных не показал бы
ничего никогда — сделка с прошедшим сроком так и висела бы в «ждёт оплаты».

Задача идемпотентна: повторный запуск на тех же данных ничего не меняет,
потому что выбираются только сделки в статусе «ждёт оплаты».
"""

import asyncio
import logging
from datetime import UTC, datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select

from app.core.db import SessionLocal
from app.models import ActorKind, Deal, DealEvent, DealEventKind, DealStatus, User
from app.realtime.events import emit_to_conversation
from app.schemas.deal import detail_payload
from app.services.audit import log_event

log = logging.getLogger("astra.scheduler")

# Раз в минуту: срок сделки считается днями, минутная точность избыточна с запасом,
# но дешева и убирает ощущение «статус меняется когда-то потом».
EXPIRE_EVERY_SECONDS = 60


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


async def _safe_expire() -> None:
    """Ошибка в одном прогоне не должна останавливать планировщик."""
    try:
        await expire_overdue_deals()
    except Exception:
        log.exception("Не удалось перевести просроченные сделки")


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
    scheduler.start()
    # Один прогон сразу, не дожидаясь первого интервала.
    await _safe_expire()
    log.info("Планировщик запущен: истечение сделок каждые %s с", EXPIRE_EVERY_SECONDS)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("истекло:", asyncio.run(expire_overdue_deals()))
