"""Пересылка сообщений между чатами CRM.

- тот же аккаунт Telegram → настоящая пересылка одним запросом (drop_author —
  «скрыть отправителя»), все сообщения пачки уходят вместе;
- другой аккаунт → копия: те же текст и файлы (файлы не копируются физически —
  ссылки на тот же объект хранилища);
- комментарий уходит отдельным сообщением перед пересланными;
- пересылка — это ответ клиенту: снимает ожидание и закрепляет ничейный чат;
- служебные заметки не пересылаются; чужие сообщения и чаты — 404.

Запуск: docker compose exec -T api python -m tests.verify_forward
"""

import asyncio
import sys

from sqlalchemy import select, text

from app.core.db import SessionLocal
from app.models import Conversation, Message, MessageStatus, Outbox
from tests.support import ADMIN, BASE, MANAGER, Checks, login, until

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000100000001080600000"
    "01f15c4890000000a4944415478da6360000002000155ff2ba00000000049454e44ae426082"
)


async def _pick() -> tuple[int, int, int] | None:
    """Источник и два чата-получателя: на том же аккаунте и на другом."""
    async with SessionLocal() as db:
        rows = (
            await db.execute(
                text(
                    """
                    select c.id, c.account_id from conversations c
                    join telegram_accounts a on a.id = c.account_id
                    where a.is_active and a.deleted_at is null
                      and not c.is_blocked_by_client and c.closed_at is null
                    order by c.account_id, c.id
                    """
                )
            )
        ).all()
    by_account: dict[int, list[int]] = {}
    for conv_id, account_id in rows:
        by_account.setdefault(account_id, []).append(conv_id)
    accounts = [acc for acc, convs in by_account.items() if len(convs) >= 2]
    if not accounts or len(by_account) < 2:
        return None
    first = accounts[0]
    other = next(acc for acc in by_account if acc != first)
    return by_account[first][0], by_account[first][1], by_account[other][0]


async def _sent(message_ids: list[int]):  # noqa: ANN202
    async with SessionLocal() as db:
        rows = (await db.execute(select(Message).where(Message.id.in_(message_ids)))).scalars().all()
    if len(rows) != len(message_ids) or any(m.status not in (MessageStatus.SENT, MessageStatus.READ) for m in rows):
        return None
    return rows


