"""Как аккаунты раскладываются по процессам шлюза (ручная проверка, не CI).

Имитирует N рабочих процессов, которые одновременно приходят за аккаунтами,
и печатает, сколько получил каждый. Работает с настоящей базой: живой шлюз на
это время нужно заморозить (`docker pause`), иначе он тоже будет брать аккаунты.
"""

import asyncio
import sys

from sqlalchemy import text

from app.core.config import settings
from app.core.db import SessionLocal
from app.gateway import lease


async def main(workers: int, accounts: int) -> None:
    async with SessionLocal() as db:
        existing = (await db.execute(text("select id from telegram_accounts where deleted_at is null"))).scalars().all()
        for i in range(accounts):
            await db.execute(
                text(
                    "insert into telegram_accounts (title, phone, funnel_stage, api_id, api_hash_enc, status, is_active, created_at, updated_at) "
                    "values (:t, :p, 'diagnostic', 1, 'x', 'connected', true, now(), now())"
                ),
                {"t": f"QA-распределение {i}", "p": f"+7000000{i:04d}"},
            )
        await db.commit()
        await db.execute(text("update telegram_accounts set worker_id=null, lease_until=null where deleted_at is null"))
        await db.commit()
    ids = [f"qa-worker-{i}" for i in range(workers)]
    async with SessionLocal() as db:
        for w in ids:
            await lease.register_worker(db, w, "qa", settings.gateway_capacity)
    taken: dict[str, int] = {}
    # Как в жизни: процессы приходят за арендой почти одновременно, но по очереди.
    for _ in range(3):  # три цикла аренды
        for w in ids:
            async with SessionLocal() as db:
                await lease.heartbeat(db, w)
                got = await lease.claim_accounts(db, w, settings.gateway_capacity)
            taken[w] = taken.get(w, 0) + len(got)
    total = sum(taken.values())
    print(f"аккаунтов всего: {len(existing) + accounts}, раздано: {total}")
    for w in ids:
        print(f"  {w}: {taken.get(w, 0)}")
    async with SessionLocal() as db:
        await db.execute(text("delete from telegram_accounts where title like 'QA-распределение %'"))
        await db.execute(text("delete from gateway_workers where id like 'qa-worker-%'"))
        await db.execute(text("update telegram_accounts set worker_id=null, lease_until=null where deleted_at is null"))
        await db.commit()


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]), int(sys.argv[2])))
