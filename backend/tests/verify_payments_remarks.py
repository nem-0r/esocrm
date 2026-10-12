"""Замечания тестировщика по оплатам и выгрузкам (docs/18: BUG-10, L2, L3, формулы CSV).

Проверяется на работающем стенде, по настоящим HTTP-запросам и настоящему файлу:

* BUG-10 — колонка «Клиент» в выгрузках оплат и клиентов называет клиента так же, как
  экран (имя от менеджера → «имя фамилия» из Telegram → @username → id), а не числом;
  «Telegram ID» и «Имя в Telegram» остаются отдельными колонками;
* L2 — период «с» позже «по» и даты вне 1900–2100 сервер отклоняет с 422 и понятной
  причиной во всех ручках раздела (список, плитки, сверка по реквизитам, выгрузка);
* L3 — сумма больше 100 млн ₽, ноль и минус отклоняются понятной ошибкой и при создании,
  и при правке; сумма ровно на пределе проходит;
* формулы CSV по-прежнему обезврежены: ячейка не начинается с «=», «+», «-», «@».

Данные — свои: клиенты с telegram_id из зарезервированного диапазона и уникальным
префиксом в именах. Диалоги и сделки создаются на них же. Всё это удаляется и в начале
(остатки прерванного запуска), и в конце, демо-данные остаются прежними.

Запуск (под замком, см. docs/18, «Правила для всех исполнителей»):
    docker exec astra-crm-api-1 sh -c 'cd /app && python -m tests.verify_payments_remarks'
"""

import asyncio
import csv
import io
import re
import sys
import time
import uuid
from typing import Any

import httpx
from sqlalchemy import text

from app.core.db import SessionLocal
from app.models import Client, Conversation
from app.services.money import MAX_AMOUNT, format_rubles
from tests.support import ADMIN, BASE, Checks

# Зарезервированный диапазон telegram_id тестовых клиентов — по нему же идёт уборка.
ID_LO = 9_200_000_000_000
ID_HI = 9_299_999_999_999

PREFIX = f"PRM{uuid.uuid4().hex[:6]}"
USERNAME_BASE = PREFIX.lower()

FORMULA_START = ("=", "+", "-", "@", "\t", "\r")
MONEY = re.compile(r"^-?\d+,\d{2}$")


def parse(response: httpx.Response) -> tuple[list[str], list[list[str]]]:
    """Файл глазами Excel: снимаем BOM, режем по «;», кавычки разбирает csv."""
    body = response.content.decode("utf-8-sig")
    rows = [row for row in csv.reader(io.StringIO(body), delimiter=";") if row]
    return (rows[0], rows[1:]) if rows else ([], [])


def cell(header: list[str], row: list[str], name: str) -> str:
    return row[header.index(name)]


def build_cases() -> list[dict[str, Any]]:
    """Клиенты на каждую ступень лестницы имени и на каждый опасный первый символ."""
    seed = ID_LO + (int(time.time()) % 1_000_000) * 50
    cases: list[dict[str, Any]] = [
        {
            "key": "имя из Telegram",
            "display": None,
            "first": f"{PREFIX} Ксения",
            "last": "Орлова",
            "username": f"{USERNAME_BASE}_ksenia",
            "expected": f"{PREFIX} Ксения Орлова",
        },
        {
            "key": "только @username",
            "display": None,
            "first": None,
            "last": None,
            "username": f"{USERNAME_BASE}_only",
            "expected": f"@{USERNAME_BASE}_only",
        },
        {
            "key": "ничего, кроме id",
            "display": None,
            "first": None,
            "last": None,
            "username": None,
            "expected": None,  # подставим telegram_id ниже
        },
        {
            "key": "имя от менеджера важнее",
            "display": f"{PREFIX} Имя менеджера",
            "first": "Другое",
            "last": "Имя",
            "username": f"{USERNAME_BASE}_manager",
            "expected": f"{PREFIX} Имя менеджера",
        },
    ]
    for char in "=+-@":
        cases.append(
            {
                "key": f"формула «{char}»",
                "display": f'{char}{PREFIX}HYPERLINK("http://evil";"x")',
                "first": None,
                "last": None,
                "username": None,
                "expected": f'{char}{PREFIX}HYPERLINK("http://evil";"x")',
                "formula": True,
            }
        )
    for index, case in enumerate(cases):
        case["telegram_id"] = seed + index
        if case["expected"] is None:
            case["expected"] = str(case["telegram_id"])
    return cases


