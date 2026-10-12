"""Статус последнего сообщения в списке чатов и аккаунт без связи (D-36, D-37, BUG-01).

Проверяется на живом стенде, на собственных данных (аккаунт, клиенты, чаты с
префиксом VCS-, всё убирается в конце; аккаунт неактивен — шлюз его не берёт):

1. Строка списка чатов отдаёт id, направление и статус последнего сообщения:
   queued / sent / read / failed у исходящего; у входящего, у служебной заметки
   и у пустого чата статуса нет. Лишних запросов на строку нет.
2. Аккаунт в `error`: отправка из CRM отклоняется сразу ответом 409 (сообщение не
   создано, в очередь ничего не встало), повтор неудачного — тоже 409, пересылка
   — 409; служебная заметка (в Telegram не уходит) проходит. Строка и карточка
   чата несут статус и причину аккаунта.
3. Уведомление `account_error`: руководителям и менеджерам аккаунта — по одному на
   переход в `error`; повторная запись `error` его не плодит; чужие менеджеры его не
   получают; связь вернули и потеряли снова — второе уведомление. Событие
   `notification.new` уходит после коммита (к его приходу строка уже в базе).
   Тот же отчёт шлюза повторно ничего не пишет. О потере связи у аккаунта этапа «Бот»
   узнают только руководители. Причину обрыва отдают в API только руководителю.
4. Связь вернули: отправка снова идёт, сообщение — `queued`, превью его показывает.
   Состояния `disconnected` и `pending` отклоняют отправку так же, как `error`.

Запуск: docker compose exec -T api python -m tests.verify_chat_status
"""

import asyncio
import contextlib
import random
import sys
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import event, text

from app.core import redis_bus
from app.core.db import SessionLocal, engine
from app.gateway import handlers
from app.models import (
    AccountManager,
    AccountStatus,
    AuthorKind,
    Client,
    Conversation,
    Direction,
    FunnelStage,
    Message,
    MessageKind,
    MessageStatus,
    Outbox,
    OutboxStatus,
    TelegramAccount,
    User,
)
from app.services import conversation_service
from tests.support import ADMIN, BASE, MANAGER, Checks, login

NO_LINK = "Аккаунт без связи — сообщение не отправлено. Сообщите руководителю"
NO_LINK_ADMIN = (
    "Аккаунт без связи — сообщение не отправлено. "
    "Переподключите аккаунт в разделе «Профиль → Аккаунты»"
)


async def _scalar(sql: str, **params):  # noqa: ANN202
    async with SessionLocal() as db:
        return (await db.execute(text(sql), params)).scalar_one()


async def _rows(sql: str, **params):  # noqa: ANN202
    async with SessionLocal() as db:
        return (await db.execute(text(sql), params)).all()


async def _set_message(message_id: int, status: MessageStatus) -> None:
    async with SessionLocal() as db:
        await db.execute(
            text("update messages set status = :s where id = :id"),
            {"s": status.value, "id": message_id},
        )
        await db.commit()


async def _set_account_status(account_id: int, status: AccountStatus) -> None:
    async with SessionLocal() as db:
        await db.execute(
            text("update telegram_accounts set status = :s where id = :id"),
            {"s": status.value, "id": account_id},
        )
        await db.commit()


async def _notifications(account_id: int) -> dict[int, int]:
    rows = await _rows(
        "select user_id, count(*) from notifications "
        "where kind = 'account_error' and entity_type = 'account' and entity_id = :a "
        "group by user_id",
        a=account_id,
    )
    return dict(rows)


