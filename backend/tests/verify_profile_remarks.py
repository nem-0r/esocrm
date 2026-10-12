"""Профиль, уведомления, сотрудники: замечания BUG-08, BUG-09, L5, L6 (docs/18).

Что доказываем на живой базе, а не на моках:

- `/notifications` отдаёт `entity_type`/`entity_id` — по ним фронтенд ведёт со строки
  уведомления к сделке или аккаунту (оплаты и `account_*`, в том числе настоящие
  назначения менеджера через `/accounts/{id}/managers`);
- счётчик непрочитанных равен числу непрочитанных в окне списка (31 день): строка
  старше окна не считается ни там, ни там;
- пометка одной строки трогает только её и только свою — чужую не отметить;
- фильтр «Без аккаунта» в списке сотрудников — только активные менеджеры; руководитель,
  отключённый и менеджер с живым аккаунтом в него не попадают, а менеджер, чей
  единственный аккаунт удалён, попадает (как и подпись «· без аккаунта» в карточке);
- подпись «Приём заявок» в профиле говорит правду: флаг влияет, только когда на аккаунте
  ровно один менеджер.

Все данные заведены с уникальной меткой и убираются в конце, даже при падении.
Запуск (под замком — см. docs/18, «Правила»):
    docker compose exec -T api python -m tests.verify_profile_remarks
"""

import asyncio
import sys
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import text

from app.core.db import SessionLocal
from app.models import Notification, NotificationKind
from app.services import inbound_service
from tests.support import ADMIN, BASE, MANAGER, Checks, login

REQUIRED_KEYS = {"id", "kind", "entity_type", "entity_id", "title", "text", "read_at", "created_at"}


async def fetch_all(
    client: httpx.AsyncClient, *, only_unread: bool = False
) -> list[dict[str, Any]]:
    """Все страницы `/notifications` — так, как их листает окно в интерфейсе."""
    items: list[dict[str, Any]] = []
    cursor: str | None = None
    for _ in range(200):
        params: dict[str, Any] = {"limit": 100}
        if only_unread:
            params["only_unread"] = True
        if cursor:
            params["cursor"] = cursor
        page = (await client.get(f"{BASE}/notifications", params=params)).json()
        items.extend(page["items"])
        cursor = page.get("next_cursor")
        if not cursor:
            break
    return items


async def unread_count(client: httpx.AsyncClient) -> int:
    return int((await client.get(f"{BASE}/notifications/unread-count")).json()["count"])