async def sweep() -> int:
    """Удалить всё, что оставили тестовые клиенты, — в том числе от прерванного запуска."""
    async with SessionLocal() as db:
        clients = [
            row[0]
            for row in await db.execute(
                text("select id from clients where telegram_id between :lo and :hi"),
                {"lo": ID_LO, "hi": ID_HI},
            )
        ]
        if not clients:
            return 0
        deals = [
            row[0]
            for row in await db.execute(
                text("select id from deals where client_id = any(:ids)"), {"ids": clients}
            )
        ]
        if deals:
            for stmt in (
                "delete from notifications where entity_type='deal' and entity_id = any(:ids)",
                "delete from event_log where entity_type='deal' and entity_id = any(:ids)",
                "delete from payment_events where deal_id = any(:ids)",
                "delete from deals where id = any(:ids)",
            ):
                await db.execute(text(stmt), {"ids": deals})
        await db.execute(
            text("delete from conversations where client_id = any(:ids)"), {"ids": clients}
        )
        await db.execute(text("delete from clients where id = any(:ids)"), {"ids": clients})
        await db.commit()
        return len(clients)


async def seed(cases: list[dict[str, Any]]) -> None:
    async with SessionLocal() as db:
        account_id = (
            await db.execute(
                text(
                    "select id from telegram_accounts where deleted_at is null order by id limit 1"
                )
            )
        ).scalar_one()
        for case in cases:
            client = Client(
                telegram_id=case["telegram_id"],
                tg_username=case["username"],
                tg_first_name=case["first"],
                tg_last_name=case["last"],
                display_name=case["display"],
            )
            db.add(client)
            await db.flush()
            conversation = Conversation(
                client_id=client.id, account_id=account_id, tg_chat_id=case["telegram_id"]
            )
            db.add(conversation)
            await db.flush()
            case["client_id"], case["conversation_id"] = client.id, conversation.id
        await db.commit()


def deal_body(
    case: dict[str, Any], amount: int = 123_456, name: str | None = None
) -> dict[str, Any]:
    return {
        "conversation_id": case["conversation_id"],
        "payment_method": "requisites",
        "custom_requisites_text": "Реквизиты для проверки выгрузки",
        "items": [{"name": name or f"{PREFIX} услуга", "amount": amount}],
    }


async def check_names(c: Checks, admin: httpx.AsyncClient, cases: list[dict[str, Any]]) -> None:
    c.section("BUG-10 — имя клиента в выгрузках такое же, как на экране")
    for case in cases:
        created = await admin.post("/deals", json=deal_body(case))
        if not c.check(
            f"{case['key']}: сделка создана", created.status_code == 201, created.text[:120]
        ):
            continue
        case["deal_id"] = created.json()["id"]

    for case in cases:
        if "deal_id" not in case:
            continue
        shown = (await admin.get("/deals", params={"client_id": case["client_id"]})).json()["items"]
        on_screen = shown[0]["client"]["name"] if shown else None
        c.check(
            f"{case['key']}: экран показывает ожидаемое имя",
            on_screen == case["expected"],
            f"экран: {on_screen!r}, ожидали {case['expected']!r}",
        )

        response = await admin.get("/deals/export", params={"client_id": case["client_id"]})
        header, rows = parse(response)
        if not c.check(
            f"{case['key']}: в выгрузке оплат одна строка", len(rows) == 1, f"строк: {len(rows)}"
        ):
            continue
        row = rows[0]
        name = cell(header, row, "Клиент")
        if case.get("formula"):
            c.check(
                f"{case['key']}: формула в имени обезврежена апострофом, значение цело",
                name.startswith("'") and name[1:] == case["expected"],
                repr(name[:60]),
            )
        else:
            # Имя, начинающееся с «@», как и колонка «Telegram», защищено апострофом спереди.
            c.check(
                f"{case['key']}: в выгрузке оплат «Клиент» = {case['expected']!r}",
                name.lstrip("'") == case["expected"],
                repr(name),
            )
            c.check(
                f"{case['key']}: вместо имени не подставлен числовой id",
                not (name.isdigit() and case["expected"] != str(case["telegram_id"])),
                repr(name),
            )
        c.check(
            f"{case['key']}: «Telegram ID» остаётся отдельной колонкой с id",
            cell(header, row, "Telegram ID") == str(case["telegram_id"]),
            cell(header, row, "Telegram ID"),
        )
        tg_name = " ".join(p for p in (case["first"], case["last"]) if p)
        c.check(
            f"{case['key']}: «Имя в Telegram» — только имя из Telegram",
            cell(header, row, "Имя в Telegram") == tg_name,
            repr(cell(header, row, "Имя в Telegram")),
        )

    c.section("BUG-10 — то же в выгрузке клиентов")
    response = await admin.get("/clients/export", params={"date_from": "2000-01-01"})
    header, rows = parse(response)
    by_id = {row[header.index("ID")]: row for row in rows}
    for case in cases:
        row = by_id.get(str(case["client_id"]))
        if row is None:
            c.check(f"{case['key']}: клиент есть в выгрузке клиентов", False)
            continue
        name = cell(header, row, "Имя")
        if case.get("formula"):
            c.check(
                f"{case['key']}: формула обезврежена и в выгрузке клиентов",
                name.startswith("'") and name[1:] == case["expected"],
                repr(name[:60]),
            )
        else:
            c.check(
                f"{case['key']}: «Имя» = {case['expected']!r}",
                name.lstrip("'") == case["expected"],
                repr(name),
            )
        c.check(
            f"{case['key']}: «Telegram ID» и «Имя в Telegram» отдельно",
            cell(header, row, "Telegram ID") == str(case["telegram_id"])
            and cell(header, row, "Имя в Telegram")
            == " ".join(p for p in (case["first"], case["last"]) if p),
        )


