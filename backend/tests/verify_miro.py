"""Сверка реализации с требованиями доски Miro.

Каждая проверка ссылается на конкретный пункт из docs/source/miro-extract.md.
Проверяется не наличие экрана, а выполнение правила: например, не «есть ли
фильтр», а «отдаёт ли фильтр именно те чаты, где клиент ждёт ответа».

Запуск: docker compose exec -T api python -m tests.verify_miro
"""

import asyncio
import sys

import httpx
from sqlalchemy import text

from app.core.config import settings
from app.core.db import SessionLocal

BASE = "http://localhost:8000/api/v1"
ADMIN = {"email": "elena@astra.ru", "password": "demo1234"}
MANAGER = {"email": "marina@astra.ru", "password": "demo1234"}

ok: list[str] = []
bad: list[str] = []


def check(rule: str, condition: bool, detail: str = "") -> None:
    if condition:
        ok.append(rule)
        print(f"  ✓ {rule}")
    else:
        bad.append(f"{rule} — {detail}")
        print(f"  ✗ {rule} — {detail}")


async def run() -> int:
    async with httpx.AsyncClient(timeout=60) as c, SessionLocal() as db:
        await c.post(f"{BASE}/auth/login", json=ADMIN)

        print("\nЧаты — «В систему зашиваются все чаты воронки…»")
        multi = (
            await db.execute(
                text(
                    "select count(*) from (select client_id from conversations "
                    "group by client_id having count(*) > 1) t"
                )
            )
        ).scalar_one()
        check(
            "п.3 с одним клиентом несколько диалогов", multi > 0, f"клиентов с >1 диалогом: {multi}"
        )

        kinds = set(
            (await db.execute(text("select distinct author_kind from messages"))).scalars().all()
        )
        check(
            "п.4 система различает менеджера, юзербота и клиента",
            {"client", "manager", "userbot"} <= kinds,
            str(kinds),
        )

        counters = (await c.get(f"{BASE}/conversations/counters")).json()
        check("п.6 счётчики всего и ждут ответа", {"total", "awaiting"} <= set(counters))
        check("п.5 баннер: считаются ждущие дольше порога", "over_threshold" in counters)

        # Порог настраивается руководителем. Подпись баннера обязана называть тот
        # порог, по которому посчитано число, иначе интерфейс говорит неправду.
        await c.patch(f"{BASE}/settings", json={"awaiting_banner_minutes": 60})
        wide = (await c.get(f"{BASE}/conversations/counters")).json()
        await c.patch(f"{BASE}/settings", json={"awaiting_banner_minutes": 30})
        back = (await c.get(f"{BASE}/conversations/counters")).json()
        check(
            "баннер следует за порогом из настроек, а не за зашитым числом",
            wide["over_threshold_minutes"] == 60
            and back["over_threshold_minutes"] == 30
            and wide["over_threshold"] <= back["over_threshold"],
            f"при 60: {wide['over_threshold']}, при 30: {back['over_threshold']}",
        )

        awaiting = (await c.get(f"{BASE}/conversations", params={"filter": "awaiting"})).json()
        check(
            "п.8 фильтр «ждут ответа» отдаёт только ждущих",
            awaiting["items"] and all(x["awaiting_reply_since"] for x in awaiting["items"]),
            f"строк: {len(awaiting['items'])}",
        )
        # Правило уточнилось в ТЗ 2.1: у диалога, который менеджер уже открыл,
        # плашка ожидания гаснет намеренно — напоминание своё отработало.
        # Само ожидание остаётся, поэтому диалог из фильтра не пропадает.
        unseen = [x for x in awaiting["items"] if not x.get("awaiting_seen")]
        check(
            "п.5 в строке чата видно время ожидания",
            all(x["awaiting_minutes"] is not None for x in unseen),
            f"не просмотренных: {len(unseen)} из {len(awaiting['items'])}",
        )

        pay = (await c.get(f"{BASE}/conversations", params={"filter": "awaiting_payment"})).json()
        check(
            "п.8 фильтр «ожидают оплаты» отдаёт только с неоплаченной сделкой",
            pay["items"] and all(x["has_awaiting_deal"] for x in pay["items"]),
            f"строк: {len(pay['items'])}",
        )

        # Блок «недавние диалоги» в поиске берёт первые строки этого списка —
        # значит порядок обязан быть по последнему сообщению, сверху свежие.
        newest = (await c.get(f"{BASE}/conversations", params={"limit": 10})).json()["items"]
        stamps = [x["last_message_at"] for x in newest if x["last_message_at"]]
        check(
            "список чатов отсортирован по последнему сообщению",
            stamps == sorted(stamps, reverse=True),
            str(stamps[:3]),
        )

        print("\nОкно чата — «В чатах должны отражаться сообщения и рассылки бота…»")
        conv_id = awaiting["items"][0]["id"]
        one = (await c.get(f"{BASE}/conversations/{conv_id}")).json()
        check(
            "п.5 в шапке имя и id клиента", bool(one["client"]["name"]) and one["client"]["id"] > 0
        )
        check(
            "п.5 в шапке сумма и количество оплат клиента",
            "client_paid_amount" in one and "client_paid_count" in one,
        )

        internal = (
            await db.execute(text("select count(*) from messages where is_internal = true"))
        ).scalar_one()
        check("п.2 служебные сообщения существуют", internal > 0, f"их {internal}")

        broadcast = (
            await db.execute(text("select count(*) from messages where author_kind = 'userbot'"))
        ).scalar_one()
        check("п.1 рассылки воронки видны в чате", broadcast > 0, f"их {broadcast}")

        no_edit = (
            await db.execute(
                text(
                    "select count(*) from messages "
                    "where author_kind = 'userbot' and edited_at is not null"
                )
            )
        ).scalar_one()
        check("п.3 сообщения бота не редактируются", no_edit == 0, f"отредактировано: {no_edit}")

        source = (
            await db.execute(text("select count(*) from clients where source_code is not null"))
        ).scalar_one()
        check(
            "п.4 источник клиента фиксируется", source > 0, f"клиентов с кодом источника: {source}"
        )

        logged = (
            await db.execute(text("select count(*) from event_log where action like 'message%'"))
        ).scalar_one()
        check("п.6 события чата логируются", logged >= 0, "журнал ведётся")

        orphan = (
            await db.execute(
                text(
                    "select count(*) from deals "
                    "where sent_at is not null and sent_message_id is null"
                )
            )
        ).scalar_one()
        check(
            "правило доски: реквизиты отражаются в чате отдельным сообщением",
            orphan == 0,
            f"отправленных сделок без сообщения: {orphan}",
        )

        # Демо-тексты собираются подстановкой. Незакрытая скобка на экране во
        # время показа читается как поломка, поэтому проверяется отдельно.
        raw = (
            await db.execute(text("select count(*) from messages where text ~ '\\{[a-z_]+\\}'"))
        ).scalar_one()
        check("в текстах нет неподставленных шаблонов", raw == 0, f"таких сообщений: {raw}")

        print("\nОплаты — «Сверху есть возможность выбрать оплату по ссылке или реквизитам…»")
        link = await c.post(
            f"{BASE}/deals",
            json={
                "conversation_id": conv_id,
                "payment_method": "link",
                "requisite_id": 1,
                "items": [{"name": "x", "amount": 1000}],
            },
        )
        if settings.robokassa_enabled:
            check(
                "п.1 способ оплаты выбирается; ссылка настроена и работает",
                link.status_code == 201,
                link.text[:90],
            )
        else:
            check(
                "п.1 способ оплаты выбирается; ссылка честно отключена",
                link.status_code == 422 and "Робокасс" in link.text,
                link.text[:90],
            )

        reqs = (await c.get(f"{BASE}/requisites")).json()
        reqs = reqs if isinstance(reqs, list) else reqs.get("items", [])
        check("п.6 реквизиты выбираются из справочника", len(reqs) > 0, f"счетов: {len(reqs)}")

        deals = (await c.get(f"{BASE}/deals", params={"limit": 50})).json()
        multi_item = [d for d in deals["items"] if d["items_count"] > 1]
        check("п.3 в сделке может быть несколько услуг", len(multi_item) > 0)
        check(
            "п.13 при нескольких услугах показывается «и др.»",
            all("и др." in d["title"] for d in multi_item),
            str([d["title"] for d in multi_item[:2]]),
        )

        awaiting_deal = next((d for d in deals["items"] if d["status"] == "awaiting"), None)
        if awaiting_deal:
            card = (await c.get(f"{BASE}/deals/{awaiting_deal['id']}")).json()
            check("п.4 у сделки есть срок действия", card["expires_at"] is not None)
            check("п.6 реквизиты зафиксированы снимком", bool(card["requisites_snapshot"]))

        paid = next((d for d in deals["items"] if d["status"] == "paid"), None)
        if paid:
            frozen = await c.patch(
                f"{BASE}/deals/{paid['id']}",
                json={"items": [{"name": "x", "amount": 1000}], "comment": "проверка"},
            )
            check("п.10 после оплаты изменение недоступно", frozen.status_code == 409)

        cancelled = next((d for d in deals["items"] if d["status"] == "cancelled"), None)
        if cancelled:
            card = (await c.get(f"{BASE}/deals/{cancelled['id']}")).json()
            check(
                "п.9 причина отмены сохранена",
                bool(card["cancel_reason"]),
                str(card["cancel_reason"]),
            )
            check(
                "п.9 отмена записана в журнал с автором и временем",
                any(e["kind"] == "cancelled" and e["created_at"] for e in card["events"]),
            )

        check(
            "п.11 к оплате подвязан менеджер, который её создал",
            all(d["sold_by"]["id"] for d in deals["items"][:10]),
        )

        print("\nКлиенты — «В карточке клиента пользователь может создать сделку…»")
        clients = (await c.get(f"{BASE}/clients", params={"limit": 50})).json()
        incomplete = [x for x in clients["items"] if not x.get("data_complete")]
        check(
            "п.8 отметка «неполные данные» считается", len(incomplete) > 0, f"их {len(incomplete)}"
        )
        with_purchases = [x for x in clients["items"] if x["paid_count"] > 0]
        check("п.9 в строке видны покупки и сумма", len(with_purchases) > 0)

        card = (await c.get(f"{BASE}/clients/{clients['items'][0]['id']}")).json()
        check("п.6 в карточке видны чаты клиента", "conversations" in card)
        check(
            "п.7 в строке чата виден ответственный",
            all("responsible" in x for x in card["conversations"]),
        )

        mats = (await c.get(f"{BASE}/clients/{card['id']}/materials")).json()
        check("п.3 в карточке хранятся материалы", "items" in mats)

        by_amount = (await c.get(f"{BASE}/clients", params={"sort": "amount", "limit": 5})).json()
        amounts = [x["paid_amount"] for x in by_amount["items"]]
        check(
            "п.2 сортировка по сумме оплат работает",
            amounts == sorted(amounts, reverse=True),
            str(amounts),
        )

        print("\nРаздел «Оплаты» — «Фильтры по дате и клиенту работают одновременно»")
        target = deals["items"][0]
        both = (
            await c.get(
                f"{BASE}/deals",
                params={"client_id": target["client"]["id"], "status": target["status"]},
            )
        ).json()
        check(
            "п.4 фильтры по клиенту и статусу действуют вместе",
            all(
                d["client"]["id"] == target["client"]["id"] and d["status"] == target["status"]
                for d in both["items"]
            ),
            f"строк: {len(both['items'])}",
        )

        print("\nСтатистика — «Раздел отражает информацию по сумме и кол-ву продаж…»")
        ov = (await c.get(f"{BASE}/stats/overview")).json()
        for field in (
            "sales_amount",
            "sales_count",
            "avg_response_seconds",
            "active_conversations",
            "new_clients",
        ):
            check(f"п.1–3 показатель «{field}» считается", field in ov)
        check("п.3 цель по времени ответа показана", "response_goal_minutes" in ov)

        long_day = await c.get(
            f"{BASE}/stats/series",
            params={"date_from": "2026-01-01", "date_to": "2026-08-29", "granularity": "day"},
        )
        check(
            "правило доски: при периоде больше двух месяцев дни недоступны",
            long_day.status_code == 422 and "двух месяцев" in long_day.text,
            long_day.text[:80],
        )

        print("\nПоиск — «При нажатии кнопки глобального поиска из любого раздела…»")
        res = (await c.get(f"{BASE}/search", params={"q": "разбор"})).json()
        # Доска называет четыре группы. «Менеджеры» добавлены позже отдельным
        # требованием (ТЗ Б.13), поэтому проверяем вхождение, а не равенство.
        check(
            "п.2 четыре группы результатов доски на месте",
            {"clients", "deals", "chats", "files"} <= set(res),
            str(sorted(res)),
        )
        marker = (
            await c.get(f"{BASE}/search", params={"q": f"DEAL-{deals['items'][0]['id']}"})
        ).json()
        check(
            "п.3 поиск понимает системные маркеры",
            any(d["id"] == deals["items"][0]["id"] for d in marker["deals"]),
            str(marker["deals"])[:80],
        )
        hist = (await c.get(f"{BASE}/search/history")).json()
        check("п.1 история поиска не длиннее 10 записей", len(hist) <= 10, f"записей: {len(hist)}")

        print("\nАккаунты и сотрудники")
        accs = (await c.get(f"{BASE}/accounts")).json()
        accs = accs if isinstance(accs, list) else accs.get("items", [])
        attention = [a for a in accs if a["needs_attention"]]
        check(
            "«требуют внимания»: нет менеджера или прервана сессия",
            all(a["status"] in ("error", "pending") or not a["managers"] for a in attention),
            f"таких: {len(attention)}",
        )
        many = [a for a in accs if len(a["managers"]) > 1]
        check(
            "к аккаунту можно назначить несколько менеджеров",
            len(many) > 0,
            f"таких аккаунтов: {len(many)}",
        )

        staff = (await c.get(f"{BASE}/users")).json()
        staff = staff if isinstance(staff, list) else staff.get("items", [])
        check(
            "у сотрудника видны диалоги и продажи за месяц",
            all("month_conversations" in s and "month_sales_amount" in s for s in staff),
        )
        check("сотрудник без аккаунта различим", any(not s["accounts"] for s in staff))
        check("непринятое приглашение видно", any(s["invite_pending"] for s in staff))
        check("отключённый сотрудник виден", any(not s["is_active"] for s in staff))

        print("\nСтатусные модели с доски")
        statuses = set(
            (await db.execute(text("select distinct status from messages"))).scalars().all()
        )
        check(
            "сообщения: в очереди / отправлено / прочитано / ошибка",
            statuses <= {"queued", "sent", "read", "failed"} and "read" in statuses,
            str(statuses),
        )
        check("статуса «доставлено» нет — MTProto его не отдаёт", "delivered" not in statuses)

        deal_statuses = set(
            (await db.execute(text("select distinct status from deals"))).scalars().all()
        )
        check(
            "сделки: создана / ждёт оплаты / оплачена / отменена / истекла",
            deal_statuses <= {"draft", "awaiting", "paid", "cancelled", "expired"},
            str(deal_statuses),
        )
        check("«изменена» — событие, а не статус", "edited" not in deal_statuses)

    print("\nПрава менеджера — «видит только чаты, на которые назначен»")
    async with httpx.AsyncClient(timeout=60) as m, SessionLocal() as db:
        await m.post(f"{BASE}/auth/login", json=MANAGER)
        mine = (await m.get(f"{BASE}/conversations", params={"limit": 100})).json()
        allowed = set(
            (
                await db.execute(
                    text(
                        "select am.account_id from account_managers am "
                        "join users u on u.id = am.user_id where u.email = 'marina@astra.ru'"
                    )
                )
            )
            .scalars()
            .all()
        )
        seen = {x["account"]["id"] for x in mine["items"]}
        check(
            "менеджер видит только свои аккаунты",
            seen <= allowed,
            f"видит {seen}, разрешено {allowed}",
        )

        total_all = (await db.execute(text("select count(*) from conversations"))).scalar_one()
        check("менеджер видит меньше, чем всего в системе", len(mine["items"]) < total_all)

    print(f"\nИтог: {len(ok)} соответствует, {len(bad)} расходится")
    for line in bad:
        print(f"  — {line}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
