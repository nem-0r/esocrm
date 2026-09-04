"""Проверка контура Telegram: команда из API → шлюз → приём → PostgreSQL.

Проверяем не «эндпойнт ответил 200», а то, ради чего всё написано: сообщение
доходит до базы, повтор не создаёт дубль, ожидание клиента начинает тикать,
история не поднимает шум, ответ менеджера снимает ожидание.

Демо-провайдер отличается от боевого одним — источником события. Всё, что
происходит после источника, здесь одно и то же, поэтому проверка останется
осмысленной и после подключения настоящего аккаунта.
"""

import asyncio
import sys
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from app.core import command_bus
from app.core.db import SessionLocal
from app.models import (
    Client,
    Conversation,
    Direction,
    Message,
    TelegramAccount,
    TelegramPeer,
)

ok: list[str] = []
bad: list[str] = []

# Заведомо не занятый идентификатор: демо-клиенты живут в другом диапазоне.
PROBE_TG_ID = 770_000_101
PROBE_ACCOUNT_TITLE_HINT = "проверка шлюза"


def check(title: str, condition: bool, detail: str = "") -> None:
    (ok if condition else bad).append(title)
    print(f"  {'✓' if condition else '✗'} {title}{f' — {detail}' if detail else ''}")


async def _counts(db, conversation_id: int) -> tuple[int, int]:
    total = await db.scalar(
        select(func.count()).select_from(Message).where(Message.conversation_id == conversation_id)
    )
    conv = await db.get(Conversation, conversation_id)
    return int(total or 0), conv.unread_count


async def _cleanup(db) -> None:
    """Убираем следы прошлого прогона: проверка должна быть повторяемой."""
    conv_ids = [
        row[0]
        for row in (
            await db.execute(
                select(Conversation.id)
                .join(Client, Client.id == Conversation.client_id)
                .where(Client.telegram_id == PROBE_TG_ID)
            )
        ).all()
    ]
    for conv_id in conv_ids:
        await db.execute(Message.__table__.delete().where(Message.conversation_id == conv_id))
        await db.execute(Conversation.__table__.delete().where(Conversation.id == conv_id))
    await db.execute(
        TelegramPeer.__table__.delete().where(TelegramPeer.tg_user_id == PROBE_TG_ID)
    )
    await db.execute(Client.__table__.delete().where(Client.telegram_id == PROBE_TG_ID))
    await db.commit()