async def check_formulas(c: Checks, admin: httpx.AsyncClient, cases: list[dict[str, Any]]) -> None:
    c.section("Формулы CSV по-прежнему обезврежены")
    formula_case = next(case for case in cases if case.get("formula"))
    evil = '=HYPERLINK("http://evil";"клик")'
    created = await admin.post("/deals", json=deal_body(formula_case, name=evil))
    c.check("сделка с названием-формулой создана", created.status_code == 201, created.text[:120])

    response = await admin.get("/deals/export")
    header, rows = parse(response)
    dangerous = [
        value
        for row in rows
        for value in row
        if value.startswith(FORMULA_START) and not MONEY.match(value)
    ]
    c.check(
        "в файле оплат ни одна ячейка не начинается с формулы", not dangerous, str(dangerous[:3])
    )
    services = [
        cell(header, row, "Услуги")
        for row in rows
        if cell(header, row, "ID клиента") == str(formula_case["client_id"])
    ]
    c.check(
        "название услуги-формулы обезврежено апострофом",
        any(s.startswith("'=HYPERLINK") for s in services),
        str(services[:2]),
    )

    clients = await admin.get("/clients/export", params={"date_from": "2000-01-01"})
    _, client_rows = parse(clients)
    dangerous = [
        value
        for row in client_rows
        for value in row
        if value.startswith(FORMULA_START) and not MONEY.match(value)
    ]
    c.check(
        "в файле клиентов ни одна ячейка не начинается с формулы", not dangerous, str(dangerous[:3])
    )


