"""Поиск (D-35, BUG-02) и история поиска (L4): сквозная проверка на работающем стенде.

Что проверяем:

* клиент БЕЗ `display_name` (как у всех 1802 клиентов в проде), но с именем из
  Telegram, @username и телефоном находится по имени, фамилии, обоим словам в
  любом порядке, @username с «@» и без, части телефона в любом форматировании,
  «ё» вместо «е», по id и по telegram_id (если именно он показан как имя);
* три места — глобальный поиск, раздел «Клиенты» и список чатов — отвечают
  согласованно: одному запросу соответствует один набор клиентов;
* в выдаче всегда читаемое имя (лестница `Client.name`), а не null; в группах
  «Оплаты», «Чаты», «Файлы» — тоже;
* оплата находится по имени клиента и по названию услуги, чат — по имени и по
  тексту, файл — по имени файла и по имени клиента;
* права видимости не нарушены: менеджер не видит чужих клиентов, их оплаты,
  сообщения и файлы; удалённый клиент не виден никому;
* сумма оплат клиента в группе «Клиенты» режется по видимости (D-26): менеджер,
  видящий клиента через один свой чат, не видит сумм из чужих чатов и из «бота»;
* запрос длиннее 255 символов — 422; поиск по тексту сообщений идёт через индекс
  `ix_messages_text_fts` (выражение запроса совпадает с индексным);
* `%` и `_` в запросе — обычные символы, а не шаблон;
* «Показать все»: `totals` честно говорит, сколько найдено, а `limit` до 50;
* история: GET /search её не пишет; запись только через POST /search/history;
  дубли (регистр, ё/е, пробелы) не плодятся, повтор поднимает запись наверх,
  остаются последние 10, у каждого сотрудника своя, очистка работает.

Данные создаются напрямую в базе под уникальным префиксом и убираются в
`finally`; история двух демо-пользователей сохраняется и возвращается как была.

Запуск (под замком, см. docs/18):
  docker exec astra-crm-api-1 sh -c 'cd /app && python -m tests.verify_search'
"""

import asyncio
import secrets
import sys
from typing import Any

import httpx
from sqlalchemy import text

from app.core.db import SessionLocal
from app.services.search_service import MESSAGE_FTS
from tests.support import ADMIN, BASE, MANAGER, Checks, login

LETTERS = "abcdefghjkmnpqrstuvwxyz"
ACCOUNT_MARINA = 2  # «Марина · допы» — аккаунт менеджера Марины (её видимость)
ACCOUNT_ANNA = 3  # аккаунт другой сотрудницы: Марина его не видит
ACCOUNT_WARMUP = 1  # «бот» — менеджерам не существует вовсе
MARINA, ANNA, ELENA = 2, 3, 1
# Оплаченные сделки одного клиента в трёх чатах: чей чат — тот и считает (D-26).
PAID_OWN, PAID_ANNA, PAID_WARMUP = 100_000, 700_000, 20_000


def unique_tag() -> str:
    return "".join(secrets.choice(LETTERS) for _ in range(6))


def random_digits(count: int) -> str:
    return "".join(str(secrets.randbelow(10)) for _ in range(count))


async def get_search(client: httpx.AsyncClient, q: str, **params: Any) -> dict[str, Any]:
    response = await client.get(f"{BASE}/search", params={"q": q, **params})
    assert response.status_code == 200, (q, response.status_code, response.text[:200])
    return response.json()


def client_ids(groups: dict[str, Any]) -> set[int]:
    return {row["id"] for row in groups["clients"]}


async def list_client_ids(client: httpx.AsyncClient, q: str) -> set[int]:
    response = await client.get(f"{BASE}/clients", params={"q": q, "limit": 200})
    assert response.status_code == 200, (q, response.status_code, response.text[:200])
    return {row["id"] for row in response.json()["items"]}


async def chat_client_ids(client: httpx.AsyncClient, q: str) -> set[int]:
    response = await client.get(f"{BASE}/conversations", params={"q": q, "limit": 100})
    assert response.status_code == 200, (q, response.status_code, response.text[:200])
    return {row["client"]["id"] for row in response.json()["items"]}