async def run() -> int:  # noqa: PLR0915
    c = Checks("Профиль, уведомления, сотрудники (BUG-08, BUG-09, L5, L6)")
    admin = await login(ADMIN)
    manager = await login(MANAGER)
    suffix = uuid.uuid4().hex[:6]
    tag = f"prm{suffix}"
    title_mark = f"PRM-{suffix}"
    user_ids: list[int] = []
    account_ids: list[int] = []

    async def seed(
        user_id: int,
        kind: NotificationKind,
        entity_type: str | None,
        entity_id: int | None,
        *,
        age: timedelta = timedelta(0),
        payload: dict[str, Any] | None = None,
    ) -> int:
        async with SessionLocal() as db:
            row = Notification(
                user_id=user_id,
                kind=kind,
                entity_type=entity_type,
                entity_id=entity_id,
                payload={"tag": tag, **(payload or {})},
                created_at=datetime.now(UTC) - age,
            )
            db.add(row)
            await db.flush()
            row_id = row.id
            await db.commit()
        return int(row_id)

    async def invite(role: str, name: str) -> dict[str, Any]:
        r = await admin.post(
            f"{BASE}/users",
            json={
                "full_name": f"{title_mark} {name}",
                "email": f"{tag}{name}@astra.ru",
                "role": role,
            },
        )
        row = r.json() if r.status_code == 201 else {}
        if row.get("id"):
            user_ids.append(row["id"])
        c.check(f"сотрудник «{name}» заведён", r.status_code == 201, r.text[:160])
        return row

    async def make_account(name: str, digits: int) -> dict[str, Any]:
        r = await admin.post(
            f"{BASE}/accounts",
            json={
                "title": f"{title_mark} {name}",
                "phone": f"+7999{digits:07d}",
                "funnel_stage": "sales",
            },
        )
        row = r.json() if r.status_code == 201 else {}
        if row.get("id"):
            account_ids.append(row["id"])
        c.check(f"аккаунт «{name}» заведён", r.status_code == 201, r.text[:160])
        return row

    try:
        admin_me = (await admin.get(f"{BASE}/auth/me")).json()
        manager_me = (await manager.get(f"{BASE}/auth/me")).json()
        admin_id, manager_id = admin_me["id"], manager_me["id"]

        deals = (await admin.get(f"{BASE}/deals", params={"limit": 1})).json()["items"]
        if not c.check("в базе есть сделка, к которой вести уведомление", len(deals) > 0):
            return c.finish()
        deal_id = deals[0]["id"]

        # ------------------------------------------------------------------
        c.section("BUG-08. Уведомление отдаёт, к какому объекту оно относится")
        base_account = await make_account("база", int(suffix, 16) % 10_000_000)
        acc_id = base_account.get("id")
        if acc_id is None:
            return c.finish()

        s_paid = await seed(
            admin_id, NotificationKind.DEAL_PAID, "deal", deal_id,
            payload={"number": f"DEAL-{deal_id}", "amount": 12345, "client_name": "Клиент"},
        )
        s_mismatch = await seed(
            admin_id, NotificationKind.PAYMENT_MISMATCH, "deal", deal_id,
            payload={"number": f"DEAL-{deal_id}"},
        )
        s_orphan = await seed(
            admin_id, NotificationKind.PAYMENT_ORPHANED, "deal", deal_id,
            payload={"number": f"DEAL-{deal_id}"},
        )
        s_error = await seed(
            admin_id, NotificationKind.ACCOUNT_ERROR, "account", acc_id,
            payload={"account_title": base_account["title"], "reason": "сессия слетела"},
        )
        s_noent = await seed(admin_id, NotificationKind.ACCOUNT_ERROR, None, None)

        listed = {n["id"]: n for n in await fetch_all(admin)}
        for sid, kind, etype, eid in (
            (s_paid, "deal_paid", "deal", deal_id),
            (s_mismatch, "payment_mismatch", "deal", deal_id),
            (s_orphan, "payment_orphaned", "deal", deal_id),
            (s_error, "account_error", "account", acc_id),
        ):
            row = listed.get(sid, {})
            c.check(
                f"{kind}: entity_type={etype}, entity_id={eid}",
                row.get("kind") == kind
                and row.get("entity_type") == etype
                and row.get("entity_id") == eid,
                row,
            )
        c.check(
            "у каждого уведомления полный набор полей",
            all(set(n) >= REQUIRED_KEYS for n in listed.values()),
            [sorted(REQUIRED_KEYS - set(n)) for n in listed.values() if REQUIRED_KEYS - set(n)],
        )
        c.check(
            "уведомление без объекта отдаёт null (в интерфейсе строка не кликабельна)",
            listed.get(s_noent, {}).get("entity_type") is None
            and listed.get(s_noent, {}).get("entity_id") is None,
            listed.get(s_noent),
        )
        c.check(
            "заголовок и текст собраны на сервере",
            all(listed[i]["title"] and listed[i]["text"] for i in (s_paid, s_mismatch, s_error)),
        )
        c.check(
            "ссылка из уведомления об оплате открывается (сделка существует)",
            (await admin.get(f"{BASE}/deals/{deal_id}")).status_code == 200,
        )

        real_paid = [
            n for n in listed.values() if n["kind"] == "deal_paid" and n["id"] != s_paid
        ]
        broken = []
        for n in real_paid[:5]:
            opened = await admin.get(f"{BASE}/deals/{n['entity_id']}")
            if n["entity_type"] != "deal" or opened.status_code != 200:
                broken.append((n["id"], n["entity_type"], n["entity_id"], opened.status_code))
        if real_paid:
            c.check(
                "настоящие уведомления об оплате указывают на существующие сделки",
                not broken,
                broken,
            )

        # Настоящее назначение менеджера: уведомления создаёт сам сервис аккаунтов.
        assignee = await invite("manager", "assign")
        if assignee.get("id"):
            before_ids = {n["id"] for n in await fetch_all(admin)}
            r = await admin.post(
                f"{BASE}/accounts/{acc_id}/managers",
                json={"user_ids": [assignee["id"]], "notify": True},
            )
            c.check("менеджер назначен на аккаунт", r.status_code == 200, r.text[:160])
            fresh = [n for n in await fetch_all(admin) if n["id"] not in before_ids]
            assigned = [n for n in fresh if n["kind"] == "account_assigned"]
            c.check(
                "account_assigned: руководитель получил уведомление с entity=account/id аккаунта",
                any(n["entity_type"] == "account" and n["entity_id"] == acc_id for n in assigned),
                assigned,
            )
            async with SessionLocal() as db:
                got = (
                    await db.execute(
                        text(
                            "select count(*) from notifications where user_id = :u "
                            "and kind = 'account_assigned' and entity_type = 'account' "
                            "and entity_id = :a"
                        ),
                        {"u": assignee["id"], "a": acc_id},
                    )
                ).scalar_one()
            c.check("и назначенный менеджер получил своё уведомление", int(got) == 1, got)

            before_ids = {n["id"] for n in await fetch_all(admin)}
            r = await admin.post(
                f"{BASE}/accounts/{acc_id}/managers", json={"user_ids": [], "notify": True}
            )
            c.check("менеджер снят с аккаунта", r.status_code == 200, r.text[:160])
            fresh = [n for n in await fetch_all(admin) if n["id"] not in before_ids]
            c.check(
                "account_unassigned: entity=account/id аккаунта",
                any(
                    n["kind"] == "account_unassigned"
                    and n["entity_type"] == "account"
                    and n["entity_id"] == acc_id
                    for n in fresh
                ),
                fresh,
            )

        # ------------------------------------------------------------------
        c.section("Счётчик непрочитанных и окно списка (31 день)")
        before = await unread_count(admin)
        s_old = await seed(
            admin_id, NotificationKind.DEAL_PAID, "deal", deal_id, age=timedelta(days=40)
        )
        s_out = await seed(
            admin_id, NotificationKind.DEAL_PAID, "deal", deal_id,
            age=timedelta(days=31, hours=1),
        )
        c.check(
            "уведомления старше 31 дня не увеличивают счётчик",
            await unread_count(admin) == before,
            f"было {before}, стало {await unread_count(admin)}",
        )
        s_in = await seed(
            admin_id, NotificationKind.DEAL_PAID, "deal", deal_id,
            age=timedelta(days=30, hours=23),
        )
        c.check(
            "уведомление внутри окна (30 дн. 23 ч) счётчик увеличивает на одно",
            await unread_count(admin) == before + 1,
            f"было {before}, стало {await unread_count(admin)}",
        )
        window = await fetch_all(admin)
        window_ids = {n["id"] for n in window}
        c.check("старше окна в списке нет", s_old not in window_ids and s_out not in window_ids)
        c.check("внутри окна в списке есть", s_in in window_ids)
        in_window_unread = [n for n in window if n["read_at"] is None]
        count = await unread_count(admin)
        c.check(
            "счётчик равен числу непрочитанных в списке",
            count == len(in_window_unread),
            f"счётчик {count}, в списке {len(in_window_unread)}",
        )
        only_unread = await fetch_all(admin, only_unread=True)
        c.check(
            "фильтр «только непрочитанные» даёт то же число, что счётчик",
            len(only_unread) == count,
            f"{len(only_unread)} против {count}",
        )

        # ------------------------------------------------------------------
        c.section("Пометка одной строки")
        manager_row = await seed(
            manager_id, NotificationKind.DEAL_PAID, "deal", deal_id,
            payload={"number": f"DEAL-{deal_id}"},
        )
        before = await unread_count(admin)
        r = await admin.post(f"{BASE}/notifications/read", json={"ids": [s_paid]})
        c.check("пометка одной строки: updated = 1", r.json() == {"updated": 1}, r.text)
        after = {n["id"]: n for n in await fetch_all(admin)}
        c.check("строка стала прочитанной", after[s_paid]["read_at"] is not None)
        c.check(
            "соседние строки остались непрочитанными",
            after[s_mismatch]["read_at"] is None and after[s_error]["read_at"] is None,
        )
        c.check(
            "счётчик уменьшился ровно на единицу",
            await unread_count(admin) == before - 1,
            f"было {before}, стало {await unread_count(admin)}",
        )
        r = await admin.post(f"{BASE}/notifications/read", json={"ids": [s_paid]})
        c.check("повторная пометка ничего не меняет: updated = 0", r.json() == {"updated": 0})

        r = await manager.post(f"{BASE}/notifications/read", json={"ids": [s_mismatch]})
        c.check(
            "чужое уведомление отметить нельзя: updated = 0", r.json() == {"updated": 0}, r.text
        )
        still = {n["id"]: n for n in await fetch_all(admin)}
        c.check("чужое осталось непрочитанным", still[s_mismatch]["read_at"] is None)
        r = await admin.post(f"{BASE}/notifications/read", json={"ids": [manager_row]})
        c.check("и руководитель не отмечает уведомления менеджера", r.json() == {"updated": 0})
        mine = {n["id"]: n for n in await fetch_all(manager)}
        c.check(
            "уведомление менеджера у него по-прежнему непрочитано",
            mine.get(manager_row, {}).get("read_at") is None,
            mine.get(manager_row),
        )

        # ------------------------------------------------------------------
        c.section("L5. Фильтр «Без аккаунта» — только активные менеджеры")
        u_free = await invite("manager", "free")  # менеджер без аккаунта
        u_busy = await invite("manager", "busy")  # менеджер с живым аккаунтом
        u_off = await invite("manager", "off")  # отключённый менеджер без аккаунта
        u_boss = await invite("admin", "boss")  # руководитель без аккаунта
        u_lost = await invite("manager", "lost")  # единственный аккаунт удалён
        second = await make_account("второй", (int(suffix, 16) + 1) % 10_000_000)
        third = await make_account("третий", (int(suffix, 16) + 2) % 10_000_000)
        if all(x.get("id") for x in (u_free, u_busy, u_off, u_boss, u_lost, second, third)):
            r = await admin.patch(
                f"{BASE}/users/{u_busy['id']}", json={"account_ids": [second["id"]]}
            )
            c.check("менеджеру назначен аккаунт", r.status_code == 200, r.text[:160])
            r = await admin.patch(f"{BASE}/users/{u_off['id']}", json={"is_active": False})
            c.check("менеджер отключён", r.status_code == 200, r.text[:160])
            r = await admin.patch(
                f"{BASE}/users/{u_lost['id']}", json={"account_ids": [third["id"]]}
            )
            c.check("будущему «потерявшему аккаунт» назначен аккаунт", r.status_code == 200)
            r = await admin.delete(f"{BASE}/accounts/{third['id']}")
            c.check("его аккаунт удалён", r.status_code in (200, 204), r.text[:160])

            filtered = (
                await admin.get(f"{BASE}/users", params={"no_account": True, "limit": 200})
            ).json()["items"]
            ids = {u["id"] for u in filtered}
            c.check("менеджер без аккаунта в фильтре есть", u_free["id"] in ids)
            c.check("менеджер с аккаунтом в фильтре нет", u_busy["id"] not in ids)
            c.check("отключённый менеджер в фильтре не показан", u_off["id"] not in ids)
            c.check("руководитель в фильтре не показан", u_boss["id"] not in ids)
            c.check(
                "руководитель из демо-данных в фильтре не показан",
                all(u["role"] == "manager" for u in filtered),
                [u["email"] for u in filtered if u["role"] != "manager"],
            )
            c.check(
                "менеджер, чей единственный аккаунт удалён, в фильтре есть",
                u_lost["id"] in ids,
            )

            everyone = (await admin.get(f"{BASE}/users", params={"limit": 200})).json()["items"]
            lost_row = next((u for u in everyone if u["id"] == u_lost["id"]), {})
            c.check(
                "в карточке у него нет аккаунтов — подпись «· без аккаунта» совпадает с фильтром",
                lost_row.get("accounts") == [],
                lost_row.get("accounts"),
            )
            # Правило подписи в интерфейсе (isWithoutAccount) и фильтр на сервере — одно и то же.
            wrong = [
                u["email"]
                for u in everyone
                if (u["id"] in ids)
                != (u["role"] == "manager" and u["is_active"] and not u["accounts"])
            ]
            c.check("правило подписи и фильтр совпадают на всех сотрудниках", not wrong, wrong)

        # ------------------------------------------------------------------
        c.section("L6. Подпись «Приём заявок» опирается на реальное поведение")
        solo = await invite("manager", "solo")
        duo = await invite("manager", "duo")
        if solo.get("id") and duo.get("id"):
            r = await admin.post(
                f"{BASE}/accounts/{acc_id}/managers",
                json={"user_ids": [solo["id"]], "notify": False},
            )
            c.check("на аккаунте один менеджер", r.status_code == 200, r.text[:160])

            async def set_flag(user_id: int, value: bool) -> None:
                async with SessionLocal() as db:
                    await db.execute(
                        text("update users set accepting_leads = :v where id = :u"),
                        {"v": value, "u": user_id},
                    )
                    await db.commit()

            async def sole() -> int | None:
                async with SessionLocal() as db:
                    return await inbound_service._sole_manager_id(db, acc_id)

            await set_flag(solo["id"], True)
            c.check(
                "единственный менеджер, приём включён: новый диалог закрепляется за ним",
                await sole() == solo["id"],
            )
            await set_flag(solo["id"], False)
            c.check(
                "единственный менеджер, приём выключен: диалог остаётся без ответственного",
                await sole() is None,
            )
            r = await admin.post(
                f"{BASE}/accounts/{acc_id}/managers",
                json={"user_ids": [solo["id"], duo["id"]], "notify": False},
            )
            c.check("на аккаунте двое", r.status_code == 200, r.text[:160])
            for flag in (True, False):
                await set_flag(solo["id"], flag)
                await set_flag(duo["id"], flag)
                c.check(
                    f"двое на аккаунте, приём {'включён' if flag else 'выключен'}: "
                    "флаг ничего не меняет, диалог ничей",
                    await sole() is None,
                )
    finally:
        async with SessionLocal() as db:
            await db.execute(
                text(
                    "delete from notifications where payload->>'tag' = :tag "
                    "or payload->>'account_title' like :mark"
                ),
                {"tag": tag, "mark": f"{title_mark}%"},
            )
            if user_ids:
                await db.execute(
                    text("delete from users where id = any(:ids)"), {"ids": user_ids}
                )
            if account_ids:
                await db.execute(
                    text("delete from telegram_accounts where id = any(:ids)"),
                    {"ids": account_ids},
                )
            await db.commit()
        await admin.aclose()
        await manager.aclose()

    return c.finish()


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