async def run() -> int:
    print("Проверка контура Telegram\n")
    async with SessionLocal() as db:
        await _cleanup(db)
        account = await db.scalar(
            select(TelegramAccount)
            .where(TelegramAccount.deleted_at.is_(None), TelegramAccount.is_active.is_(True))
            .order_by(TelegramAccount.id)
        )
        if account is None:
            print("Нет активных аккаунтов — проверять нечего")
            return 1
        account_id = account.id
        print(f"Аккаунт для проверки: {account.title} (id={account_id})\n")

    now = datetime.now(UTC)

    print("Канал команд")
    try:
        answer = await command_bus.call(
            account_id,
            "demo_incoming",
            {
                "tg_user_id": PROBE_TG_ID,
                "access_hash": 424242,
                "username": "probe_client",
                "first_name": "Проверка",
                "tg_message_id": 900001,
                "text": "Здравствуйте, хочу консультацию",
                "date": (now - timedelta(minutes=7)).isoformat(),
            },
            timeout=20,
        )
        check("шлюз принял команду и ответил", bool(answer.get("delivered")), str(answer))
    except command_bus.GatewayUnavailable as exc:
        check("шлюз принял команду и ответил", False, str(exc.message))
        print("\nДальше проверять нечего: шлюз не отвечает.")
        return 1

    await asyncio.sleep(0.6)

    print("\nПриём входящего")
    async with SessionLocal() as db:
        client = await db.scalar(select(Client).where(Client.telegram_id == PROBE_TG_ID))
        check("карточка клиента заведена автоматически", client is not None)
        if client is None:
            return 1
        conv = await db.scalar(
            select(Conversation).where(
                Conversation.client_id == client.id, Conversation.account_id == account_id
            )
        )
        check("диалог создан для пары «клиент + аккаунт»", conv is not None)
        if conv is None:
            return 1
        conversation_id = conv.id
        message = await db.scalar(
            select(Message).where(
                Message.conversation_id == conversation_id, Message.tg_message_id == 900001
            )
        )
        check("сообщение записано в базу", message is not None)
        check(
            "направление входящее, автор — клиент",
            message is not None and message.direction == Direction.IN,
            str(message.direction) if message else "",
        )
        check(
            "текст сохранён целиком",
            message is not None and message.text == "Здравствуйте, хочу консультацию",
        )
        check("счётчик непрочитанных вырос", conv.unread_count >= 1, str(conv.unread_count))
        check(
            "ожидание ответа пошло от времени сообщения, а не от времени записи",
            conv.awaiting_reply_since is not None
            and abs((conv.awaiting_reply_since - (now - timedelta(minutes=7))).total_seconds()) < 5,
            str(conv.awaiting_reply_since),
        )
        peer = await db.scalar(
            select(TelegramPeer).where(
                TelegramPeer.account_id == account_id, TelegramPeer.tg_user_id == PROBE_TG_ID
            )
        )
        check(
            "пропуск access_hash сохранён — ответить можно и после перезапуска",
            peer is not None and peer.access_hash == 424242,
            str(peer.access_hash) if peer else "нет строки",
        )
        check(
            "собеседник связан с карточкой клиента",
            peer is not None and peer.client_id == client.id,
        )
        before_total, before_unread = await _counts(db, conversation_id)

    print("\nПовтор того же события")
    await command_bus.call(
        account_id,
        "demo_incoming",
        {
            "tg_user_id": PROBE_TG_ID,
            "tg_message_id": 900001,
            "text": "Здравствуйте, хочу консультацию",
            "date": (now - timedelta(minutes=7)).isoformat(),
        },
        timeout=20,
    )
    await asyncio.sleep(0.5)
    async with SessionLocal() as db:
        total, unread = await _counts(db, conversation_id)
        check("дубль не создан", total == before_total, f"было {before_total}, стало {total}")
        check("счётчик непрочитанных не задвоился", unread == before_unread, str(unread))

    print("\nВторое сообщение подряд")
    async with SessionLocal() as db:
        conv = await db.get(Conversation, conversation_id)
        awaiting_before = conv.awaiting_reply_since
    await command_bus.call(
        account_id,
        "demo_incoming",
        {
            "tg_user_id": PROBE_TG_ID,
            "tg_message_id": 900002,
            "text": "Ещё вопрос",
            "date": (now - timedelta(minutes=3)).isoformat(),
        },
        timeout=20,
    )
    await asyncio.sleep(0.5)
    async with SessionLocal() as db:
        conv = await db.get(Conversation, conversation_id)
        check(
            "ожидание считается от первого сообщения без ответа",
            conv.awaiting_reply_since == awaiting_before,
            str(conv.awaiting_reply_since),
        )
        check(
            "непрочитанных стало два",
            conv.unread_count == before_unread + 1,
            str(conv.unread_count),
        )

    print("\nОтвет менеджера с телефона мимо CRM")
    await command_bus.call(
        account_id,
        "demo_incoming",
        {
            "tg_user_id": PROBE_TG_ID,
            "tg_message_id": 900003,
            "text": "Здравствуйте! Сейчас подберу время",
            "outgoing": True,
            "date": (now - timedelta(minutes=1)).isoformat(),
        },
        timeout=20,
    )
    await asyncio.sleep(0.5)
    async with SessionLocal() as db:
        conv = await db.get(Conversation, conversation_id)
        answer_row = await db.scalar(
            select(Message).where(
                Message.conversation_id == conversation_id, Message.tg_message_id == 900003
            )
        )
        check("ответ с телефона попал в переписку", answer_row is not None)
        check(
            "он записан исходящим",
            answer_row is not None and answer_row.direction == Direction.OUT,
        )
        check(
            "автор не приписан менеджеру: CRM его не отправляла",
            answer_row is not None and answer_row.author_id is None,
        )
        check("ожидание клиента снято ответом", conv.awaiting_reply_since is None)

    print("\nПодтяжка истории")
    async with SessionLocal() as db:
        conv = await db.get(Conversation, conversation_id)
        unread_before = conv.unread_count
    await command_bus.call(
        account_id,
        "demo_incoming",
        {
            "tg_user_id": PROBE_TG_ID,
            "tg_message_id": 900004,
            "text": "Старое сообщение из истории",
            "date": (now - timedelta(days=40)).isoformat(),
            "live": False,
        },
        timeout=20,
    )
    await asyncio.sleep(0.5)
    async with SessionLocal() as db:
        conv = await db.get(Conversation, conversation_id)
        old = await db.scalar(
            select(Message).where(
                Message.conversation_id == conversation_id, Message.tg_message_id == 900004
            )
        )
        check("старое сообщение сохранено", old is not None)
        check(
            "история не поднимает счётчик непрочитанных",
            conv.unread_count == unread_before,
            str(conv.unread_count),
        )
        check("история не начинает отсчёт ожидания", conv.awaiting_reply_since is None)

    print("\nЧужой аккаунт")
    try:
        await command_bus.call(
            999_999, "demo_incoming", {"tg_user_id": 1, "tg_message_id": 1}, timeout=4
        )
        check("команда по несуществующему аккаунту не выполняется", False, "ответ получен")
    except command_bus.GatewayUnavailable:
        check("команда по несуществующему аккаунту не выполняется", True, "шлюз промолчал")

    async with SessionLocal() as db:
        await _cleanup(db)

    print(f"\nИтог: {len(ok)} выполнено, {len(bad)} не выполнено")
    for line in bad:
        print(f"  — {line}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