class Fixture:
    """Тестовые клиенты, диалоги, сообщения, оплаты и файлы под одним префиксом."""

    def __init__(self, tag: str) -> None:
        self.tag = tag
        self.client_ids: list[int] = []
        self.ids: dict[str, int] = {}
        self.conv: dict[str, int] = {}
        self.deal: dict[str, int] = {}
        self.phone_digits = "999" + random_digits(7)
        self.telegram_ids: dict[str, int] = {}

    @property
    def phone(self) -> str:
        d = self.phone_digits
        return f"+7 {d[:3]} {d[3:6]}-{d[6:8]}-{d[8:]}"

    async def client(  # noqa: PLR0913
        self,
        db: Any,
        key: str,
        *,
        first: str | None = None,
        last: str | None = None,
        username: str | None = None,
        phone: str | None = None,
        display: str | None = None,
        account: int | None = ACCOUNT_MARINA,
        responsible: int | None = None,
        deleted: bool = False,
    ) -> int:
        telegram_id = 7_000_000_000 + secrets.randbelow(900_000_000)
        client_id = await db.scalar(
            text(
                "insert into clients (telegram_id, tg_username, tg_first_name, tg_last_name, "
                "display_name, phone, deleted_at) values (:tg, :u, :f, :l, :d, :p, "
                "case when :del then now() end) returning id"
            ),
            {
                "tg": telegram_id,
                "u": username,
                "f": first,
                "l": last,
                "d": display,
                "p": phone,
                "del": deleted,
            },
        )
        self.client_ids.append(client_id)
        self.ids[key] = client_id
        self.telegram_ids[key] = telegram_id
        if account is not None:
            self.conv[key] = await db.scalar(
                text(
                    "insert into conversations (client_id, account_id, tg_chat_id, responsible_id, "
                    "last_message_at) values (:c, :a, :chat, :r, now()) returning id"
                ),
                {
                    "c": client_id,
                    "a": account,
                    "chat": 8_000_000_000 + secrets.randbelow(900_000_000),
                    "r": responsible,
                },
            )
        return client_id

    async def message(self, db: Any, key: str, body: str) -> int:
        return await db.scalar(
            text(
                "insert into messages (conversation_id, direction, author_kind, kind, text, "
                "status, created_at) values (:c, 'in', 'client', 'text', :t, 'sent', now()) "
                "returning id"
            ),
            {"c": self.conv[key], "t": body},
        )

    async def deal_for(self, db: Any, key: str, item: str) -> int:
        deal_id = await db.scalar(
            text(
                "insert into deals (client_id, conversation_id, created_by_id, sold_by_id, "
                "payment_method, status, total_amount) values (:cl, :c, :u, :u, 'requisites', "
                "'awaiting', 150000) returning id"
            ),
            {"cl": self.ids[key], "c": self.conv[key], "u": ELENA},
        )
        await db.execute(
            text("insert into deal_items (deal_id, name, amount) values (:d, :n, 150000)"),
            {"d": deal_id, "n": item},
        )
        self.deal[key] = deal_id
        return deal_id

    async def conversation(
        self, db: Any, key: str, account: int, *, responsible: int | None = None
    ) -> int:
        """Ещё один диалог того же клиента, но на другом аккаунте (один диалог на аккаунт)."""
        return await db.scalar(
            text(
                "insert into conversations (client_id, account_id, tg_chat_id, responsible_id, "
                "last_message_at) values (:c, :a, :chat, :r, now()) returning id"
            ),
            {
                "c": self.ids[key],
                "a": account,
                "chat": 8_000_000_000 + secrets.randbelow(900_000_000),
                "r": responsible,
            },
        )

    async def paid_deal(self, db: Any, key: str, conversation_id: int, amount: int) -> int:
        return await db.scalar(
            text(
                "insert into deals (client_id, conversation_id, created_by_id, sold_by_id, "
                "payment_method, status, total_amount, paid_at) values (:cl, :c, :u, :u, "
                "'requisites', 'paid', :amount, now()) returning id"
            ),
            {"cl": self.ids[key], "c": conversation_id, "u": ELENA, "amount": amount},
        )

    async def attachment(self, db: Any, key: str, message_id: int, name: str) -> int:
        return await db.scalar(
            text(
                "insert into attachments (message_id, conversation_id, client_id, file_name, "
                "kind, status, mime_type) values (:m, :c, :cl, :n, 'document', 'ready', "
                "'application/pdf') returning id"
            ),
            {"m": message_id, "c": self.conv[key], "cl": self.ids[key], "n": name},
        )

    async def cleanup(self) -> None:
        if not self.client_ids:
            return
        async with SessionLocal() as db:
            params = {"ids": self.client_ids}
            await db.execute(text("delete from attachments where client_id = any(:ids)"), params)
            await db.execute(text("delete from deals where client_id = any(:ids)"), params)
            await db.execute(text("delete from conversations where client_id = any(:ids)"), params)
            await db.execute(text("delete from clients where id = any(:ids)"), params)
            await db.commit()


async def snapshot_history(user_ids: list[int]) -> list[dict[str, Any]]:
    async with SessionLocal() as db:
        rows = await db.execute(
            text(
                "select id, user_id, query, created_at from search_history where user_id = any(:u)"
            ),
            {"u": user_ids},
        )
        return [dict(row._mapping) for row in rows]


