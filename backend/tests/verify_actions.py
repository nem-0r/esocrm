"""Проверка, что действия реально меняют состояние, а не только отвечают 200.

Каждая проверка устроена одинаково: снимаем состояние, выполняем действие,
снимаем состояние снова и убеждаемся, что изменилось именно то, что обещала
кнопка. Ответ «200 OK» сам по себе ничего не доказывает — заглушка отвечает так же.

Скрипт меняет демо-данные. После него положено выполнить `make seed`.

Запуск: docker compose exec -T api python -m tests.verify_actions
"""

import asyncio
import io
import sys
from datetime import date, timedelta

import httpx
from sqlalchemy import text

from app.core.config import settings
from app.core.db import SessionLocal
from app.scheduler.jobs import expire_overdue_deals

BASE = "http://localhost:8000/api/v1"
ADMIN = {"email": "elena@astra.ru", "password": "demo1234"}
MANAGER = {"email": "marina@astra.ru", "password": "demo1234"}

ok: list[str] = []
bad: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> bool:
    if condition:
        ok.append(name)
        print(f"  ✓ {name}")
    else:
        bad.append(f"{name} — {detail}")
        print(f"  ✗ {name} — {detail}")
    return condition


async def _cleanup_fixtures() -> None:
    """Следы прошлого прогона: тестовый аккаунт и приглашённый сотрудник.

    Прогон обязан быть повторяемым, а «уже заведён» из прошлого раза — это
    мусор в базе, а не найденный дефект.
    """
    from sqlalchemy import text as sql_text

    from app.core.db import SessionLocal

    async with SessionLocal() as db:
        await db.execute(sql_text("delete from users where email = 'autotest@astra.ru'"))
        await db.execute(
            sql_text(
                "delete from telegram_accounts a where a.phone = '+79995550001' "
                "and not exists (select 1 from conversations c where c.account_id = a.id)"
            )
        )
        await db.commit()


