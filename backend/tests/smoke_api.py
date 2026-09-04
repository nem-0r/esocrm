"""Сквозная проверка API на демо-данных.

Запуск: docker compose exec -T api python -m tests.smoke_api

Проверяет не «отвечает ли сервер», а возвращает ли он осмысленные данные
и соблюдает ли права: менеджер не должен видеть чужое, а чужая запись
обязана отвечать 404, а не 403.
"""

import asyncio
import sys

import httpx

BASE = "http://localhost:8000/api/v1"
ADMIN = {"email": "elena@astra.ru", "password": "demo1234"}
MANAGER = {"email": "marina@astra.ru", "password": "demo1234"}

passed: list[str] = []
failed: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        passed.append(name)
        print(f"  ✓ {name}")
    else:
        failed.append(f"{name}: {detail}")
        print(f"  ✗ {name} — {detail}")


async def login(client: httpx.AsyncClient, creds: dict[str, str]) -> bool:
    response = await client.post(f"{BASE}/auth/login", json=creds)
    return response.status_code == 200


async def run() -> int:
    async with httpx.AsyncClient(timeout=30) as admin:
        print("\nВход руководителя")
        check("вход elena@astra.ru", await login(admin, ADMIN))
        if failed:
            print("\nДальше идти нет смысла — вход не работает.")
            return 1

        me = (await admin.get(f"{BASE}/auth/me")).json()
        check("роль руководителя", me.get("role") == "admin", str(me)[:120])

        print("\nЧаты")
        counters = (await admin.get(f"{BASE}/conversations/counters")).json()
        check("счётчики чатов", counters.get("total", 0) > 0, str(counters)[:120])
        check("есть ждущие ответа", counters.get("awaiting", 0) > 0, str(counters)[:120])

        chats = (await admin.get(f"{BASE}/conversations", params={"limit": 5})).json()
        items = chats.get("items", [])
        check("список чатов не пуст", len(items) > 0)
        if items:
            first = items[0]
            check("в строке чата есть клиент", bool(first.get("client", {}).get("name")))
            conversation_id = first["id"]

            messages = (await admin.get(f"{BASE}/conversations/{conversation_id}/messages")).json()
            check("сообщения загружаются", len(messages.get("items", [])) > 0)

        print("\nКлиенты")
        clients = (await admin.get(f"{BASE}/clients", params={"limit": 5})).json()
        check("список клиентов", len(clients.get("items", [])) > 0)
        if clients.get("items"):
            client_id = clients["items"][0]["id"]
            card = await admin.get(f"{BASE}/clients/{client_id}")
            check("карточка клиента", card.status_code == 200, card.text[:120])

        print("\nОплаты")
        deals = (await admin.get(f"{BASE}/deals", params={"limit": 5})).json()
        check("список оплат", len(deals.get("items", [])) > 0)
        summary = (await admin.get(f"{BASE}/deals/summary")).json()
        check("итоги по оплатам", summary.get("paid_amount", 0) > 0, str(summary)[:120])
        if deals.get("items"):
            deal_id = deals["items"][0]["id"]
            card = (await admin.get(f"{BASE}/deals/{deal_id}")).json()
            check("карточка сделки с журналом", len(card.get("events", [])) > 0)

        print("\nСтатистика и поиск")
        stats = await admin.get(
            f"{BASE}/stats/overview", params={"date_from": "2026-05-01", "date_to": "2026-08-28"}
        )
        check("статистика отвечает", stats.status_code == 200, stats.text[:160])
        if stats.status_code == 200:
            check("сумма продаж посчитана", stats.json().get("sales_amount", 0) > 0)

        series = await admin.get(
            f"{BASE}/stats/series",
            params={"date_from": "2026-08-01", "date_to": "2026-08-28", "granularity": "day"},
        )
        check("ряд для графика", series.status_code == 200, series.text[:160])

        search = await admin.get(f"{BASE}/search", params={"q": "разбор"})
        check("поиск отвечает", search.status_code == 200, search.text[:160])

        print("\nАккаунты и сотрудники")
        accounts = await admin.get(f"{BASE}/accounts")
        check("список аккаунтов", accounts.status_code == 200, accounts.text[:160])
        if accounts.status_code == 200:
            data = accounts.json()
            rows = data if isinstance(data, list) else data.get("items", [])
            check("есть аккаунт «требует внимания»", any(r.get("needs_attention") for r in rows))

        users = await admin.get(f"{BASE}/users")
        check("список сотрудников", users.status_code == 200, users.text[:160])

        settings = await admin.get(f"{BASE}/settings")
        check("настройки читаются", settings.status_code == 200, settings.text[:160])

    async with httpx.AsyncClient(timeout=30) as manager:
        print("\nПрава менеджера")
        check("вход marina@astra.ru", await login(manager, MANAGER))
        mine = (await manager.get(f"{BASE}/conversations", params={"limit": 50})).json()
        my_ids = {row["account"]["id"] for row in mine.get("items", [])}
        check("менеджер видит только свои аккаунты", len(my_ids) <= 2, str(my_ids))

        # Чужая запись обязана отвечать 404, а не 403: 403 подтверждает,
        # что запись существует, и это уже утечка.
        missing = await manager.get(f"{BASE}/clients/999999")
        check("несуществующий клиент → 404", missing.status_code == 404, str(missing.status_code))

        forbidden = await manager.get(f"{BASE}/users")
        check("менеджеру закрыт список сотрудников", forbidden.status_code == 403)

    print(f"\nИтог: {len(passed)} успешно, {len(failed)} с ошибкой")
    for line in failed:
        print(f"  — {line}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