async def restore_history(user_ids: list[int], rows: list[dict[str, Any]]) -> None:
    async with SessionLocal() as db:
        await db.execute(
            text("delete from search_history where user_id = any(:u)"), {"u": user_ids}
        )
        for row in rows:
            await db.execute(
                text(
                    "insert into search_history (id, user_id, query, created_at) "
                    "values (:id, :user_id, :query, :created_at)"
                ),
                row,
            )
        await db.commit()


async def run() -> int:  # noqa: PLR0915
    c = Checks("Поиск: имена, телефоны, права, «показать все», история")
    admin = await login(ADMIN)
    manager = await login(MANAGER)
    tag = unique_tag()
    fx = Fixture(tag)
    saved_history = await snapshot_history([ELENA, MARINA])
    try:
        # ------------------------------------------------------------ данные
        async with SessionLocal() as db:
            await fx.client(
                db,
                "ksenia",
                first="Ксения",
                last=f"Сергеева{tag}",
                username=f"ksenia_{tag}",
                phone=fx.phone,
            )
            await fx.client(db, "artem", first=f"Артём{tag}", last=f"Фёдоров{tag}")
            await fx.client(db, "wassup", first=f"Wassup{tag}", responsible=MARINA)
            await fx.client(db, "bare")  # ни имени, ни @username: имя = telegram_id
            await fx.client(db, "nick", username=f"only_nick_{tag}")
            await fx.client(
                db, "manual", display=f"Вписано рукой {tag}", first="Другое", last=f"Телеграм{tag}"
            )
            await fx.client(db, "foreign", first="Чужая", last=f"Клиентка{tag}", responsible=ANNA)
            await fx.client(db, "annas", first="Анин", last=f"Клиент{tag}", account=ACCOUNT_ANNA)
            await fx.client(db, "warm", first="Прогрев", last=f"Бот{tag}", account=ACCOUNT_WARMUP)
            await fx.client(db, "gone", first="Удалена", last=f"Давно{tag}", deleted=True)
            await fx.client(db, "pct", first=f"Сто%{tag}")
            await fx.client(db, "pctx", first=f"СтоХ{tag}")
            await fx.client(db, "und", first=f"Сто_{tag}")
            await fx.client(db, "undx", first=f"СтоЯ{tag}")

            # Клиент в трёх чатах: Маринин (видит Марина), Анин и «бота» (их Марина не
            # видит). У каждого чата — оплаченная сделка своей суммы (проверка D-26).
            await fx.client(db, "split", first=f"Раздвоенный{tag}", responsible=MARINA)
            anna_conv = await fx.conversation(db, "split", ACCOUNT_ANNA)
            warm_conv = await fx.conversation(db, "split", ACCOUNT_WARMUP)
            await fx.paid_deal(db, "split", fx.conv["split"], PAID_OWN)
            await fx.paid_deal(db, "split", anna_conv, PAID_ANNA)
            await fx.paid_deal(db, "split", warm_conv, PAID_WARMUP)

            message_id = await fx.message(db, "ksenia", f"Хочу расклад на любовь {tag}")
            await fx.message(db, "foreign", f"Секретная переписка {tag}")
            await fx.deal_for(db, "ksenia", f"Расклад{tag}")
            await fx.deal_for(db, "foreign", f"Чужой расклад{tag}")
            await fx.attachment(db, "ksenia", message_id, f"Разбор карты {tag}.pdf")
            foreign_msg = await fx.message(db, "foreign", "файл чужой")
            await fx.attachment(db, "foreign", foreign_msg, f"Секрет {tag}.pdf")

            # Для «показать все»: 7 и 52 клиента с общим словом.
            for index in range(7):
                await fx.client(db, f"few{index}", first=f"Мало{tag}", last=f"Номер{index}")
            for index in range(52):
                await fx.client(db, f"many{index}", first=f"Масса{tag}", last=f"Номер{index}")
            await db.commit()
        ids = fx.ids
        c.check("тестовые клиенты созданы", len(fx.client_ids) == 15 + 7 + 52, len(fx.client_ids))

        # --------------------------------------------- имя из Telegram, без display_name
        c.section("Поиск по имени из Telegram (display_name пуст)")
        cases = {
            "фамилия": f"сергеева{tag}",
            "фамилия в другом регистре": f"СЕРГЕЕВА{tag.upper()}",
            "имя + фамилия": f"Ксения Сергеева{tag}",
            "фамилия + имя": f"Сергеева{tag} Ксения",
            "лишние пробелы": f"  ксения    сергеева{tag}  ",
            "@username": f"@ksenia_{tag}",
            "username без @": f"ksenia_{tag}",
            "имя + @username": f"Ксения @ksenia_{tag}",
            "кусок фамилии": f"ергеева{tag[:3]}",  # подстрока «Сергеева<метка>»
        }
        for title, query in cases.items():
            groups = await get_search(admin, query)
            c.check(f"глобальный поиск: {title}", ids["ksenia"] in client_ids(groups), query)
        wrong = await get_search(admin, f"Ксения Иванова{tag}")
        c.check(
            "каждое слово обязательно: чужая фамилия не находит",
            ids["ksenia"] not in client_ids(wrong),
        )

        c.section("«ё» и «е» не различаются")
        for query in (f"артем{tag} федоров{tag}", f"АРТЁМ{tag}", f"фёдоров{tag} артём{tag}"):
            c.check(
                f"«{query}» находит Артёма Фёдорова",
                ids["artem"] in client_ids(await get_search(admin, query)),
                query,
            )

        c.section("Клиент без username и без фамилии")
        found = await get_search(admin, f"wassup{tag}")
        row = next((r for r in found["clients"] if r["id"] == ids["wassup"]), None)
        c.check("«Wassup…» находится по имени", row is not None)
        c.check("имя в выдаче — из Telegram", bool(row) and row["name"] == f"Wassup{tag}", row)
        nick = await get_search(admin, f"only_nick_{tag}")
        row = next((r for r in nick["clients"] if r["id"] == ids["nick"]), None)
        c.check("клиент только с @username находится", row is not None)
        c.check(
            "имя такого клиента — @username", bool(row) and row["name"] == f"@only_nick_{tag}", row
        )
        c.check(
            "и @username отдаётся отдельным полем",
            bool(row) and row["tg_username"] == f"only_nick_{tag}",
            row,
        )

        c.section("Телефон ищется по цифрам")
        digits = fx.phone_digits
        phone_queries = {
            "последние 6 цифр": digits[-6:],
            "как записан": fx.phone,
            "без форматирования": digits,
            "с семёркой и скобками": f"+7 ({digits[:3]}) {digits[3:6]}-{digits[6:8]}-{digits[8:]}",
            "через дефисы": f"{digits[3:6]}-{digits[6:8]}-{digits[8:]}",
            "имя и часть телефона": f"ксения {digits[-6:]}",
        }
        for title, query in phone_queries.items():
            c.check(
                f"глобальный поиск: {title}",
                ids["ksenia"] in client_ids(await get_search(admin, query)),
                query,
            )
        c.check(
            "два цифровых слова склеиваются в номер",
            ids["ksenia"] in client_ids(await get_search(admin, f"{digits[:5]} {digits[5:]}")),
        )
        other = random_digits(9)
        while other in f"7{digits}":
            other = random_digits(9)
        c.check(
            "чужие цифры телефона не находят",
            ids["ksenia"] not in client_ids(await get_search(admin, other)),
            other,
        )

        c.section("Числовой id клиента — точное совпадение")
        by_id = await get_search(admin, str(ids["ksenia"]))
        c.check("id клиента находит клиента", ids["ksenia"] in client_ids(by_id))
        bare = await get_search(admin, str(fx.telegram_ids["bare"]))
        row = next((r for r in bare["clients"] if r["id"] == ids["bare"]), None)
        c.check("клиент без имени находится по telegram_id", row is not None)
        c.check(
            "и именно он показан как имя",
            bool(row) and row["name"] == str(fx.telegram_ids["bare"]),
            row,
        )
        huge = await admin.get(f"{BASE}/search", params={"q": "9" * 40})
        c.check(
            "число длиннее bigint — не падение, а «ничего»",
            huge.status_code == 200,
            huge.text[:120],
        )

        c.section("Имя по лестнице: display_name → Telegram → @username → telegram_id")
        manual = await get_search(admin, f"Телеграм{tag}")
        row = next((r for r in manual["clients"] if r["id"] == ids["manual"]), None)
        c.check("клиент с вписанным именем находится и по имени из Telegram", row is not None)
        c.check(
            "в выдаче имя менеджера вытесняет Telegram",
            bool(row) and row["name"] == f"Вписано рукой {tag}",
            row,
        )
        by_display = await get_search(admin, f"вписано рукой {tag}")
        c.check("и по имени, вписанному менеджером", ids["manual"] in client_ids(by_display))
        listing = (await admin.get(f"{BASE}/clients", params={"q": tag, "limit": 200})).json()[
            "items"
        ]
        list_names = {r["id"]: r["name"] for r in listing}
        everything = await get_search(admin, tag, limit=50)
        same_names = [
            (r["id"], r["name"], list_names.get(r["id"]))
            for r in everything["clients"]
            if r["id"] in list_names and r["name"] != list_names[r["id"]]
        ]
        c.check(
            "имя в поиске совпадает с именем в разделе «Клиенты»", not same_names, same_names[:3]
        )
        c.check(
            "ни у одного найденного клиента нет пустого имени",
            all(bool(r["name"]) for r in everything["clients"]),
        )

        # ------------------------------------------------ согласованность трёх мест
        c.section("Глобальный поиск, «Клиенты» и список чатов дают одно и то же")
        mine = {
            "фамилия": f"сергеева{tag}",
            "два слова": f"сергеева{tag} ксения",
            "username": f"@ksenia_{tag}",
            "ё/е": f"артем{tag}",
            "телефон": digits[-6:],
            "id клиента": str(ids["ksenia"]),
            "общее слово": f"мало{tag}",
            "процент": f"сто%{tag}",
            "подчёркивание": f"сто_{tag}",
            "чужая фамилия": f"Клиентка{tag}",
        }
        for role, session in (("руководитель", admin), ("менеджер", manager)):
            for title, query in mine.items():
                universe = set(fx.client_ids)
                g = client_ids(await get_search(session, query, limit=50)) & universe
                lst = (await list_client_ids(session, query)) & universe
                chats = (await chat_client_ids(session, query)) & universe
                c.check(
                    f"{role}: «{title}» — три места согласны",
                    g == lst == chats,
                    f"поиск={sorted(g)} клиенты={sorted(lst)} чаты={sorted(chats)}",
                )

        c.section("Права видимости")
        hidden = {
            "foreign": ("клиент другого менеджера", f"Клиентка{tag}"),
            "annas": ("клиент чужого аккаунта", f"Клиент{tag}"),
            "warm": ("клиент аккаунта-бота", f"Бот{tag}"),
            "gone": ("удалённый клиент", f"Давно{tag}"),
        }
        for key, (title, query) in hidden.items():
            seen = await get_search(manager, query, limit=50)
            c.check(f"менеджер не находит: {title}", ids[key] not in client_ids(seen))
            c.check(
                f"менеджер не видит в «Клиентах»: {title}",
                ids[key] not in await list_client_ids(manager, query),
            )
            if key != "gone":
                c.check(
                    f"руководитель находит: {title}",
                    ids[key] in client_ids(await get_search(admin, query)),
                )
        c.check(
            "удалённого клиента не видит и руководитель",
            ids["gone"] not in client_ids(await get_search(admin, f"Давно{tag}")),
        )
        secret = await get_search(manager, f"Секретная переписка {tag}")
        c.check(
            "менеджер не находит чужую переписку",
            not any(r["conversation_id"] == fx.conv["foreign"] for r in secret["chats"]),
        )
        admin_secret = await get_search(admin, f"Секретная переписка {tag}")
        c.check(
            "руководитель находит её по тексту",
            any(r["conversation_id"] == fx.conv["foreign"] for r in admin_secret["chats"]),
        )
        c.check(
            "менеджер не находит чужой файл",
            not (await get_search(manager, f"Секрет {tag}"))["files"],
        )
        c.check(
            "и чужую оплату по имени клиента",
            not any(
                d["id"] == fx.deal["foreign"]
                for d in (await get_search(manager, f"Клиентка{tag}"))["deals"]
            ),
        )
        c.check(
            "руководитель находит оплату по имени клиента",
            any(
                d["id"] == fx.deal["foreign"]
                for d in (await get_search(admin, f"Клиентка{tag}"))["deals"]
            ),
        )
        c.check(
            "у менеджера нет группы сотрудников",
            (await get_search(manager, "Марина"))["managers"] == [],
        )

        c.section("Суммы оплат в группе «Клиенты» режутся по видимости (D-26)")
        split_name = f"Раздвоенный{tag}"
        found_by_manager = (await get_search(manager, split_name))["clients"]
        found_by_admin = (await get_search(admin, split_name))["clients"]
        as_manager = next((r for r in found_by_manager if r["id"] == ids["split"]), None)
        as_admin = next((r for r in found_by_admin if r["id"] == ids["split"]), None)
        c.check("менеджер находит клиента через свой чат", as_manager is not None)
        c.check(
            "и видит сумму только по своему чату, без Аниного и «бота»",
            bool(as_manager) and as_manager["paid_amount"] == PAID_OWN,
            as_manager,
        )
        c.check(
            "руководитель видит сумму по всем чатам клиента",
            bool(as_admin) and as_admin["paid_amount"] == PAID_OWN + PAID_ANNA + PAID_WARMUP,
            as_admin,
        )
        in_clients = next(
            (
                r
                for r in (
                    await manager.get(f"{BASE}/clients", params={"q": split_name, "limit": 50})
                ).json()["items"]
                if r["id"] == ids["split"]
            ),
            None,
        )
        c.check(
            "и ровно то же число, что в разделе «Клиенты»",
            bool(in_clients) and int(in_clients["paid_amount"]) == PAID_OWN,
            in_clients,
        )

        c.section("Длина запроса и индекс полнотекстового поиска")
        at_limit = await admin.get(f"{BASE}/search", params={"q": "я" * 255})
        c.check(
            "запрос в 255 символов принимается", at_limit.status_code == 200, at_limit.status_code
        )
        too_long = await admin.get(f"{BASE}/search", params={"q": "я" * 256})
        c.check("в 256 символов — 422", too_long.status_code == 422, too_long.status_code)
        explain = text(
            "explain select m.id from messages m where "  # noqa: S608
            f"{MESSAGE_FTS} @@ plainto_tsquery('russian', :q)"
        )
        async with SessionLocal() as db:
            # На малой базе планировщик и с индексом может выбрать перебор, поэтому в
            # проверочной транзакции перебор выключен: так видно, ЕСТЬ ли у запроса индекс.
            await db.execute(text("set local enable_seqscan = off"))
            plan = "\n".join(row[0] for row in await db.execute(explain, {"q": "расклад"}))
            await db.rollback()
        c.check(
            "поиск по сообщениям использует индекс ix_messages_text_fts",
            "ix_messages_text_fts" in plan and "Seq Scan" not in plan,
            plan,
        )

        # ------------------------------------------------------------- оплаты
        c.section("Оплаты")
        by_client = await get_search(admin, f"Ксения Сергеева{tag}")
        deal = next((d for d in by_client["deals"] if d["id"] == fx.deal["ksenia"]), None)
        c.check("оплата находится по имени клиента (из Telegram)", deal is not None)
        c.check(
            "в оплате читаемое имя клиента",
            bool(deal) and deal["client_name"] == f"Ксения Сергеева{tag}",
            deal,
        )
        by_item = await get_search(admin, f"расклад{tag}")
        c.check(
            "оплата находится по названию услуги",
            any(d["id"] == fx.deal["ksenia"] for d in by_item["deals"]),
        )
        mixed = await get_search(admin, f"ксения расклад{tag}")
        c.check(
            "слова можно брать из имени клиента и из услуги",
            any(d["id"] == fx.deal["ksenia"] for d in mixed["deals"]),
        )
        c.check(
            "по номеру DEAL-N",
            any(
                d["id"] == fx.deal["ksenia"]
                for d in (await get_search(admin, f"DEAL-{fx.deal['ksenia']}"))["deals"]
            ),
        )
        c.check(
            "по одному числу",
            any(
                d["id"] == fx.deal["ksenia"]
                for d in (await get_search(admin, str(fx.deal["ksenia"])))["deals"]
            ),
        )
        c.check(
            "чужая фамилия оплату не находит",
            not any(
                d["id"] == fx.deal["ksenia"]
                for d in (await get_search(admin, f"Иванова{tag} ксения"))["deals"]
            ),
        )

        # -------------------------------------------------------------- чаты
        c.section("Чаты")
        by_text = await get_search(admin, f"расклад на любовь {tag}")
        hit = next((r for r in by_text["chats"] if r["conversation_id"] == fx.conv["ksenia"]), None)
        c.check("чат находится по тексту сообщения", hit is not None)
        c.check(
            "у такого чата читаемое имя клиента",
            bool(hit) and hit["client_name"] == f"Ксения Сергеева{tag}",
            hit,
        )
        by_name = await get_search(admin, f"Ксения Сергеева{tag}")
        hit = next((r for r in by_name["chats"] if r["conversation_id"] == fx.conv["ksenia"]), None)
        c.check("чат находится по имени клиента, как в списке чатов", hit is not None)
        c.check("у него есть что показать под именем", bool(hit) and bool(hit["snippet"]), hit)
        c.check(
            "и адрес сообщения для ключа строки", bool(hit) and hit["message_id"] is not None, hit
        )
        keys = [(r["conversation_id"], r["message_id"]) for r in by_name["chats"]]
        c.check("строки чатов не повторяются", len(keys) == len(set(keys)), keys)

        # ------------------------------------------------------------- файлы
        c.section("Файлы")
        by_file = await get_search(admin, f"разбор карты {tag}")
        file_row = next((f for f in by_file["files"] if f["client_id"] == ids["ksenia"]), None)
        c.check("файл находится по имени файла (несколько слов)", file_row is not None)
        c.check(
            "с читаемым именем клиента",
            bool(file_row) and file_row["client_name"] == f"Ксения Сергеева{tag}",
            file_row,
        )
        by_owner = await get_search(admin, f"Сергеева{tag}")
        c.check(
            "файл находится по имени клиента",
            any(f["client_id"] == ids["ksenia"] for f in by_owner["files"]),
        )
        mixed = await get_search(admin, f"ксения карты {tag}")
        c.check(
            "слова из имени клиента и из имени файла в одном запросе",
            any(f["client_id"] == ids["ksenia"] for f in mixed["files"]),
        )

        # --------------------------------------------------- сотрудники
        c.section("Сотрудники")
        async with SessionLocal() as db:
            full_name = await db.scalar(
                text("select full_name from users where id = :i"), {"i": MARINA}
            )
        reverse = " ".join(reversed(full_name.split()))
        staff = await get_search(admin, reverse)
        c.check(
            "сотрудник находится, даже если слова в другом порядке",
            any(s["id"] == MARINA for s in staff["managers"]),
            (full_name, reverse),
        )

        # ----------------------------------------------- спецсимволы и ввод
        c.section("Служебные символы — обычные символы")
        pct = client_ids(await get_search(admin, f"сто%{tag}"))
        c.check(
            "«%» ищет только настоящий процент",
            ids["pct"] in pct and ids["pctx"] not in pct,
            sorted(pct),
        )
        und = client_ids(await get_search(admin, f"сто_{tag}"))
        c.check(
            "«_» ищет только настоящее подчёркивание",
            ids["und"] in und and ids["undx"] not in und,
            sorted(und),
        )
        empty = await get_search(admin, "%%")
        c.check(
            "«%%» не означает «всё»",
            not any(empty[g] for g in ("clients", "deals", "chats", "files")),
        )
        c.check("«@» не означает «всё»", not (await get_search(admin, "@@"))["clients"])
        evil = await admin.get(f"{BASE}/search", params={"q": "x' or 1=1 --"})
        c.check(
            "попытка инъекции — обычный запрос без совпадений",
            evil.status_code == 200 and not evil.json()["clients"],
        )
        one = await get_search(admin, "а")
        c.check(
            "запрос из одного символа ничего не ищет",
            not one["clients"] and one["totals"]["clients"] == 0,
        )
        c.check(
            "контракт ответа: все прежние группы на месте",
            all(k in one for k in ("clients", "deals", "chats", "files", "managers")),
        )
        row = (await get_search(admin, f"сергеева{tag}"))["clients"][0]
        c.check(
            "и прежние поля строки клиента",
            all(k in row for k in ("id", "name", "phone", "paid_amount")),
            row,
        )

        # ----------------------------------------------------- показать все
        c.section("«Показать все»: totals и предел")
        few = await get_search(admin, f"мало{tag}", limit=5)
        c.check(
            "показано пять, найдено семь",
            len(few["clients"]) == 5 and few["totals"]["clients"] == 7,
            (len(few["clients"]), few["totals"]),
        )
        few_all = await get_search(admin, f"мало{tag}", limit=50)
        c.check("с limit=50 — все семь", len(few_all["clients"]) == 7, len(few_all["clients"]))
        many = await get_search(admin, f"масса{tag}", limit=5)
        c.check(
            "совпадений больше 50 — totals = 51 («больше 50»)",
            many["totals"]["clients"] == 51,
            many["totals"],
        )
        many_all = await get_search(admin, f"масса{tag}", limit=50)
        c.check("и показано ровно 50", len(many_all["clients"]) == 50, len(many_all["clients"]))
        c.check("без повторов", len({r["id"] for r in many_all["clients"]}) == 50)
        over = await admin.get(f"{BASE}/search", params={"q": "ксения", "limit": 51})
        c.check("limit больше 50 — 422", over.status_code == 422, over.status_code)
        only = await get_search(admin, f"масса{tag}", types="clients", limit=5)
        c.check(
            "вкладка-фильтр: чужие группы пустые",
            not only["deals"] and not only["files"] and only["totals"]["deals"] == 0,
        )

        # ================================================================ история
        c.section("История поиска (L4): GET /search её не пишет")
        for session in (admin, manager):
            r = await session.delete(f"{BASE}/search/history")
            c.check("очистка истории — 204", r.status_code == 204, r.status_code)
        for query in (f"сергеева{tag}", f"ксения {tag}", "wassup", f"сергеева{tag}"):
            await get_search(admin, query)
            await get_search(admin, query, types="clients")
        hist = (await admin.get(f"{BASE}/search/history")).json()
        c.check("после нескольких поисков история пуста", hist == [], hist)

        c.section("История: явное сохранение, дубли, подъём наверх")

        async def remember(session: httpx.AsyncClient, q: str) -> httpx.Response:
            return await session.post(f"{BASE}/search/history", json={"q": q})

        async def history_of(session: httpx.AsyncClient) -> list[dict[str, Any]]:
            return (await session.get(f"{BASE}/search/history")).json()

        r = await remember(admin, "Ксения Сергеева")
        c.check("POST /search/history — 200", r.status_code == 200, r.text[:150])
        c.check(
            "ответ — обновлённая история",
            [i["query"] for i in r.json()] == ["Ксения Сергеева"],
            r.text[:150],
        )
        await remember(admin, "  ксения   сергеева ")
        await remember(admin, "КСЕНИЯ СЕРГЕЕВА")
        hist = await history_of(admin)
        c.check("регистр и пробелы не плодят дубли", len(hist) == 1, [i["query"] for i in hist])
        c.check("остаётся последнее написание", hist[0]["query"] == "КСЕНИЯ СЕРГЕЕВА", hist)
        await remember(admin, "Ёлка")
        await remember(admin, "елка")
        c.check(
            "ё и е — один запрос",
            len(await history_of(admin)) == 2,
            [i["query"] for i in await history_of(admin)],
        )
        await asyncio.gather(*(remember(admin, "Одновременно") for _ in range(6)))
        simultaneous = [i for i in await history_of(admin) if i["query"] == "Одновременно"]
        c.check(
            "шесть одновременных нажатий Enter — одна запись", len(simultaneous) == 1, simultaneous
        )

        await remember(admin, "первый запрос")
        await remember(admin, "второй запрос")
        await remember(admin, "третий запрос")
        await remember(admin, "Первый  ЗАПРОС")
        order = [i["query"] for i in await history_of(admin)]
        c.check(
            "повтор поднимает запись наверх",
            order[:3] == ["Первый ЗАПРОС", "третий запрос", "второй запрос"],
            order,
        )
        c.check(
            "и не оставляет прежнюю", sum(q.lower().startswith("первый") for q in order) == 1, order
        )

        for index in range(14):
            await remember(admin, f"запрос номер {index}")
        hist = await history_of(admin)
        c.check("хранится не больше десяти", len(hist) == 10, len(hist))
        c.check(
            "новые сверху",
            hist[0]["query"] == "запрос номер 13" and hist[-1]["query"] == "запрос номер 4",
            [i["query"] for i in hist],
        )

        r = await remember(admin, "a")
        c.check("запрос короче двух символов — 422", r.status_code == 422, r.status_code)
        r = await remember(admin, "   ")
        c.check("пустой запрос — 422", r.status_code == 422, r.status_code)
        r = await remember(admin, "я" * 300)
        c.check("слишком длинный — 422, а не обрезка молча", r.status_code == 422, r.status_code)
        anon = httpx.AsyncClient(timeout=30)
        r = await anon.post(f"{BASE}/search/history", json={"q": "кто-то"})
        c.check("без входа — 401", r.status_code == 401, r.status_code)
        await anon.aclose()

        c.section("История: у каждого сотрудника своя; дубли старой версии")
        c.check("у менеджера пусто, пока он сам ничего не искал", await history_of(manager) == [])
        await remember(manager, "запрос номер 13")
        c.check(
            "запись менеджера — только у него",
            [i["query"] for i in await history_of(manager)] == ["запрос номер 13"],
        )
        c.check("у руководителя прежние десять", len(await history_of(admin)) == 10)
        r = await manager.delete(f"{BASE}/search/history")
        c.check(
            "очистка менеджера не трогает руководителя",
            r.status_code == 204 and len(await history_of(admin)) == 10,
        )

        # Старая версия писала историю на каждый поиск: в базе могут лежать дубли.
        async with SessionLocal() as db:
            for q in ("Старый дубль", "старый  дубль", "СТАРЫЙ ДУБЛЬ", "Другой"):
                await db.execute(
                    text("insert into search_history (user_id, query) values (:u, :q)"),
                    {"u": MARINA, "q": q},
                )
            await db.commit()
        shown = [i["query"] for i in await history_of(manager)]
        c.check("дубли старой версии показываются один раз", len(shown) == 2, shown)
        c.check(
            "самый свежий вариант побеждает",
            shown[0] == "Другой" and shown[1] == "СТАРЫЙ ДУБЛЬ",
            shown,
        )
        await remember(manager, "старый дубль")
        async with SessionLocal() as db:
            left = await db.scalar(
                text("select count(*) from search_history where user_id = :u"), {"u": MARINA}
            )
        c.check("сохранение заодно вычищает старые дубли в базе", left == 2, left)

        r = await admin.delete(f"{BASE}/search/history")
        c.check("очистка работает", r.status_code == 204 and await history_of(admin) == [])
    finally:
        await fx.cleanup()
        await restore_history([ELENA, MARINA], saved_history)
        await admin.aclose()
        await manager.aclose()
    return c.finish()


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