async def run() -> int:  # noqa: PLR0915, PLR0912
    c = Checks("Статус последнего сообщения и аккаунт без связи")
    admin = await login(ADMIN)
    manager = await login(MANAGER)
    suffix = uuid.uuid4().hex[:6]
    base_id = random.randint(8_000_000_000, 8_900_000_000)  # noqa: S311
    account_id = 0
    extra_accounts: list[int] = []  # доп. аккаунты (этап «Бот»), которые надо убрать в конце
    client_ids: list[int] = []
    conv_ids: list[int] = []

    try:
        # ------------------------------------------------------------ данные
        async with SessionLocal() as db:
            marina = (
                await db.execute(text("select id from users where email = :e"), {"e": MANAGER["email"]})
            ).scalar_one()
            account = TelegramAccount(
                title=f"VCS-{suffix}",
                phone=f"+7000{random.randint(1_000_000, 9_999_999)}",  # noqa: S311
                funnel_stage=FunnelStage.SALES,
                api_id=1,
                api_hash_enc=b"x",
                status=AccountStatus.CONNECTED,
                # Неактивный аккаунт шлюз не арендует и очередь его не разбирает.
                is_active=False,
            )
            db.add(account)
            await db.flush()
            account_id = account.id
            db.add(AccountManager(account_id=account_id, user_id=marina))

            now = datetime.now(UTC)
            names = ("main", "incoming", "note", "empty")
            convs: dict[str, Conversation] = {}
            for i, name in enumerate(names):
                client = Client(
                    telegram_id=base_id + i,
                    display_name=f"VCS-{suffix}-{name}",
                    tg_first_name=f"VCS-{suffix}-{name}",
                )
                db.add(client)
                await db.flush()
                client_ids.append(client.id)
                conv = Conversation(
                    client_id=client.id,
                    account_id=account_id,
                    tg_chat_id=base_id + i,
                    responsible_id=marina,
                    last_message_at=now - timedelta(minutes=i),
                )
                db.add(conv)
                await db.flush()
                conv_ids.append(conv.id)
                convs[name] = conv

            def add(conv: str, direction: Direction, status: MessageStatus, text_: str,
                    seconds: int, *, internal: bool = False) -> Message:
                message = Message(
                    conversation_id=convs[conv].id,
                    direction=direction,
                    author_kind=AuthorKind.MANAGER if direction == Direction.OUT else AuthorKind.CLIENT,
                    author_id=marina if direction == Direction.OUT else None,
                    kind=MessageKind.TEXT,
                    text=text_,
                    is_internal=internal,
                    status=status,
                    created_at=now - timedelta(minutes=30) + timedelta(seconds=seconds),
                )
                db.add(message)
                return message

            add("main", Direction.IN, MessageStatus.READ, "Здравствуйте", 1)
            out = add("main", Direction.OUT, MessageStatus.QUEUED, "Ответ менеджера", 2)
            # В чате «incoming» последним пришло входящее — после нашего исходящего.
            add("incoming", Direction.OUT, MessageStatus.READ, "Наш ответ", 1)
            incoming = add("incoming", Direction.IN, MessageStatus.READ, "Спасибо!", 2)
            add("note", Direction.OUT, MessageStatus.SENT, "Заметка для команды", 1, internal=True)
            await db.flush()
            out_id, incoming_id = out.id, incoming.id
            await db.commit()
        main_id, incoming_conv, note_conv, empty_conv = conv_ids

        async def list_rows(client, **extra) -> dict[int, dict]:  # noqa: ANN001
            r = await client.get(
                f"{BASE}/conversations", params={"account_id": account_id, "limit": 50, **extra}
            )
            r.raise_for_status()
            return {row["id"]: row for row in r.json()["items"]}

        # ------------------------------------------------ 1. строка списка
        c.section("1. Строка списка чатов: статус последнего сообщения")
        rows = await list_rows(manager)
        c.check("менеджер видит все четыре чата аккаунта", set(rows) == set(conv_ids), sorted(rows))
        row = rows.get(main_id, {})
        c.check("исходящее в очереди: queued", row.get("last_message_status") == "queued", row)
        c.check("направление исходящее", row.get("last_message_direction") == "out", row)
        c.check("отдан id последнего сообщения", row.get("last_message_id") == out_id, row)
        c.check("превью по-прежнему «Вы: …»", row.get("last_message_preview") == "Вы: Ответ менеджера", row)

        for status in (MessageStatus.SENT, MessageStatus.READ, MessageStatus.FAILED):
            await _set_message(out_id, status)
            row = (await list_rows(manager)).get(main_id, {})
            c.check(f"исходящее {status.value}: строка отдаёт {status.value}",
                    row.get("last_message_status") == status.value, row.get("last_message_status"))

        row = rows.get(incoming_conv, {})
        c.check("последним пришло входящее: направление in", row.get("last_message_direction") == "in", row)
        c.check("у входящего значка статуса нет", row.get("last_message_status") is None, row)
        c.check("и id — входящего, а не прежнего нашего", row.get("last_message_id") == incoming_id, row)

        row = rows.get(note_conv, {})
        c.check("служебная заметка: статуса нет", row.get("last_message_status") is None, row)

        row = rows.get(empty_conv, {})
        c.check("пустой чат: все поля пусты", (row.get("last_message_id"), row.get("last_message_direction"),
                row.get("last_message_status"), row.get("last_message_preview")) == (None, None, None, None), row)

        detail = (await manager.get(f"{BASE}/conversations/{main_id}")).json()
        c.check("карточка чата отдаёт те же поля", detail.get("last_message_status") == "failed"
                and detail.get("last_message_id") == out_id, detail)
        c.check("и связь аккаунта: connected", detail.get("account", {}).get("status") == "connected", detail.get("account"))

        # Без N+1: число запросов не зависит от числа строк на странице.
        counts: list[int] = []
        counter = {"n": 0}

        def on_execute(*_args: object) -> None:
            counter["n"] += 1

        event.listen(engine.sync_engine, "before_cursor_execute", on_execute)
        try:
            async with SessionLocal() as db:
                user = await db.get(User, marina)
                for limit in (1, 4):
                    counter["n"] = 0
                    page = await conversation_service.list_conversations(
                        db, user, account_id=account_id, limit=limit
                    )
                    assert len(page.items) == limit
                    counts.append(counter["n"])
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", on_execute)
        c.check("число запросов не растёт со строками (нет N+1)", counts[0] == counts[1], counts)

        # Для повтора нужна строка очереди, как её оставляет шлюз после неудачи.
        async with SessionLocal() as db:
            db.add(
                Outbox(
                    message_id=out_id,
                    account_id=account_id,
                    conversation_id=main_id,
                    payload={"text": "Ответ менеджера", "attachment_ids": []},
                    status=OutboxStatus.FAILED,
                    attempts=3,
                    error_text="Сессия недействительна — нужен повторный вход",
                )
            )
            await db.execute(
                text("update messages set error_text = 'Сессия недействительна — нужен повторный вход' where id = :i"),
                {"i": out_id},
            )
            await db.commit()

        # ------------------------------------------------ 2. аккаунт в error
        c.section("2. Аккаунт потерял связь: отправка отклоняется сразу")
        admins = {r[0] for r in await _rows(
            "select id from users where role = 'admin' and is_active and deleted_at is null")}
        expected_recipients = admins | {marina}
        others = [r[0] for r in await _rows(
            "select id from users where role = 'manager' and is_active and deleted_at is null and id <> :m",
            m=marina)]

        reason = "Сеанс завершён в Telegram — нужен повторный вход"
        # Событие «пришло уведомление» должно уйти ПОСЛЕ коммита (иначе экран
        # перезапросит счётчик и увидит старое число): слушаем шину так же, как
        # вебсокет, и в момент получения события смотрим в базу.
        arrived: list[tuple[list[int], int, int]] = []

        async def listen() -> None:
            async for message in redis_bus.subscribe():
                note = (message.get("data") or {}).get("notification") or {}
                if message.get("type") == "notification.new" and note.get("entity_id") == account_id:
                    in_db = await _scalar("select count(*) from notifications where id = :i", i=note["id"])
                    arrived.append((message["audience"].get("user_ids", []), note["id"], in_db))

        listener = asyncio.create_task(listen())
        await asyncio.sleep(0.5)
        await handlers.write_status(account_id, "error", reason)
        await asyncio.sleep(0.8)
        listener.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await listener
        c.check(
            "событие notification.new пришло каждому получателю по одному разу",
            sorted(u for users, _, _ in arrived for u in users) == sorted(expected_recipients),
            arrived,
        )
        c.check("и к моменту события уведомление уже было в базе", all(n[2] == 1 for n in arrived), arrived)

        row = (await list_rows(manager)).get(main_id, {})
        c.check("в строке списка статус аккаунта error", row.get("account", {}).get("status") == "error", row.get("account"))
        c.check("менеджеру причину обрыва не отдают", row.get("account", {}).get("status_reason") is None, row.get("account"))
        row_admin = (await list_rows(admin)).get(main_id, {})
        c.check("руководителю в строке списка — причина", row_admin.get("account", {}).get("status_reason") == reason, row_admin.get("account"))
        detail = (await manager.get(f"{BASE}/conversations/{main_id}")).json()
        c.check("в карточке чата — статус тот же", detail.get("account", {}).get("status") == "error", detail.get("account"))
        c.check("и причины у менеджера нет", detail.get("account", {}).get("status_reason") is None, detail.get("account"))
        detail_admin = (await admin.get(f"{BASE}/conversations/{main_id}")).json()
        c.check("руководитель в карточке получает причину", detail_admin.get("account", {}).get("status_reason") == reason, detail_admin.get("account"))

        before_messages = await _scalar("select count(*) from messages where conversation_id = :c", c=main_id)
        before_outbox = await _scalar("select count(*) from outbox where conversation_id = :c", c=main_id)
        for who, client, expected_text in (
            ("менеджер", manager, NO_LINK),
            ("руководитель", admin, NO_LINK_ADMIN),
        ):
            r = await client.post(f"{BASE}/conversations/{main_id}/messages", json={"text": f"Не уйдёт ({who})"})
            err = (r.json() if r.status_code == 409 else {}).get("error", {})
            c.check(f"{who}: отправка → 409", r.status_code == 409, f"{r.status_code} {r.text[:150]}")
            c.check(f"{who}: понятный текст на русском", err.get("message") == expected_text, err)
            c.check(f"{who}: машинный код account_offline", err.get("code") == "account_offline", err)
        c.check("сообщение не создано", await _scalar("select count(*) from messages where conversation_id = :c", c=main_id) == before_messages)
        c.check("в очередь ничего не встало", await _scalar("select count(*) from outbox where conversation_id = :c", c=main_id) == before_outbox)

        r = await manager.post(f"{BASE}/conversations/{main_id}/messages/{out_id}/retry")
        c.check("«Повторить» неудачного при мёртвой связи → 409", r.status_code == 409, f"{r.status_code} {r.text[:150]}")
        c.check("неудачное остаётся неудачным", await _scalar("select status::text from messages where id = :i", i=out_id) == "failed")

        r = await manager.post(
            f"{BASE}/conversations/{note_conv}/forward",
            json={"source_conversation_id": incoming_conv, "message_ids": [incoming_id], "hide_sender": True},
        )
        c.check("пересылка в чат такого аккаунта → 409", r.status_code == 409, f"{r.status_code} {r.text[:150]}")
        c.check("после отказа пересылка ничего не создала",
                await _scalar("select count(*) from messages where conversation_id = :c", c=note_conv) == 1)

        r = await manager.post(
            f"{BASE}/conversations/{main_id}/messages", json={"text": "Заметка при обрыве", "is_internal": True}
        )
        c.check("служебная заметка (в Telegram не уходит) проходит", r.status_code == 201, f"{r.status_code} {r.text[:150]}")

        # ------------------------------------------------ 3. уведомления
        c.section("3. Уведомление account_error — один раз на переход")
        got = await _notifications(account_id)
        c.check("получили руководители и менеджер аккаунта", set(got) == expected_recipients, (got, expected_recipients))
        c.check("каждый — ровно одно", set(got.values()) == {1}, got)
        c.check("менеджеры без этого аккаунта не получили", all(u not in got for u in others), (got, others))
        payload = (await _rows(
            "select entity_type, entity_id, payload from notifications "
            "where kind = 'account_error' and entity_id = :a limit 1", a=account_id))[0]
        c.check("связано с аккаунтом", (payload[0], payload[1]) == ("account", account_id), payload[:2])
        c.check("в данных название и причина",
                payload[2].get("account_title") == f"VCS-{suffix}" and payload[2].get("reason") == reason, payload[2])

        feed = (await manager.get(f"{BASE}/notifications", params={"limit": 20})).json()
        mine = [n for n in feed.get("items", []) if n.get("entity_id") == account_id and n.get("kind") == "account_error"]
        c.check("в ленте менеджера: заголовок и текст по-русски",
                len(mine) == 1 and mine[0].get("title") == "Аккаунт потерял связь"
                and mine[0].get("text") == f"VCS-{suffix} · {reason}", mine)

        await handlers.write_status(account_id, "error", "Повторный отчёт шлюза")
        await handlers.write_status(account_id, "error", reason)
        c.check("повторная запись error не плодит уведомлений", await _notifications(account_id) == got, await _notifications(account_id))

        # Шлюз повторяет один и тот же отчёт на каждое сообщение мёртвого аккаунта:
        # ничего не изменилось — ни записи в журнал, ни события.
        status_changes = (
            "select count(*) from event_log where entity_type = 'account' "
            "and entity_id = :a and action = 'account.status_changed'"
        )
        logged = await _scalar(status_changes, a=account_id)
        await asyncio.gather(*(handlers.write_status(account_id, "error", reason) for _ in range(4)))
        c.check("тот же отчёт ещё раз — в журнал ничего не пишется", await _scalar(status_changes, a=account_id) == logged)

        # ------------------------------------------------ 4. связь вернули
        c.section("4. Связь вернули и потеряли снова")
        await handlers.write_status(account_id, "connected", None)
        c.check("возврат связи уведомлений не создаёт", await _notifications(account_id) == got)
        row = (await list_rows(manager)).get(main_id, {})
        c.check("статус аккаунта снова connected, причины нет",
                row.get("account", {}).get("status") == "connected" and row.get("account", {}).get("status_reason") is None, row.get("account"))

        r = await manager.post(f"{BASE}/conversations/{main_id}/messages/{out_id}/retry")
        c.check("«Повторить» при живой связи работает", r.status_code == 200 and r.json().get("status") == "queued", f"{r.status_code} {r.text[:150]}")
        r = await manager.post(f"{BASE}/conversations/{main_id}/messages", json={"text": "Теперь уйдёт"})
        body = r.json() if r.status_code == 201 else {}
        c.check("отправка снова идёт: 201, статус queued", r.status_code == 201 and body.get("status") == "queued", f"{r.status_code} {r.text[:150]}")
        row = (await list_rows(manager)).get(main_id, {})
        c.check("превью показывает новое сообщение со статусом queued",
                row.get("last_message_id") == body.get("id") and row.get("last_message_status") == "queued"
                and row.get("last_message_preview") == "Вы: Теперь уйдёт", row)
        c.check("и оно стоит в очереди отправки",
                await _scalar("select count(*) from outbox where message_id = :m and status::text = 'pending'", m=body.get("id")) == 1)

        await handlers.write_status(account_id, "error", "Снова отозвали")
        again = await _notifications(account_id)
        c.check("второй обрыв — второе уведомление каждому", set(again) == expected_recipients and set(again.values()) == {2}, again)

        # ------------------------------------------------ 5. другие состояния
        c.section("5. Отключён и «ожидает входа» — тоже без связи")
        for status in (AccountStatus.DISCONNECTED, AccountStatus.PENDING):
            await _set_account_status(account_id, status)
            r = await manager.post(f"{BASE}/conversations/{main_id}/messages", json={"text": "Не уйдёт"})
            err = (r.json() if r.status_code == 409 else {}).get("error", {})
            c.check(f"{status.value}: отправка → 409 с тем же кодом", r.status_code == 409 and err.get("code") == "account_offline", f"{r.status_code} {r.text[:120]}")
        c.check("эти состояния уведомлений не создают", await _notifications(account_id) == again)

        await handlers.write_status(account_id, "connected", None)
        r = await manager.post(f"{BASE}/conversations/{main_id}/messages", json={"text": "Снова работает"})
        c.check("после возврата связи отправка снова проходит", r.status_code == 201, f"{r.status_code} {r.text[:120]}")

        # ------------------------------------------------ 6. одновременные отчёты
        c.section("6. Шлюз сообщает об обрыве из нескольких мест сразу")
        await asyncio.gather(
            handlers.write_status(account_id, "error", "Запуск сессии"),
            handlers.write_status(account_id, "error", "Отказ на отправке"),
            handlers.write_status(account_id, "error", "Отказ на пересылке"),
        )
        third = await _notifications(account_id)
        c.check("три одновременных отчёта — одно уведомление каждому, не три",
                set(third) == expected_recipients and set(third.values()) == {3}, third)
        c.check("аккаунт в итоге в error", await _scalar("select status::text from telegram_accounts where id = :a", a=account_id) == "error")

        # ------------------------------------------------ 7. аккаунт этапа «Бот»
        c.section("7. Аккаунт этапа «Бот» менеджерам не существует — и уведомления о нём им не шлют")
        async with SessionLocal() as db:
            warm = TelegramAccount(
                title=f"VCS-bot-{suffix}",
                phone=f"+7000{random.randint(1_000_000, 9_999_999)}",  # noqa: S311
                funnel_stage=FunnelStage.WARMUP,
                api_id=1,
                api_hash_enc=b"x",
                status=AccountStatus.CONNECTED,
                is_active=False,
            )
            db.add(warm)
            await db.flush()
            warm_id = warm.id
            extra_accounts.append(warm_id)
            db.add(AccountManager(account_id=warm_id, user_id=marina))
            await db.commit()
        await handlers.write_status(warm_id, "error", "Сессия отозвана")
        got_bot = await _notifications(warm_id)
        c.check("руководители уведомлены", set(got_bot) == admins and set(got_bot.values()) == {1}, got_bot)
        c.check("назначенный на «бота» менеджер — нет", marina not in got_bot, got_bot)
        c.check("и остальные менеджеры — нет", all(u not in got_bot for u in others), got_bot)
        feed = (await manager.get(f"{BASE}/notifications", params={"limit": 50})).json()
        c.check("в ленте менеджера уведомления о боте нет",
                not any(n.get("entity_id") == warm_id for n in feed.get("items", [])), feed.get("items"))

    finally:
        # ------------------------------------------------ уборка
        async with SessionLocal() as db:
            for extra in extra_accounts:
                await db.execute(
                    text("delete from notifications where entity_type = 'account' and entity_id = :a"),
                    {"a": extra},
                )
                await db.execute(
                    text("delete from event_log where entity_type = 'account' and entity_id = :a"),
                    {"a": extra},
                )
                await db.execute(text("delete from account_managers where account_id = :a"), {"a": extra})
                await db.execute(text("delete from telegram_accounts where id = :a"), {"a": extra})
            if conv_ids:
                await db.execute(text("delete from outbox where conversation_id = any(:c)"), {"c": conv_ids})
                await db.execute(
                    text(
                        "delete from event_log where (entity_type = 'message' and entity_id in "
                        "(select id from messages where conversation_id = any(:c))) "
                        "or (entity_type = 'conversation' and entity_id = any(:c))"
                    ),
                    {"c": conv_ids},
                )
                await db.execute(text("delete from messages where conversation_id = any(:c)"), {"c": conv_ids})
                await db.execute(text("delete from conversations where id = any(:c)"), {"c": conv_ids})
            if client_ids:
                await db.execute(text("delete from clients where id = any(:c)"), {"c": client_ids})
            if account_id:
                await db.execute(
                    text("delete from notifications where entity_type = 'account' and entity_id = :a"),
                    {"a": account_id},
                )
                await db.execute(
                    text("delete from event_log where entity_type = 'account' and entity_id = :a"),
                    {"a": account_id},
                )
                await db.execute(text("delete from account_managers where account_id = :a"), {"a": account_id})
                await db.execute(text("delete from telegram_accounts where id = :a"), {"a": account_id})
            await db.commit()
        await admin.aclose()
        await manager.aclose()

    return c.finish()


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