async def run() -> int:  # noqa: PLR0915
    c = Checks("Пересылка сообщений")
    picked = await _pick()
    if not c.check("есть чаты на одном и на разных аккаунтах", picked is not None):
        return c.finish()
    source_id, same_id, other_id = picked
    admin = await login(ADMIN)
    manager = await login(MANAGER)
    try:
        # Исходные сообщения: текст и фото, отправленные из CRM, — у них есть номера в Telegram.
        up = (await admin.post(f"{BASE}/files/upload", files={"file": ("карта.png", PNG, "image/png")})).json()
        m1 = (await admin.post(f"{BASE}/conversations/{source_id}/messages", json={"text": "Ваш разбор готов"})).json()
        m2 = (await admin.post(f"{BASE}/conversations/{source_id}/messages", json={"text": "Схема", "uploads": [up]})).json()
        note = (await admin.post(f"{BASE}/conversations/{source_id}/messages", json={"text": "заметка", "is_internal": True})).json()
        ready = await until(lambda: _sent([m1["id"], m2["id"]]), 20)
        c.check("исходные сообщения ушли в Telegram", bool(ready))

        c.section("Тот же аккаунт — настоящая пересылка")
        async with SessionLocal() as db:
            await db.execute(
                text("update conversations set awaiting_reply_since = now() - interval '5 minutes' where id = :id"),
                {"id": same_id},
            )
            await db.commit()
        r = await admin.post(
            f"{BASE}/conversations/{same_id}/forward",
            json={"source_conversation_id": source_id, "message_ids": [m2["id"], m1["id"]], "hide_sender": True, "comment": "Смотрите пример"},
        )
        body = r.json() if r.status_code == 201 else {}
        c.check("пересылка принята", r.status_code == 201, r.text[:200])
        c.check("режим — настоящая пересылка", body.get("mode") == "native", body.get("mode"))
        created = body.get("messages", [])
        c.check("комментарий первым, затем сообщения в порядке ленты", [m.get("text") for m in created] == ["Смотрите пример", "Ваш разбор готов", "Схема"], [m.get("text") for m in created])
        forwarded = created[1:]
        c.check(
            "в CRM видно, откуда переслано",
            all((m.get("meta") or {}).get("forwarded", {}).get("conversation_id") == source_id for m in forwarded),
        )
        c.check("файл виден и в новом сообщении", len(forwarded[1].get("attachments", [])) == 1 if len(forwarded) > 1 else False)
        async with SessionLocal() as db:
            specs = [
                row.payload.get("forward")
                for row in (await db.execute(select(Outbox).where(Outbox.message_id.in_([m["id"] for m in forwarded])))).scalars()
            ]
            conv = await db.get(Conversation, same_id)
        c.check(
            "в очереди — одна пачка с drop_author",
            len(specs) == 2 and all(s and s["drop_author"] for s in specs) and len({s["batch_id"] for s in specs}) == 1,
            specs,
        )
        c.check("пересылка сняла ожидание клиента", conv.awaiting_reply_since is None)
        c.check("и закрепила чат за переславшим", conv.responsible_id is not None)
        done = await until(lambda: _sent([m["id"] for m in created]), 25)
        c.check("шлюз переслал: у всех есть номера в Telegram", bool(done) and all(m.tg_message_id for m in done))

        c.section("Другой аккаунт — копия")
        r = await admin.post(
            f"{BASE}/conversations/{other_id}/forward",
            json={"source_conversation_id": source_id, "message_ids": [m2["id"]], "hide_sender": False},
        )
        body = r.json() if r.status_code == 201 else {}
        c.check("режим — копия", r.status_code == 201 and body.get("mode") == "copy", r.text[:200])
        copied = (body.get("messages") or [{}])[0]
        async with SessionLocal() as db:
            keys = (
                await db.execute(
                    text("select storage_key from attachments where message_id in (:a, :b)"),
                    {"a": m2["id"], "b": copied.get("id", 0)},
                )
            ).scalars().all()
            payload = await db.scalar(select(Outbox.payload).where(Outbox.message_id == copied.get("id", 0)))
        c.check("файл не скопирован физически — тот же объект хранилища", len(keys) == 2 and len(set(keys)) == 1, keys)
        c.check("в очереди обычная отправка с файлом", payload is not None and "forward" not in payload and len(payload.get("attachment_ids", [])) == 1, payload)
        done = await until(lambda: _sent([copied.get("id", 0)]), 25)
        c.check("копия ушла в Telegram", bool(done))

        c.section("Запреты")
        r = await admin.post(f"{BASE}/conversations/{same_id}/forward", json={"source_conversation_id": source_id, "message_ids": [note["id"]]})
        c.check("служебную заметку переслать нельзя", r.status_code == 422, r.status_code)
        r = await admin.post(f"{BASE}/conversations/{same_id}/forward", json={"source_conversation_id": same_id, "message_ids": [m1["id"]]})
        c.check("сообщение из другого чата — 404", r.status_code == 404, r.status_code)
        r = await admin.post(f"{BASE}/conversations/{same_id}/forward", json={"source_conversation_id": source_id, "message_ids": []})
        c.check("пустой список — 422", r.status_code == 422, r.status_code)

        async with SessionLocal() as db:
            hidden = (
                await db.execute(
                    text(
                        """
                        select c.id from conversations c where not exists (
                            select 1 from account_managers am join users u on u.id = am.user_id
                            where u.email = :email and am.account_id = c.account_id
                              and (c.responsible_id is null or c.responsible_id = u.id)
                        ) order by c.id limit 1
                        """
                    ),
                    {"email": MANAGER["email"]},
                )
            ).scalar()
        if hidden:
            r = await manager.post(f"{BASE}/conversations/{hidden}/forward", json={"source_conversation_id": hidden, "message_ids": [1]})
            c.check("менеджер не пересылает в чужой чат (404)", r.status_code == 404, r.status_code)

        async with SessionLocal() as db:
            await db.execute(text("update conversations set is_blocked_by_client = true where id = :id"), {"id": other_id})
            await db.commit()
        r = await admin.post(f"{BASE}/conversations/{other_id}/forward", json={"source_conversation_id": source_id, "message_ids": [m1["id"]]})
        c.check("клиент заблокировал номер — понятный отказ", r.status_code == 422 and "заблокировал" in r.text, r.text[:120])
        async with SessionLocal() as db:
            await db.execute(text("update conversations set is_blocked_by_client = false where id = :id"), {"id": other_id})
            await db.commit()
    finally:
        await admin.aclose()
        await manager.aclose()
    return c.finish()


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