async def check_period(c: Checks, admin: httpx.AsyncClient) -> None:
    c.section("L2 — неверный период отклоняется сервером")
    ok_range = {"date_from": "2026-10-01", "date_to": "2026-10-07"}
    for path in ("/deals", "/deals/summary", "/deals/by-requisite", "/deals/export"):
        c.check(
            f"{path}: нормальный период принят",
            (await admin.get(path, params=ok_range)).status_code == 200,
        )

        r = await admin.get(path, params={"date_from": "2026-10-08", "date_to": "2026-10-07"})
        error = (
            r.json().get("error", {})
            if r.headers.get("content-type", "").startswith("application/json")
            else {}
        )
        c.check(
            f"{path}: «с» позже «по» — 422 «Начало периода позже конца»",
            r.status_code == 422
            and error.get("message") == "Начало периода позже конца"
            and error.get("code") == "invalid",
            f"{r.status_code} {r.text[:100]}",
        )

        for params in (
            {"date_from": "1899-12-31"},
            {"date_to": "2101-01-01"},
            {"date_from": "0001-01-01"},
        ):
            r = await admin.get(path, params=params)
            message = (
                r.json().get("error", {}).get("message") if r.status_code == 422 else r.text[:80]
            )
            c.check(
                f"{path}: {params} — 422 про границы 1900–2100",
                r.status_code == 422 and message == "Период должен лежать между 1900 и 2100 годами",
                f"{r.status_code} {message}",
            )

    for params, title in (
        ({"date_from": "2026-10-07", "date_to": "2026-10-07"}, "один день"),
        ({"date_from": "1900-01-01", "date_to": "2100-12-31"}, "самые широкие границы"),
    ):
        r = await admin.get("/deals", params=params)
        c.check(
            f"допустимый период проходит: {title}",
            r.status_code == 200,
            f"{r.status_code} {r.text[:80]}",
        )

    r = await admin.get("/deals", params={"date_from": ""})
    c.check(
        "пустая дата не подменяется молча периодом по умолчанию",
        r.status_code == 422,
        f"{r.status_code} {r.text[:80]}",
    )


async def check_amounts(c: Checks, admin: httpx.AsyncClient, cases: list[dict[str, Any]]) -> None:
    c.section("L3 — предел суммы 100 млн ₽ и понятные ошибки")
    case = cases[0]
    limit_text = f"Сумма не больше {format_rubles(MAX_AMOUNT)}"

    over = await admin.post("/deals", json=deal_body(case, amount=MAX_AMOUNT + 1))
    message = (
        over.json().get("error", {}).get("message") if over.status_code == 422 else over.text[:80]
    )
    c.check(
        "сумма на копейку больше предела отклоняется (422)",
        over.status_code == 422,
        f"{over.status_code} {message}",
    )
    c.check(
        "текст ошибки называет предел, а не «слишком большая»", message == limit_text, repr(message)
    )

    exact = await admin.post("/deals", json=deal_body(case, amount=MAX_AMOUNT))
    c.check("сумма ровно на пределе проходит", exact.status_code == 201, exact.text[:100])

    for amount, title in ((0, "ноль"), (-100, "минус")):
        r = await admin.post("/deals", json=deal_body(case, amount=amount))
        message = r.json().get("error", {}).get("message") if r.status_code == 422 else r.text[:80]
        c.check(
            f"{title}: 422 «Сумма должна быть больше нуля»",
            r.status_code == 422 and message == "Сумма должна быть больше нуля",
            f"{r.status_code} {message}",
        )

    # Правка существующей сделки проверяется теми же правилами.
    draft = await admin.post("/deals", json=deal_body(case))
    if c.check("черновик для правки создан", draft.status_code == 201, draft.text[:100]):
        deal_id = draft.json()["id"]
        r = await admin.patch(
            f"/deals/{deal_id}",
            json={
                "items": [{"name": "Правка", "amount": MAX_AMOUNT + 1}],
                "comment": "проверка предела",
            },
        )
        message = r.json().get("error", {}).get("message") if r.status_code == 422 else r.text[:80]
        c.check(
            "правка суммой больше предела отклоняется тем же текстом",
            r.status_code == 422 and message == limit_text,
            f"{r.status_code} {message}",
        )
        after = (await admin.get(f"/deals/{deal_id}")).json()
        c.check(
            "после отказа состав сделки не изменился",
            after["total_amount"] == 123_456,
            str(after["total_amount"]),
        )


async def run() -> int:
    c = Checks("Оплаты и выгрузки: замечания тестировщика")
    removed = await sweep()
    if removed:
        print(f"  · убраны остатки прошлого запуска: клиентов {removed}\n")
    cases = build_cases()
    await seed(cases)
    admin = httpx.AsyncClient(base_url=BASE, timeout=60)
    login_response = await admin.post("/auth/login", json=ADMIN)
    login_response.raise_for_status()
    try:
        await check_names(c, admin, cases)
        await check_formulas(c, admin, cases)
        await check_period(c, admin)
        await check_amounts(c, admin, cases)
    finally:
        await admin.aclose()
        cleaned = await sweep()
        print(f"\n  · тестовые данные удалены: клиентов {cleaned}")
    return c.finish()


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
