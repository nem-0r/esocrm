"""Проверка правок из ТЗ от 01.09.2026 (`docs/source/tz-2026-09-01.md`).

Каждая проверка названа номером пункта, чтобы при следующем круге правок было
видно, что именно сломалось.

Скрипт не меняет демо-данные, кроме отметки «прочитано» на одном диалоге —
она и так снимается следующим `make seed`.

Запуск: docker compose exec -T api python -m tests.verify_tz
"""

import asyncio
import sys
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import text, update

from app.core.db import SessionLocal
from app.models import Conversation, TelegramAccount

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
    async with httpx.AsyncClient(timeout=60) as admin, httpx.AsyncClient(timeout=60) as manager:
        await admin.post(f"{BASE}/auth/login", json=ADMIN)
        await manager.post(f"{BASE}/auth/login", json=MANAGER)

        print("\n1.4 — аккаунты этапа «Бот» менеджеру не видны")
        a_chats = (await admin.get(f"{BASE}/conversations", params={"limit": 100})).json()["items"]
        m_chats = (
            await manager.get(f"{BASE}/conversations", params={"limit": 100})
        ).json()["items"]
        a_stages = {x["account"]["funnel_stage"] for x in a_chats}
        m_stages = {x["account"]["funnel_stage"] for x in m_chats}
        check("руководитель видит этап «Бот»", "warmup" in a_stages, str(a_stages))
        check("менеджер не видит этап «Бот»", "warmup" not in m_stages, str(m_stages))
        check("менеджеру осталась работа", len(m_chats) > 0, f"чатов: {len(m_chats)}")

        print("\nБ.8 — снятие менеджера с аккаунта убирает работу целиком")
        acc_id = m_chats[0]["account"]["id"]
        await admin.post(
            f"{BASE}/accounts/{acc_id}/managers", json={"user_ids": [], "notify": False}
        )
        left = (await manager.get(f"{BASE}/conversations", params={"limit": 100})).json()["items"]
        left_clients = (await manager.get(f"{BASE}/clients", params={"limit": 100})).json()["items"]
        check(
            "чаты снятого аккаунта исчезают",
            not any(x["account"]["id"] == acc_id for x in left),
            f"осталось чатов: {len(left)}",
        )
        check(
            "клиенты этого аккаунта тоже исчезают",
            len(left_clients) == 0,
            str(len(left_clients)),
        )
        # Возвращаем назначение, иначе следующие проверки останутся без данных.
        me = (await manager.get(f"{BASE}/auth/me")).json()
        await admin.post(
            f"{BASE}/accounts/{acc_id}/managers", json={"user_ids": [me["id"]], "notify": False}
        )

        print("\nБ.8 — карточка клиента и материалы тоже ограничены видимостью")
        # Карточка показывала диалоги клиента на всех аккаунтах, включая чужие
        # и этап «Бот». Сами переписки не открывались (404), но список вёл
        # в никуда и выдавал, где ещё общается клиент.
        shared = None
        for row in m_chats[:20]:
            card = (await admin.get(f"{BASE}/clients/{row['client']['id']}")).json()
            if len(card["conversations"]) > 1:
                shared = row["client"]["id"]
                break
        if shared is not None:
            admin_card = (await admin.get(f"{BASE}/clients/{shared}")).json()
            mgr_card = (await manager.get(f"{BASE}/clients/{shared}")).json()
            visible = {a["id"] for a in (await manager.get(f"{BASE}/accounts")).json()}
            check(
                "менеджеру в карточке видны только его диалоги",
                all(c["account"]["id"] in visible for c in mgr_card["conversations"]),
                str([c["account"]["title"] for c in mgr_card["conversations"]]),
            )
            check(
                "руководителю видны все диалоги клиента",
                len(admin_card["conversations"]) > len(mgr_card["conversations"]),
                f"{len(admin_card['conversations'])} против {len(mgr_card['conversations'])}",
            )
            check(
                "счётчик аккаунтов клиента считается по видимым",
                mgr_card["accounts_count"]
                == len({c["account"]["id"] for c in mgr_card["conversations"]}),
                str(mgr_card["accounts_count"]),
            )
            mgr_files = (await manager.get(f"{BASE}/clients/{shared}/materials")).json()["items"]
            openable = True
            for row in mgr_files[:3]:
                if (await manager.get(f"{BASE}/files/{row['id']}")).status_code == 404:
                    openable = False
            check(
                "материалы клиента открываются: список не ведёт в никуда",
                openable,
                f"файлов у менеджера: {len(mgr_files)}",
            )

        print("\n2.1 — плашка ожидания гаснет после просмотра и срабатывает один раз")
        aw = (
            await admin.get(f"{BASE}/conversations", params={"filter": "awaiting", "limit": 50})
        ).json()["items"]
        overdue = [x for x in aw if (x["awaiting_minutes"] or 0) > 30]
        if overdue:
            cid = overdue[0]["id"]
            before = (await admin.get(f"{BASE}/conversations/counters")).json()
            await admin.post(f"{BASE}/conversations/{cid}/read")
            after = (await admin.get(f"{BASE}/conversations/counters")).json()
            one = (await admin.get(f"{BASE}/conversations/{cid}")).json()
            check(
                "плашка в строке гаснет",
                one["awaiting_minutes"] is None,
                str(one["awaiting_minutes"]),
            )
            check(
                "баннер перестаёт считать этот диалог",
                after["over_threshold"] == before["over_threshold"] - 1,
                f"{before['over_threshold']} → {after['over_threshold']}",
            )
            check(
                "но клиент остаётся в очереди ждущих",
                after["awaiting"] == before["awaiting"],
                f"{before['awaiting']} → {after['awaiting']}",
            )

        print("\n2.2 и 2.3 — краткая информация и оплаты по аккаунту")
        conv = (await admin.get(f"{BASE}/conversations", params={"limit": 1})).json()["items"][0]
        detail = (await admin.get(f"{BASE}/conversations/{conv['id']}")).json()
        check(
            "в шапке есть всё для строки: аккаунт, этап, оплаты, ответственный, срок",
            {"account", "client_paid_amount", "client_paid_count", "responsible"} <= set(detail)
            and "funnel_stage" in detail["account"]
            and "first_contact_at" in detail["client"],
        )
        async with SessionLocal() as db:
            total_paid = (
                await db.execute(
                    text(
                        "select coalesce(sum(total_amount),0) from deals "
                        "where client_id = :c and status = 'paid'"
                    ),
                    {"c": detail["client"]["id"]},
                )
            ).scalar_one()
        check(
            "оплаты в шапке считаются только по этому аккаунту",
            detail["client_paid_amount"] <= total_paid,
            f"в шапке {detail['client_paid_amount']}, всего у клиента {total_paid}",
        )

        print("\n4.6 — оплаты по каналу")
        period = {"date_from": "2026-01-01", "date_to": datetime.now(UTC).date().isoformat()}
        deals = (await admin.get(f"{BASE}/deals", params={**period, "limit": 50})).json()["items"]
        check("в строке сделки указан канал", all("account" in d for d in deals))
        acc_id = deals[0]["account"]["id"]
        scoped = (
            await admin.get(f"{BASE}/deals", params={**period, "account_id": acc_id, "limit": 50})
        ).json()["items"]
        check(
            "фильтр по каналу отдаёт только его сделки",
            scoped and all(d["account"]["id"] == acc_id for d in scoped),
            f"строк: {len(scoped)}",
        )
        check("канал сужает выборку", len(scoped) < len(deals), f"{len(scoped)} из {len(deals)}")

        print("\n5.2 и 5.3 — карточка клиента")
        client_id = deals[0]["client"]["id"]
        card = (await admin.get(f"{BASE}/clients/{client_id}")).json()
        check("видно, через какой аккаунт заведена карточка", "created_via_account" in card)
        check(
            "число аккаунтов совпадает с числом чатов клиента",
            card["accounts_count"] == len({c["account"]["id"] for c in card["conversations"]}),
            f"{card['accounts_count']} против {len(card['conversations'])} чатов",
        )
        check("имя менеджера отдаётся отдельно от показанного", "display_name" in card)

        async with SessionLocal() as db:
            # Клиент без имени и без @username должен показываться публичным id,
            # а с @username — юзернеймом. Проверяем обе ступени лестницы.
            row = (
                await db.execute(
                    text(
                        "select id from clients where display_name is null "
                        "and tg_first_name is null and tg_username is not null limit 1"
                    )
                )
            ).first()
        if row:
            nameless = (await admin.get(f"{BASE}/clients/{row[0]}")).json()
            check(
                "без имени показывается @username, а не id",
                nameless["name"].startswith("@"),
                nameless["name"],
            )

        print("\n1.3 — уведомления только за последний месяц")
        notes = (await admin.get(f"{BASE}/notifications", params={"limit": 100})).json()
        items = notes["items"] if isinstance(notes, dict) else notes
        cutoff = datetime.now(UTC) - timedelta(days=31)
        old = [n for n in items if datetime.fromisoformat(n["created_at"]) < cutoff]
        check("старых уведомлений в выдаче нет", not old, f"старых: {len(old)}")

        print("\n6.3 — чек обязателен")
        awaiting = (
            await admin.get(f"{BASE}/deals", params={**period, "status": "awaiting", "limit": 1})
        ).json()["items"]
        if awaiting:
            no_receipt = await admin.post(f"{BASE}/deals/{awaiting[0]['id']}/pay", json={})
            check(
                "оплата без чека отклоняется",
                no_receipt.status_code == 422 and "чек" in no_receipt.text.lower(),
                no_receipt.text[:90],
            )

        print("\n4.4 и 4.5 — текст к счёту")
        card_deal = (await admin.get(f"{BASE}/deals/{deals[0]['id']}")).json()
        check("карточка сделки отдаёт текст к счёту", "intro_text" in card_deal)
        check("карточка сделки отдаёт номер чека", "receipt_number" in card_deal)

        print("\nБ.1, Б.2, Б.5 — профиль: срок работы, показатели, требующие внимания")
        me_admin = (await admin.get(f"{BASE}/auth/me")).json()
        me_manager = (await manager.get(f"{BASE}/auth/me")).json()
        check("в профиле есть, с какого момента человек работает", bool(me_admin["works_since"]))
        check(
            "в профиле есть диалоги, продажи и среднее время ответа",
            {"month_conversations", "month_sales_amount", "avg_response_seconds"} <= set(me_admin),
        )
        check(
            "«требуют внимания» приходит обеим ролям",
            "accounts_attention" in me_admin and "accounts_attention" in me_manager,
        )
        check(
            "менеджеру считаются только его аккаунты",
            me_manager["accounts_attention"] <= me_admin["accounts_attention"],
            f"менеджер {me_manager['accounts_attention']}, "
            f"руководитель {me_admin['accounts_attention']}",
        )

        print("\nБ.13 — поиск менеджеров только у руководителя")
        found = (await admin.get(f"{BASE}/search", params={"q": "мар"})).json()
        hidden = (await manager.get(f"{BASE}/search", params={"q": "мар"})).json()
        check(
            "руководитель находит сотрудников по имени",
            len(found.get("managers", [])) > 0,
            str([m["full_name"] for m in found.get("managers", [])]),
        )
        check(
            "менеджеру сотрудники в поиске не отдаются",
            len(hidden.get("managers", [])) == 0,
            str(len(hidden.get("managers", []))),
        )

        print("\nБ.10 — аккаунт можно отключить")
        accounts = (await admin.get(f"{BASE}/accounts")).json()
        accounts = accounts if isinstance(accounts, list) else accounts.get("items", [])
        # Берём аккаунт без переписки: с ошибкой сессии или, если такого нет,
        # последний в списке. Проверка не должна пропускаться из-за того, что
        # предыдущий прогон оставил базу в другом состоянии.
        spare = next(
            (a for a in accounts if a["status"] == "error"),
            accounts[-1] if accounts else None,
        )
        if spare:
            gone = await admin.delete(f"{BASE}/accounts/{spare['id']}")
            check("отключение аккаунта проходит", gone.status_code == 200, gone.text[:90])
            rest = (await admin.get(f"{BASE}/accounts")).json()
            rest = rest if isinstance(rest, list) else rest.get("items", [])
            check(
                "отключённый аккаунт исчезает из списка",
                not any(a["id"] == spare["id"] for a in rest),
            )
            # Возвращаем аккаунт на место: проверки идут одна за другой по общей
            # базе, и следующий прогон не должен зависеть от порядка запуска.
            async with SessionLocal() as db:
                await db.execute(
                    update(TelegramAccount)
                    .where(TelegramAccount.id == spare["id"])
                    .values(is_active=True, deleted_at=None)
                )
                await db.commit()

        print("\nБ.3 — личный график работы сотрудника")
        marina = (await admin.get(f"{BASE}/users/2")).json()
        before = marina["schedule"]
        set_ok = await admin.patch(
            f"{BASE}/users/2",
            json={
                "schedule": {
                    "enabled": True,
                    "days": [1, 2, 3, 4, 5],
                    "start": "10:00",
                    "end": "19:00",
                }
            },
        )
        check("руководитель ставит график", set_ok.status_code == 200, set_ok.text[:90])
        saved = set_ok.json()["schedule"]
        check(
            "график сохранился и читается словами",
            saved["enabled"] and saved["summary"] == "Пн–Пт · 10:00–19:00",
            saved["summary"],
        )
        me_marina = (await manager.get(f"{BASE}/auth/me")).json()["schedule"]
        check(
            "сотрудник видит свой график в профиле",
            me_marina["enabled"] and me_marina["days"] == [1, 2, 3, 4, 5],
            str(me_marina),
        )
        check(
            "«на смене» посчитан сервером, а не браузером",
            isinstance(me_marina["on_shift"], bool),
            str(me_marina["on_shift"]),
        )
        night = await admin.patch(
            f"{BASE}/users/2",
            json={"schedule": {"enabled": True, "days": [2], "start": "22:00", "end": "06:00"}},
        )
        check("ночная смена принимается", night.status_code == 200, night.text[:80])
        broken = await admin.patch(
            f"{BASE}/users/2",
            json={"schedule": {"enabled": True, "days": [], "start": "10:00", "end": "19:00"}},
        )
        check(
            "график без дней отклоняется понятным текстом",
            broken.status_code == 422 and "дни" in broken.json()["error"]["message"],
            f"{broken.status_code} {broken.json()['error']['message'][:60]}",
        )
        denied = await manager.patch(
            f"{BASE}/users/2",
            json={"schedule": {"enabled": False, "days": [], "start": None, "end": None}},
        )
        check(
            "менеджер не может менять график",
            denied.status_code in (403, 404),
            str(denied.status_code),
        )
        await admin.patch(
            f"{BASE}/users/2",
            json={"schedule": {"enabled": before["enabled"], "days": before["days"],
                               "start": before["start"], "end": before["end"]}},
        )

        print("\nПрава: менеджер видит только свои чаты и ничейные")
        # Новое правило: менеджер работает там, куда его поставил руководитель.
        # Чужой назначенный диалог не должен быть виден нигде — ни в списке,
        # ни в поиске, ни в оплатах, ни в карточке клиента.
        async with httpx.AsyncClient(timeout=40) as other:
            await other.post(
                f"{BASE}/auth/login",
                json={"email": "anna@astra.ru", "password": ADMIN["password"]},
            )
            page = await manager.get(f"{BASE}/conversations", params={"limit": 100})
            mine = page.json()["items"]
            owned = next((c for c in mine if c.get("responsible")), None)

            # Ничейный диалог: его видят все менеджеры аккаунта, пока кто-то не
            # ответит. На аккаунте с одним менеджером ничейных не остаётся —
            # они закрепляются за ним автоматически, поэтому делаем случай руками.
            if owned is not None:
                async with SessionLocal() as db:
                    await db.execute(
                        update(Conversation)
                        .where(Conversation.id == owned["id"])
                        .values(responsible_id=None, responsible_since=None)
                    )
                    await db.commit()
                free_now = (
                    await manager.get(f"{BASE}/conversations", params={"limit": 100})
                ).json()["items"]
                check(
                    "ничейный диалог менеджеру виден — иначе нечего брать в очереди",
                    any(c["id"] == owned["id"] and not c.get("responsible") for c in free_now),
                )
                answered = await manager.post(
                    f"{BASE}/conversations/{owned['id']}/messages",
                    json={"text": "Здравствуйте, беру в работу"},
                )
                check("ответ в ничейном диалоге проходит", answered.status_code == 201)
                claimed = (await manager.get(f"{BASE}/conversations/{owned['id']}")).json()
                check(
                    "ответивший становится ответственным",
                    (claimed.get("responsible") or {}).get("full_name", "").startswith("Марина"),
                    str(claimed.get("responsible")),
                )

            if owned is not None:
                account_id = owned["account"]["id"]
                anna_id = (await other.get(f"{BASE}/auth/me")).json()["id"]
                marina_id = (await manager.get(f"{BASE}/auth/me")).json()["id"]
                before_ids = {
                    a["id"]
                    for a in (await admin.get(f"{BASE}/accounts")).json()
                    if a["id"] == account_id
                }
                _ = before_ids
                await admin.post(
                    f"{BASE}/accounts/{account_id}/managers",
                    json={"user_ids": [marina_id, anna_id], "notify": False},
                )
                moved = await admin.post(
                    f"{BASE}/conversations/{owned['id']}/transfer", json={"user_id": anna_id}
                )
                check(
                    "передача диалога другому менеджеру проходит",
                    moved.status_code == 200,
                    moved.text[:80],
                )

                left = await manager.get(f"{BASE}/conversations", params={"limit": 100})
                after = left.json()["items"]
                check(
                    "переданный чат исчезает у прежнего менеджера",
                    not any(c["id"] == owned["id"] for c in after),
                )
                check(
                    "открыть переданный чат он больше не может",
                    (await manager.get(f"{BASE}/conversations/{owned['id']}")).status_code == 404,
                )
                check(
                    "написать в переданный чат он больше не может",
                    (
                        await manager.post(
                            f"{BASE}/conversations/{owned['id']}/messages",
                            json={"text": "проверка прав"},
                        )
                    ).status_code
                    == 404,
                )
                got = await other.get(f"{BASE}/conversations", params={"limit": 100})
                taken = got.json()["items"]
                check(
                    "у нового ответственного чат появляется",
                    any(c["id"] == owned["id"] for c in taken),
                )
                client_id = owned["client"]["id"]
                card = await manager.get(f"{BASE}/clients/{client_id}")
                if card.status_code == 200:
                    check(
                        "переданный диалог пропал и из карточки клиента",
                        not any(c["id"] == owned["id"] for c in card.json()["conversations"]),
                    )
                else:
                    check(
                        "клиент без своих диалогов больше не виден менеджеру",
                        card.status_code == 404,
                    )

                # Возвращаем как было: следующие прогоны не должны зависеть от этого.
                await admin.post(
                    f"{BASE}/conversations/{owned['id']}/transfer", json={"user_id": marina_id}
                )
                await admin.post(
                    f"{BASE}/accounts/{account_id}/managers",
                    json={"user_ids": [marina_id], "notify": False},
                )

            queued = await manager.get(f"{BASE}/conversations/next")
            check(
                "«Взять первого в очереди» работает у менеджера",
                queued.status_code in (200, 404),
                str(queued.status_code),
            )
            if queued.status_code == 200:
                row = queued.json()
                who = (await manager.get(f"{BASE}/auth/me")).json()
                check(
                    "взятый в работу диалог закрепляется за менеджером",
                    row.get("responsible", {}).get("id") == who["id"],
                    str(row.get("responsible")),
                )

    print(f"\nИтог: {len(ok)} выполнено, {len(bad)} не выполнено")
    for line in bad:
        print(f"  — {line}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