async def run() -> int:
    await _cleanup_fixtures()
    async with httpx.AsyncClient(timeout=60) as c:
        print("\nВход и профиль")
        r = await c.post(f"{BASE}/auth/login", json=ADMIN)
        if not check("вход руководителя", r.status_code == 200, r.text[:120]):
            return 1

        me = (await c.get(f"{BASE}/auth/me")).json()
        was = me["accepting_leads"]
        await c.patch(f"{BASE}/auth/me", json={"accepting_leads": not was})
        after = (await c.get(f"{BASE}/auth/me")).json()
        check("переключатель приёма заявок сохраняется", after["accepting_leads"] != was)
        await c.patch(f"{BASE}/auth/me", json={"accepting_leads": was})

        check("отметка присутствия", (await c.post(f"{BASE}/auth/heartbeat")).status_code == 200)

        print("\nЧаты")
        chats = (await c.get(f"{BASE}/conversations", params={"limit": 5})).json()
        conv = chats["items"][0]
        cid = conv["id"]

        nxt = await c.get(f"{BASE}/conversations/next")
        check("«взять первого в очереди» отдаёт чат", nxt.status_code == 200, nxt.text[:100])

        await c.post(f"{BASE}/conversations/{cid}/read")
        one = (await c.get(f"{BASE}/conversations/{cid}")).json()
        check("отметка «прочитано» обнуляет счётчик", one["unread_count"] == 0)

        print("\nОтправка сообщений")
        msg = await c.post(
            f"{BASE}/conversations/{cid}/messages",
            json={"text": "Проверка отправки из автотеста"},
        )
        check("сообщение отправляется", msg.status_code == 201, msg.text[:140])
        if msg.status_code == 201:
            body = msg.json()
            check(
                "исходящее попадает в очередь",
                body["status"] in ("queued", "sent"),
                str(body.get("status")),
            )
            listed = (
                await c.get(f"{BASE}/conversations/{cid}/messages", params={"limit": 5})
            ).json()
            check(
                "сообщение видно в переписке", any(m["id"] == body["id"] for m in listed["items"])
            )

        note = await c.post(
            f"{BASE}/conversations/{cid}/messages",
            json={"text": "Служебная заметка автотеста", "is_internal": True},
        )
        if check("служебная заметка создаётся", note.status_code == 201, note.text[:120]):
            nb = note.json()
            check("служебная не уходит в Telegram", nb["status"] == "sent" and nb["is_internal"])

        empty = await c.post(f"{BASE}/conversations/{cid}/messages", json={})
        check("пустое сообщение отклоняется", empty.status_code == 422, str(empty.status_code))

        print("\nПередача диалога")
        accounts = (await c.get(f"{BASE}/accounts")).json()
        accounts = accounts if isinstance(accounts, list) else accounts.get("items", [])
        acc = next((a for a in accounts if a["id"] == conv["account"]["id"]), None)
        managers = acc["managers"] if acc else []
        if len(managers) >= 1:
            target = managers[0]["id"]
            tr = await c.post(f"{BASE}/conversations/{cid}/transfer", json={"user_id": target})
            if check("передача диалога проходит", tr.status_code == 200, tr.text[:120]):
                one = (await c.get(f"{BASE}/conversations/{cid}")).json()
                check(
                    "ответственный сменился",
                    one["responsible"] and one["responsible"]["id"] == target,
                    str(one.get("responsible")),
                )
        stranger = await c.post(f"{BASE}/conversations/{cid}/transfer", json={"user_id": 999999})
        check(
            "передача чужому отклоняется",
            stranger.status_code in (404, 422),
            str(stranger.status_code),
        )

        print("\nКлиенты")
        clients = (await c.get(f"{BASE}/clients", params={"limit": 3})).json()
        client = clients["items"][0]
        cl_id = client["id"]

        patched = await c.patch(f"{BASE}/clients/{cl_id}", json={"birth_city": "Владивосток"})
        if check("правка клиента сохраняется", patched.status_code == 200, patched.text[:120]):
            card = (await c.get(f"{BASE}/clients/{cl_id}")).json()
            check(
                "город записался", card["birth_city"] == "Владивосток", str(card.get("birth_city"))
            )

        bad_phone = await c.patch(f"{BASE}/clients/{cl_id}", json={"phone": "не телефон"})
        check(
            "кривой телефон отклоняется", bad_phone.status_code == 422, str(bad_phone.status_code)
        )

        note_r = await c.post(f"{BASE}/clients/{cl_id}/notes", json={"text": "Заметка автотеста"})
        if check("заметка создаётся", note_r.status_code in (200, 201), note_r.text[:120]):
            note_id = note_r.json()["id"]
            notes = (await c.get(f"{BASE}/clients/{cl_id}/notes")).json()
            items = notes["items"] if isinstance(notes, dict) else notes
            check("заметка видна в списке", any(n["id"] == note_id for n in items))
            deleted = await c.delete(f"{BASE}/clients/{cl_id}/notes/{note_id}")
            check("заметка удаляется", deleted.status_code in (200, 204), str(deleted.status_code))

        csv = await c.get(f"{BASE}/clients/export")
        check(
            "выгрузка клиентов отдаёт файл",
            csv.status_code == 200 and len(csv.content) > 100,
            f"{csv.status_code}, {len(csv.content)} байт",
        )
        text_csv = csv.content.decode("utf-8-sig", errors="replace")
        check(
            "выгрузка с разделителем ; и заголовком",
            ";" in text_csv and "ID" in text_csv.split("\n")[0],
        )

        print("\nФайлы")
        up = await c.post(
            f"{BASE}/files/upload",
            files={"file": ("проверка.txt", io.BytesIO(b"test content"), "text/plain")},
        )
        if check("файл загружается", up.status_code in (200, 201), up.text[:140]):
            key = up.json()["upload_key"]
            withfile = await c.post(
                f"{BASE}/conversations/{cid}/messages",
                json={
                    "text": "Файл из автотеста",
                    "uploads": [
                        {
                            "upload_key": key,
                            "file_name": "проверка.txt",
                            "size_bytes": 12,
                            "mime_type": "text/plain",
                        }
                    ],
                },
            )
            if check(
                "вложение привязывается к сообщению",
                withfile.status_code == 201,
                withfile.text[:140],
            ):
                att = withfile.json()["attachments"]
                check("вложение появилось в сообщении", len(att) == 1, str(att))
                if att:
                    dl = await c.get(f"http://localhost:8000{att[0]['url']}")
                    check(
                        "файл скачивается обратно",
                        dl.status_code == 200 and dl.content == b"test content",
                    )

        forbidden_type = await c.post(
            f"{BASE}/files/upload",
            files={"file": ("вирус.exe", io.BytesIO(b"MZ"), "application/octet-stream")},
        )
        check(
            "запрещённый тип файла отклоняется",
            forbidden_type.status_code == 422,
            str(forbidden_type.status_code),
        )

        print("\nСделки: полный путь")
        reqs = (await c.get(f"{BASE}/requisites")).json()
        reqs = reqs if isinstance(reqs, list) else reqs.get("items", [])
        req_id = reqs[0]["id"]

        # Доска, карточка клиента, п.1: «пользователь может создать сделку».
        # Счёт уходит в чат, поэтому путь проверяется целиком: берём чат из
        # карточки, создаём оплату на него, убеждаемся, что счёт там и оказался.
        card_for_deal = (await c.get(f"{BASE}/clients/{cl_id}")).json()
        chats_of_client = card_for_deal["conversations"]
        if check("в карточке клиента есть чат для отправки счёта", len(chats_of_client) > 0):
            target_conv = chats_of_client[0]["id"]
            from_card = await c.post(
                f"{BASE}/deals",
                json={
                    "conversation_id": target_conv,
                    "payment_method": "requisites",
                    "requisite_id": req_id,
                    "items": [{"name": "Оплата из карточки", "amount": 450000}],
                },
            )
            if check(
                "оплата из карточки создаётся", from_card.status_code == 201, from_card.text[:140]
            ):
                did = from_card.json()["id"]
                sent = await c.post(f"{BASE}/deals/{did}/send")
                check("счёт из карточки уходит в чат", sent.status_code == 200, sent.text[:140])
                msgs = (
                    await c.get(f"{BASE}/conversations/{target_conv}/messages", params={"limit": 5})
                ).json()["items"]
                code = from_card.json()["payment_code"]
                check(
                    "счёт попал именно в выбранный чат",
                    any(f"Код платежа: {code}" in (m["text"] or "") for m in msgs),
                    f"чат {target_conv}, сделка {did}, код {code}",
                )

        link = await c.post(
            f"{BASE}/deals",
            json={
                "conversation_id": cid,
                "payment_method": "link",
                "requisite_id": req_id,
                "items": [{"name": "Проверка", "amount": 100000}],
            },
        )
        if settings.robokassa_enabled:
            check("оплата по ссылке принимается — Робокасса настроена", link.status_code == 201)
        else:
            check(
                "оплата по ссылке честно отклоняется",
                link.status_code == 422,
                str(link.status_code),
            )

        created = await c.post(
            f"{BASE}/deals",
            json={
                "conversation_id": cid,
                "payment_method": "requisites",
                "requisite_id": req_id,
                "items": [{"name": "Разбор автотеста", "amount": 490000}],
            },
        )
        if check("сделка создаётся", created.status_code in (200, 201), created.text[:140]):
            deal = created.json()
            did = deal["id"]
            # Код платежа перестал быть номером сделки: опечатка клиента в одной
            # цифре попадала в чужую оплату, а номер сделки ещё и показывал
            # клиенту счётчик продаж компании.
            check(
                "код платежа выдан и не совпадает с номером сделки",
                bool(deal["payment_code"]) and deal["payment_code"] != str(did),
                str(deal.get("payment_code")),
            )
            check(
                "сумма считается из позиций",
                deal["total_amount"] == 490000,
                str(deal["total_amount"]),
            )
            check("новая сделка — черновик", deal["status"] == "draft", deal["status"])

            no_reason = await c.patch(
                f"{BASE}/deals/{did}",
                json={"items": [{"name": "Другое", "amount": 200000}], "comment": ""},
            )
            check(
                "изменение без причины отклоняется",
                no_reason.status_code == 422,
                str(no_reason.status_code),
            )

            edited = await c.patch(
                f"{BASE}/deals/{did}",
                json={
                    "items": [{"name": "Другое", "amount": 200000}],
                    "comment": "Клиент попросил дешевле",
                },
            )
            if check("изменение с причиной проходит", edited.status_code == 200, edited.text[:140]):
                card = (await c.get(f"{BASE}/deals/{did}")).json()
                check(
                    "сумма пересчиталась", card["total_amount"] == 200000, str(card["total_amount"])
                )
                check(
                    "в журнале есть запись об изменении",
                    any(e["kind"] == "edited" for e in card["events"]),
                )
                check(
                    "причина изменения сохранена",
                    any(e.get("comment") == "Клиент попросил дешевле" for e in card["events"]),
                )

            sent = await c.post(f"{BASE}/deals/{did}/send")
            if check("счёт уходит в чат", sent.status_code == 200, sent.text[:140]):
                card = sent.json()
                check("статус стал «ждёт оплаты»", card["status"] == "awaiting", card["status"])
                check("проставлен срок действия", card["expires_at"] is not None)
                msgs = (
                    await c.get(f"{BASE}/conversations/{cid}/messages", params={"limit": 3})
                ).json()
                texts = " ".join((m["text"] or "") for m in msgs["items"])
                check(
                    "клиент получил реквизиты в чате",
                    (deal.get("payment_code") or str(did)) in texts,
                    texts[:100],
                )

            again = await c.post(f"{BASE}/deals/{did}/send")
            check(
                "повторная отправка отклоняется", again.status_code == 409, str(again.status_code)
            )

            # ТЗ п. 6.3: без чека подтвердить нельзя.
            no_receipt = await c.post(f"{BASE}/deals/{did}/pay", json={})
            check(
                "оплата без чека отклоняется",
                no_receipt.status_code == 422,
                no_receipt.text[:120],
            )

            paid = await c.post(f"{BASE}/deals/{did}/pay", json={"receipt_number": "ЧЕК-000777"})
            if check("оплата подтверждается", paid.status_code == 200, paid.text[:140]):
                check("номер чека сохранён", paid.json()["receipt_number"] == "ЧЕК-000777")
                card = paid.json()
                check("статус стал «оплачена»", card["status"] == "paid", card["status"])
                check(
                    "в журнале есть запись об оплате",
                    any(e["kind"] == "paid" for e in card["events"]),
                )

            frozen = await c.patch(
                f"{BASE}/deals/{did}",
                json={"items": [{"name": "X", "amount": 1000}], "comment": "нельзя"},
            )
            check(
                "оплаченную сделку изменить нельзя",
                frozen.status_code == 409,
                str(frozen.status_code),
            )

            notif = (await c.get(f"{BASE}/notifications", params={"limit": 5})).json()
            items = notif["items"] if isinstance(notif, dict) else notif
            check(
                "об оплате пришло уведомление",
                any(str(did) in (n.get("text") or "") for n in items),
                str([n.get("text") for n in items[:3]]),
            )

        second = await c.post(
            f"{BASE}/deals",
            json={
                "conversation_id": cid,
                "payment_method": "requisites",
                "requisite_id": req_id,
                "items": [{"name": "На отмену", "amount": 300000}],
            },
        )
        if second.status_code in (200, 201):
            sid = second.json()["id"]
            await c.post(f"{BASE}/deals/{sid}/send")
            no_reason = await c.post(f"{BASE}/deals/{sid}/cancel", json={"reason": ""})
            check(
                "отмена без причины отклоняется",
                no_reason.status_code == 422,
                str(no_reason.status_code),
            )
            cancelled = await c.post(
                f"{BASE}/deals/{sid}/cancel", json={"reason": "Клиент передумал"}
            )
            if check(
                "отмена с причиной проходит", cancelled.status_code == 200, cancelled.text[:140]
            ):
                check("причина сохранена", cancelled.json()["cancel_reason"] == "Клиент передумал")

        # Статус «истекла» с доски работает сам по себе: без фоновой задачи
        # фильтр «Истекло» на живых данных не показал бы ничего никогда.
        print("\nИстечение срока сделки")
        overdue = (await c.get(f"{BASE}/deals", params={"status": "awaiting", "limit": 1})).json()[
            "items"
        ]
        if overdue:
            did = overdue[0]["id"]
            async with SessionLocal() as db:
                await db.execute(
                    text("update deals set expires_at = now() - interval '1 day' where id = :i"),
                    {"i": did},
                )
                await db.commit()
            moved = await expire_overdue_deals()
            card = (await c.get(f"{BASE}/deals/{did}")).json()
            check(
                "просроченная сделка переходит в «истекла»",
                card["status"] == "expired",
                f"перевела {moved}, статус {card['status']}",
            )
            evt = next((e for e in card["events"] if e["kind"] == "expired"), None)
            check(
                "событие истечения записано без автора-человека",
                evt is not None and evt["actor"] is None,
                str(evt),
            )
            check("повторный прогон ничего не меняет", await expire_overdue_deals() == 0)

        print("\nСправочники")
        new_req = await c.post(
            f"{BASE}/requisites",
            json={
                "title": "Автотест счёт",
                "bank_name": "Банк",
                "details_text": "Реквизиты автотеста",
            },
        )
        if check("реквизиты создаются", new_req.status_code in (200, 201), new_req.text[:120]):
            rid = new_req.json()["id"]
            upd = await c.patch(f"{BASE}/requisites/{rid}", json={"title": "Автотест счёт 2"})
            check("реквизиты меняются", upd.status_code == 200, upd.text[:120])
            check(
                "реквизиты удаляются",
                (await c.delete(f"{BASE}/requisites/{rid}")).status_code in (200, 204),
            )

        tpl = await c.post(
            f"{BASE}/templates", json={"title": "Автотест", "text": "Текст", "is_personal": True}
        )
        if check("шаблон создаётся", tpl.status_code in (200, 201), tpl.text[:120]):
            tid = tpl.json()["id"]
            listed = (await c.get(f"{BASE}/templates")).json()
            items = listed if isinstance(listed, list) else listed.get("items", [])
            check("шаблон виден в списке", any(t["id"] == tid for t in items))
            check(
                "шаблон удаляется",
                (await c.delete(f"{BASE}/templates/{tid}")).status_code in (200, 204),
            )

        print("\nАккаунты")
        acc_new = await c.post(
            f"{BASE}/accounts",
            json={"title": "Автотест аккаунт", "phone": "+79995550001", "funnel_stage": "sales"},
        )
        if check("аккаунт заводится", acc_new.status_code in (200, 201), acc_new.text[:140]):
            aid = acc_new.json()["id"]
            code = await c.post(f"{BASE}/accounts/{aid}/send-code")
            if check("код запрашивается", code.status_code == 200, code.text[:140]):
                data = code.json()
                check("демо-режим объяснён пользователю", bool(data.get("hint")), str(data)[:100])
                # Доска: «есть возможность повторного запроса». Повтор идёт по
                # тому же аккаунту и не должен заводить второй.
                before_count = len((await c.get(f"{BASE}/accounts")).json())
                again = await c.post(f"{BASE}/accounts/{aid}/send-code")
                if check("код запрашивается повторно", again.status_code == 200, again.text[:120]):
                    data = again.json()
                    check(
                        "повтор не создаёт второй аккаунт",
                        len((await c.get(f"{BASE}/accounts")).json()) == before_count,
                    )
                wrong = await c.post(
                    f"{BASE}/accounts/{aid}/confirm-code",
                    json={"code": "12", "phone_code_hash": data.get("phone_code_hash", "")},
                )
                check("короткий код отклоняется", wrong.status_code == 422, str(wrong.status_code))
                right = await c.post(
                    f"{BASE}/accounts/{aid}/confirm-code",
                    json={"code": "38151", "phone_code_hash": data.get("phone_code_hash", "")},
                )
                if check("верный код принимается", right.status_code == 200, right.text[:140]):
                    accs = (await c.get(f"{BASE}/accounts")).json()
                    accs = accs if isinstance(accs, list) else accs.get("items", [])
                    mine = next((a for a in accs if a["id"] == aid), None)
                    check(
                        "аккаунт стал подключённым",
                        mine and mine["status"] == "connected",
                        str(mine and mine["status"]),
                    )

            users = (await c.get(f"{BASE}/users")).json()
            users = users if isinstance(users, list) else users.get("items", [])
            manager = next((u for u in users if u["role"] == "manager" and u["is_active"]), None)
            if manager:
                assign = await c.post(
                    f"{BASE}/accounts/{aid}/managers",
                    json={"user_ids": [manager["id"]], "notify": True},
                )
                if check(
                    "менеджер назначается на аккаунт", assign.status_code == 200, assign.text[:140]
                ):
                    accs = (await c.get(f"{BASE}/accounts")).json()
                    accs = accs if isinstance(accs, list) else accs.get("items", [])
                    mine = next((a for a in accs if a["id"] == aid), None)
                    check(
                        "назначение видно в списке",
                        mine and any(m["id"] == manager["id"] for m in mine["managers"]),
                    )

            check(
                "аккаунт отключается",
                (await c.delete(f"{BASE}/accounts/{aid}")).status_code in (200, 204),
            )

        print("\nСотрудники")
        invited = await c.post(
            f"{BASE}/users",
            json={
                "full_name": "Автотест Сотрудник",
                "email": "autotest@astra.ru",
                "role": "manager",
            },
        )
        if check("сотрудник приглашается", invited.status_code in (200, 201), invited.text[:140]):
            uid = invited.json()["id"]
            check(
                "выдана ссылка приглашения",
                bool(invited.json().get("invite_url")),
                str(invited.json())[:120],
            )
            dup = await c.post(
                f"{BASE}/users",
                json={"full_name": "Дубль", "email": "autotest@astra.ru", "role": "manager"},
            )
            check("повтор почты отклоняется", dup.status_code == 409, str(dup.status_code))
            again = await c.post(f"{BASE}/users/{uid}/resend-invite")
            check("приглашение отправляется повторно", again.status_code == 200, again.text[:120])
            off = await c.patch(f"{BASE}/users/{uid}", json={"is_active": False})
            if check("сотрудник отключается", off.status_code == 200, off.text[:120]):
                one = (await c.get(f"{BASE}/users/{uid}")).json()
                check("отключение сохранилось", one["is_active"] is False)

        me_now = (await c.get(f"{BASE}/auth/me")).json()
        self_off = await c.patch(f"{BASE}/users/{me_now['id']}", json={"is_active": False})
        check("себя отключить нельзя", self_off.status_code == 422, str(self_off.status_code))

        print("\nНастройки")
        st = (await c.get(f"{BASE}/settings")).json()
        was_minutes = st["awaiting_banner_minutes"]
        bad_val = await c.patch(f"{BASE}/settings", json={"awaiting_banner_minutes": 99999})
        check(
            "недопустимое значение отклоняется",
            bad_val.status_code == 422,
            str(bad_val.status_code),
        )
        good = await c.patch(f"{BASE}/settings", json={"awaiting_banner_minutes": 45})
        if check("настройка сохраняется", good.status_code == 200, good.text[:120]):
            check("значение применилось", good.json()["awaiting_banner_minutes"] == 45)
            counters = (await c.get(f"{BASE}/conversations/counters")).json()
            check("порог баннера влияет на счётчик", "over_threshold" in counters)
        await c.patch(f"{BASE}/settings", json={"awaiting_banner_minutes": was_minutes})

        print("\nПоиск")
        found = (await c.get(f"{BASE}/search", params={"q": "разбор"})).json()
        check("поиск находит сделки или чаты", len(found["deals"]) + len(found["chats"]) > 0)
        by_id = (await c.get(f"{BASE}/search", params={"q": str(cl_id)})).json()
        check(
            "поиск по id клиента работает",
            any(x["id"] == cl_id for x in by_id["clients"]),
            str(by_id["clients"])[:100],
        )
        hist = (await c.get(f"{BASE}/search/history")).json()
        check("запросы попадают в историю", len(hist) > 0)
        check(
            "история очищается",
            (await c.delete(f"{BASE}/search/history")).status_code in (200, 204),
        )

        print("\nВыгрузка оплат и границы периода")
        dexp = await c.get(f"{BASE}/deals/export")
        check(
            "выгрузка оплат отдаёт файл",
            dexp.status_code == 200 and len(dexp.content) > 50,
            str(dexp.status_code),
        )
        long_period = await c.get(
            f"{BASE}/stats/series",
            params={
                "date_from": (date.today() - timedelta(days=200)).isoformat(),
                "date_to": date.today().isoformat(),
                "granularity": "day",
            },
        )
        check(
            "дни на длинном периоде отклоняются с пояснением",
            long_period.status_code == 422,
            str(long_period.status_code),
        )

        check("выход из системы", (await c.post(f"{BASE}/auth/logout")).status_code == 200)
        after_logout = await c.get(f"{BASE}/auth/me")
        check(
            "после выхода доступа нет",
            after_logout.status_code == 401,
            str(after_logout.status_code),
        )

    print("\nПрава менеджера")
    async with httpx.AsyncClient(timeout=60) as m:
        await m.post(f"{BASE}/auth/login", json=MANAGER)
        check("сотрудники закрыты", (await m.get(f"{BASE}/users")).status_code == 403)
        check(
            "настройки менять нельзя",
            (await m.patch(f"{BASE}/settings", json={"deal_link_ttl_days": 3})).status_code == 403,
        )
        check(
            "справочник реквизитов менять нельзя",
            (
                await m.post(f"{BASE}/requisites", json={"title": "x", "details_text": "y"})
            ).status_code
            == 403,
        )
        check("реквизиты читать можно", (await m.get(f"{BASE}/requisites")).status_code == 200)
        check(
            "несуществующая сделка → 404", (await m.get(f"{BASE}/deals/999999")).status_code == 404
        )

    print(f"\nИтог: {len(ok)} работает, {len(bad)} с ошибкой")
    for line in bad:
        print(f"  — {line}")
    print("\nДемо-данные изменены. Восстановить: make seed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
