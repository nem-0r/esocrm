"""Сколько процессора уходит на запись одного сообщения при подтяжке истории.

Ручной замер (не CI): прогоняет настоящий `inbound_service.ingest` на синтетических
событиях и убирает за собой. Без сети Telegram — только наша часть работы.
"""

import asyncio
import sys
import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from app.core.db import SessionLocal
from app.models import TelegramAccount
from app.services import inbound_service as svc

BASE_ID = 9_900_000_000


async def main(peers: int, per_peer: int) -> None:
    async with SessionLocal() as db:
        await db.execute(
            text(
                "insert into telegram_accounts (title, phone, funnel_stage, api_id, api_hash_enc, status, is_active, created_at, updated_at) "
                "values ('QA-замер', '+70009990001', 'diagnostic', 1, 'x', 'connected', true, now(), now())"
            )
        )
        await db.commit()
        account = await db.scalar(text("select id from telegram_accounts where title='QA-замер'"))
    try:
        async with SessionLocal() as db:
            acc = await db.get(TelegramAccount, account)
            now = datetime.now(UTC)
            written = 0
            wall0, cpu0 = time.monotonic(), time.process_time()
            for p in range(peers):
                peer = svc.PeerData(tg_user_id=BASE_ID + p, access_hash=1, username=None, first_name=f"Замер{p}", last_name=None)
                for m in range(per_peer):
                    event = svc.InboundMessage(
                        peer=peer, tg_message_id=m + 1, date=now - timedelta(minutes=(per_peer - m) + p),
                        text=f"сообщение {m} " * 5, outgoing=(m % 2 == 0), random_id=None, live=False,
                        media_kind=None, attachments=[], meta=None, is_edit=False, edit_date=None,
                    )
                    if await svc.ingest(db, acc, event) is not None:
                        written += 1
                    if written % 200 == 0:
                        await db.commit()
            await db.commit()
            wall, cpu = time.monotonic() - wall0, time.process_time() - cpu0
        print(f"записано {written} сообщений в {peers} чатов за {wall:.1f} с")
        print(f"  скорость: {written / wall:7.0f} сообщ/с на один процесс (занято ядра: {100 * cpu / wall:.0f}%)")
        print(f"  процессор на одно сообщение: {1000 * cpu / written:.2f} мс")
    finally:
        async with SessionLocal() as db:
            await db.execute(text("delete from messages where conversation_id in (select id from conversations where account_id=:a)"), {"a": account})
            await db.execute(text("delete from conversations where account_id=:a"), {"a": account})
            await db.execute(text("delete from telegram_peers where account_id=:a"), {"a": account})
            await db.execute(text("delete from clients where telegram_id >= :b and telegram_id < :c"), {"b": BASE_ID, "c": BASE_ID + 100000})
            await db.execute(text("delete from telegram_accounts where id=:a"), {"a": account})
            await db.commit()


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]), int(sys.argv[2])))
