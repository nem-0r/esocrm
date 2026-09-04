"""Проверка обещания «потеря Redis ничего не ломает».

Запускается при остановленном Redis (см. `make verify-redis`). Redis у нас —
провод для живых обновлений и присутствия, а не хранилище: переписка, сделки и
права лежат в PostgreSQL. Значит при упавшем Redis менеджер обязан продолжать
работать, теряя только мгновенное обновление экрана.

Проверка появилась после того, как выяснилось обратное: событие публиковалось
внутри обработчика запроса, ошибка Redis поднималась наверх, и отправка
сообщения отвечала 500. Перезапуск Redis означал остановку работы компании.
"""

import asyncio
import sys

import httpx

BASE = "http://localhost:8000/api/v1"
ADMIN = {"email": "elena@astra.ru", "password": "demo1234"}

ok: list[str] = []
bad: list[str] = []


def check(title: str, condition: bool, detail: str = "") -> None:
    (ok if condition else bad).append(title)
    print(f"  {'✓' if condition else '✗'} {title}{f' — {detail}' if detail else ''}")


async def run() -> int:
    print("Работа при недоступном Redis\n")
    async with httpx.AsyncClient(base_url=BASE, timeout=30) as c:
        login = await c.post("/auth/login", json=ADMIN)
        check("вход выполняется", login.status_code == 200, str(login.status_code))
        if login.status_code != 200:
            print("\nДальше проверять нечего.")
            return 1

        conversations = await c.get("/conversations", params={"limit": 3})
        check("список чатов открывается", conversations.status_code == 200)
        items = conversations.json().get("items", [])
        check("в списке есть строки", bool(items), f"{len(items)} шт.")
        if not items:
            return 1
        conversation_id = items[0]["id"]

        sent = await c.post(
            f"/conversations/{conversation_id}/messages",
            json={"text": "Проверка работы без Redis"},
        )
        check("сообщение отправляется", sent.status_code == 201, sent.text[:90])

        deal = await c.post(
            "/deals",
            json={
                "conversation_id": conversation_id,
                "payment_method": "requisites",
                "requisite_id": 1,
                "items": [{"name": "Без Redis", "amount": 100000}],
            },
        )
        check("сделка создаётся", deal.status_code == 201, deal.text[:90])
        code = deal.json().get("payment_code") if deal.status_code == 201 else None
        check("код платежа выдан", bool(code), str(code))

        check("счётчики отвечают", (await c.get("/conversations/counters")).status_code == 200)
        check("оплаты открываются", (await c.get("/deals", params={"limit": 3})).status_code == 200)
        check("статистика считается", (await c.get("/stats/overview")).status_code == 200)
        clients = await c.get("/clients", params={"limit": 3})
        check("клиенты открываются", clients.status_code == 200)

        staff = await c.get("/users", params={"limit": 5})
        check("сотрудники открываются", staff.status_code == 200)
        rows = staff.json().get("items", []) if staff.status_code == 200 else []
        check(
            "без Redis все считаются офлайн, а не «онлайн наугад»",
            all(not row["online"] for row in rows),
            str([row["online"] for row in rows]),
        )
        check("поиск отвечает", (await c.get("/search", params={"q": "ма"})).status_code == 200)

    print(f"\nИтог: {len(ok)} выполнено, {len(bad)} не выполнено")
    for line in bad:
        print(f"  — {line}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
