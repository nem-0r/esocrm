"""Что происходит с чатами и сообщениями, когда аккаунт слетел и его переподключили.

Ручная проверка на демо-стенде: симулирует «Сессия недействительна», затем
проходит то же, что менеджер в интерфейсе: «Переподключить» → код → подтвердить.
"""

import asyncio
import sys

from sqlalchemy import text

from app.core.db import SessionLocal
from tests.support import ADMIN, BASE, Checks, login, until

ACCOUNT = 3


async def counts() -> dict[str, int]:
    async with SessionLocal() as db:
        row = (
            await db.execute(
                text(
                    "select (select count(*) from conversations where account_id=:a),"
                    " (select count(*) from messages m join conversations c on c.id=m.conversation_id where c.account_id=:a),"
                    " (select count(*) from deals d join conversations c on c.id=d.conversation_id where c.account_id=:a),"
                    " (select count(*) from clients)"
                ),
                {"a": ACCOUNT},
            )
        ).one()
    return {"чатов": row[0], "сообщений": row[1], "сделок": row[2], "клиентов": row[3]}


async def main() -> int:
    c = Checks("Переподключение аккаунта после потери сессии")
    admin = await login(ADMIN)
    before = await counts()
    print("до:", before)
    async with SessionLocal() as db:
        conv = (await db.execute(text("select id from conversations where account_id=:a and closed_at is null order by id limit 1"), {"a": ACCOUNT})).scalar()
    try:
        c.section("1. Сессия слетела")
        async with SessionLocal() as db:
            await db.execute(text("update telegram_accounts set status='error', status_reason='Сессия недействительна — нужен повторный вход' where id=:a"), {"a": ACCOUNT})
            await db.commit()
        row = next(a for a in (await admin.get(f"{BASE}/accounts")).json() if a["id"] == ACCOUNT)
        c.check("в списке аккаунтов статус «ошибка» с причиной", row["status"] == "error" and "Сессия" in (row.get("status_reason") or ""), row)
        c.check("чаты на месте (список чатов открывается)", (await admin.get(f"{BASE}/conversations/{conv}")).status_code == 200)

        c.section("2. Менеджер пишет, пока аккаунт отключён")
        r = await admin.post(f"{BASE}/conversations/{conv}/messages", json={"text": "написано во время простоя"})
        c.check("сообщение принято в очередь (не теряется)", r.status_code == 201, r.text[:100])
        mid = r.json()["id"]
        await asyncio.sleep(6)
        st = next(m for m in (await admin.get(f"{BASE}/conversations/{conv}/messages", params={"limit": 5})).json()["items"] if m["id"] == mid)
        print(f"  ℹ статус сообщения за время простоя: {st['status']} · {st.get('error_text') or ''}")
        c.check("сообщение не потеряно: в очереди или с понятной ошибкой", st["status"] in ("queued", "failed"), st["status"])

        c.section("3. «Переподключить»: код → подтверждение")
        sc = await admin.post(f"{BASE}/accounts/{ACCOUNT}/send-code")
        c.check("код запрошен", sc.status_code == 200, sc.text[:100])
        cc = await admin.post(f"{BASE}/accounts/{ACCOUNT}/confirm-code", json={"code": "12345", "phone_code_hash": sc.json()["phone_code_hash"]})
        c.check("код принят", cc.status_code == 200 and not cc.json().get("needs_password"), cc.text[:150])
        row = next(a for a in (await admin.get(f"{BASE}/accounts")).json() if a["id"] == ACCOUNT)
        c.check("статус «подключён», причина очищена", row["status"] == "connected" and not row.get("status_reason"), row)

        c.section("4. Данные после переподключения")
        after = await counts()
        print("после:", after)
        c.check("чатов не стало меньше", after["чатов"] >= before["чатов"], (before, after))
        c.check("сообщения все на месте", after["сообщений"] >= before["сообщений"], (before, after))
        c.check("сделки и клиенты на месте", after["сделок"] == before["сделок"] and after["клиентов"] == before["клиентов"], (before, after))
        c.check("чат открывается, история видна", len((await admin.get(f"{BASE}/conversations/{conv}/messages", params={"limit": 50})).json()["items"]) >= 3)

        c.section("5. Новые сообщения идут")
        r = await admin.post(f"{BASE}/conversations/{conv}/messages", json={"text": "после переподключения"})
        mid2 = r.json()["id"]

        async def sent():  # noqa: ANN202
            items = (await admin.get(f"{BASE}/conversations/{conv}/messages", params={"limit": 10})).json()["items"]
            row = next((m for m in items if m["id"] == mid2), None)
            return row if row and row["status"] in ("sent", "read") else None

        c.check("новое сообщение отправлено", bool(await until(sent, 15)))

        async def old_sent():  # noqa: ANN202
            items = (await admin.get(f"{BASE}/conversations/{conv}/messages", params={"limit": 20})).json()["items"]
            row = next((m for m in items if m["id"] == mid), None)
            return row if row and row["status"] in ("sent", "read") else row

        old = await until(lambda: old_sent(), 15)
        print(f"  ℹ сообщение, написанное во время простоя, после переподключения: {old['status'] if old else '?'}")
    finally:
        async with SessionLocal() as db:
            await db.execute(text("update telegram_accounts set status='connected', status_reason=null where id=:a"), {"a": ACCOUNT})
            await db.commit()
        await admin.aclose()
    return c.finish()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
