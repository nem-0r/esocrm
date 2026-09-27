"""Очередь исходящих под запретом Telegram (FloodWait).

Telegram отвечает «подождите N секунд» на весь аккаунт, и повтор до срока
продлевает запрет. Проверяется, что шлюз:

- откладывает до срока всю очередь аккаунта, а не одно сообщение, и до срока
  не стучится в Telegram — в том числе с сообщениями, поставленными уже во
  время запрета;
- после срока отправляет всё, и внутри диалога — по порядку.

Запрет изображает демо-шлюз (команда demo_flood): до срока любая отправка
аккаунта получает тот же отказ, что прислал бы настоящий Telegram.

Запуск: docker compose exec -T api python -m tests.verify_outbox
"""

import asyncio
import sys
import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.core import command_bus
from app.core.db import SessionLocal
from app.models import Conversation, Message, Outbox, TelegramAccount
from tests.support import ADMIN, BASE, Checks, login, until

FLOOD_SECONDS = 6


async def _two_chats_of_one_account() -> tuple[int, Conversation, Conversation]:
    async with SessionLocal() as db:
        rows = (
            await db.execute(
                select(Conversation)
                .join(TelegramAccount, TelegramAccount.id == Conversation.account_id)
                .where(
                    TelegramAccount.is_active.is_(True),
                    TelegramAccount.deleted_at.is_(None),
                    Conversation.is_blocked_by_client.is_(False),
                    Conversation.closed_at.is_(None),
                )
                .order_by(Conversation.account_id, Conversation.id)
            )
        ).scalars().all()
    by_account: dict[int, list[Conversation]] = {}
    for conv in rows:
        by_account.setdefault(conv.account_id, []).append(conv)
    account_id, convs = next((a, c) for a, c in by_account.items() if len(c) >= 2)
    return account_id, convs[0], convs[1]


async def _start_flood(account_id: int) -> None:
    """Шлюз мог только что стартовать и ещё не взять аккаунт — ждём его."""
    deadline = time.monotonic() + 60
    while True:
        try:
            await command_bus.call(account_id, "demo_flood", {"seconds": FLOOD_SECONDS}, timeout=10)
            return
        except command_bus.GatewayUnavailable:
            if time.monotonic() > deadline:
                raise
            await asyncio.sleep(1)


async def _outbox(message_id: int) -> Outbox | None:
    async with SessionLocal() as db:
        return await db.scalar(select(Outbox).where(Outbox.message_id == message_id))


async def _refused(message_id: int) -> Outbox | None:
    row = await _outbox(message_id)
    return row if row and row.error_text and "подождать" in row.error_text else None


async def _all_sent(ids: list[int]) -> list[Message] | None:
    async with SessionLocal() as db:
        messages = [await db.get(Message, i) for i in ids]
    if all(m is not None and m.status.value in ("sent", "read") for m in messages):
        return messages
    return None


async def run() -> int:
    c = Checks("Очередь исходящих: запрет Telegram (FloodWait)")
    admin = await login(ADMIN)
    try:
        account_id, first, second = await _two_chats_of_one_account()
        await _start_flood(account_id)
        flood_ends = datetime.now(UTC) + timedelta(seconds=FLOOD_SECONDS)

        ids: list[int] = []
        for conv, text in (
            (first, "Проверка запрета: первое"),
            (first, "Проверка запрета: второе"),
            (second, "Проверка запрета: другой чат"),
        ):
            r = await admin.post(f"{BASE}/conversations/{conv.id}/messages", json={"text": text})
            c.check(f"«{text}» принято", r.status_code == 201, r.text[:200])
            ids.append(r.json().get("id", 0))

        c.section("Пока запрет действует")
        refused = await until(lambda: _refused(ids[0]), 10)
        c.check("первое сообщение получило «подождите» и ждёт срока", bool(refused))
        await asyncio.sleep(2)  # время, за которое шлюз без паузы успел бы взять следующие
        rest = [await _outbox(i) for i in ids[1:]]
        c.check(
            "остальные сообщения аккаунта в Telegram до срока не отправлялись",
            all(o is not None and o.status.value == "pending" and o.error_text is None for o in rest),
            [(o.status.value, o.error_text) for o in rest if o],
        )

        c.section("После срока")
        sent = await until(lambda: _all_sent(ids), FLOOD_SECONDS + 20)
        c.check("все три сообщения отправлены", bool(sent))
        if sent:
            earliest = min(m.sent_at for m in sent if m.sent_at)
            c.check(
                "ни одно не ушло раньше срока",
                earliest >= flood_ends - timedelta(seconds=1),
                f"первое ушло в {earliest:%H:%M:%S}, срок {flood_ends:%H:%M:%S}",
            )
            c.check(
                "в одном диалоге — по порядку",
                (sent[0].tg_message_id or 0) < (sent[1].tg_message_id or 0),
                (sent[0].tg_message_id, sent[1].tg_message_id),
            )
    finally:
        await admin.aclose()
    return c.finish()


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
