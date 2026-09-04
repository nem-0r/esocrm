"""Проверка выгрузок: файл открывается в Excel и совпадает с тем, что на экране.

Выгрузку отдают бухгалтеру и партнёру, и ошибка в ней обнаруживается уже после
отправки. Поэтому проверяем не «ответ 200», а сам файл, байт за байтом:

* кодировка — UTF-8 с BOM, иначе Excel на Windows показывает кракозябры;
* разделитель — `;`, иначе в русской локали строка слипается в одну ячейку;
* суммы — с копейками, через запятую, без экспоненты и потерянных нулей;
* даты — без миллисекунд и без «T», Excel обязан узнать в них дату;
* ячейка не начинается с `=`, `+`, `@` — иначе Excel исполнит её как формулу;
* имя файла в заголовке переживает кириллицу (RFC 5987);
* права: менеджер выгружает только своё, руководитель — всё;
* строки файла совпадают со строками списка при тех же фильтрах экрана.

Проверка экранирования требует поля с кавычкой, точкой с запятой и переносом
строки. Такого в демо-данных нет, поэтому одному клиенту временно меняется имя
и возвращается обратно в `finally` — как и остальные проверки из `verify_*`,
которые пишут в базу.

Запуск: docker compose exec -T api python -m tests.verify_exports
"""

import asyncio
import csv
import io
import re
import sys
from datetime import date
from typing import Any
from urllib.parse import unquote

import httpx

BASE = "http://localhost:8000/api/v1"
ADMIN = {"email": "elena@astra.ru", "password": "demo1234"}
MANAGER = {"email": "marina@astra.ru", "password": "demo1234"}

BOM = b"\xef\xbb\xbf"
MONEY = re.compile(r"^-?\d+,\d{2}$")
MOMENT = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")
DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
CLOCK = re.compile(r"^\d{2}:\d{2}$")

# С этих символов Excel начинает разбирать ячейку как формулу.
FORMULA_START = ("=", "+", "@", "\t", "\r")

# Поле, в котором есть всё разом: формула, кавычка, разделитель и перенос строки.
NASTY = '=HYPERLINK("http://evil";"клик");Ч"К\nвторая строка'

ok: list[str] = []
bad: list[str] = []


def check(title: str, condition: bool, detail: str = "") -> None:
    (ok if condition else bad).append(title if condition else f"{title} — {detail}")
    print(f"  {'✓' if condition else '✗'} {title}{f' — {detail}' if detail else ''}")


def parse(response: httpx.Response) -> tuple[list[str], list[list[str]]]:
    """Файл глазами Excel: снимаем BOM, режем по `;`, кавычки разбирает csv."""
    text = response.content.decode("utf-8-sig")
    rows = [row for row in csv.reader(io.StringIO(text), delimiter=";") if row]
    return (rows[0], rows[1:]) if rows else ([], [])


def column(header: list[str], name: str, rows: list[list[str]]) -> list[str]:
    """Значения колонки по названию — индексы в тесте только запутывают."""
    index = header.index(name)
    return [row[index] for row in rows if index < len(row)]


def kopecks(cell: str) -> int:
    rub, kop = cell.split(",")
    return (-1 if rub.startswith("-") else 1) * (abs(int(rub)) * 100 + int(kop))


def deal_ids(rows: list[list[str]]) -> set[str]:
    return {row[0].removeprefix("DEAL-") for row in rows}


async def collect(client: httpx.AsyncClient, path: str, params: dict[str, Any]) -> set[str]:
    """Все id из постраничного списка — с чем сравниваем строки файла."""
    ids: set[str] = set()
    cursor: str | None = None
    while True:
        query: dict[str, Any] = {**params, "limit": 100}
        if cursor:
            query["cursor"] = cursor
        page = (await client.get(path, params=query)).json()
        ids |= {str(item["id"]) for item in page["items"]}
        cursor = page.get("next_cursor")
        if not cursor:
            return ids


def excel_checks(
    title: str,
    response: httpx.Response,
    *,
    money: list[str],
    moments: list[str],
    days: list[str] | None = None,
    clocks: list[str] | None = None,
) -> tuple[list[str], list[list[str]]]:
    """Всё, что делает файл читаемым в Excel. Одинаково для любой выгрузки."""
    print(f"\n{title}")
    header, rows = parse(response)

    check(
        "файл начинается с BOM — Excel не покажет кракозябры",
        response.content.startswith(BOM),
        response.content[:3].hex(),
    )
    check(
        "кодировка объявлена в ответе",
        "charset=utf-8" in response.headers.get("content-type", ""),
        response.headers.get("content-type", ""),
    )
    check(
        "разделитель — точка с запятой, колонки не слиплись",
        len(header) > 1 and "," not in ";".join(header),
        f"колонок: {len(header)}",
    )
    check(
        "кириллица в шапке читается",
        any(re.search("[а-яА-ЯёЁ]", name) for name in header),
        ";".join(header[:3]),
    )

    ragged = [row[0] for row in rows if len(row) != len(header)]
    check(
        "в каждой строке столько же колонок, сколько в шапке",
        not ragged,
        f"кривых строк {len(ragged)}: {ragged[:3]}",
    )

    dangerous = [
        cell
        for row in rows
        for cell in row
        if cell.startswith(FORMULA_START) or (cell.startswith("-") and not MONEY.match(cell))
    ]
    check("ни одна ячейка не начинается с формулы", not dangerous, str(dangerous[:3]))

    broken = [
        cell for name in money for cell in column(header, name, rows) if not MONEY.match(cell)
    ]
    check(
        "суммы с копейками, через запятую, без экспоненты",
        not broken,
        f"испорчено {len(broken)}: {broken[:3]}",
    )

    wrong = [
        cell
        for pattern, names in ((MOMENT, moments), (DAY, days or []), (CLOCK, clocks or []))
        for name in names
        for cell in column(header, name, rows)
        if cell and not pattern.match(cell)
    ]
    check(
        "даты читаемы: без миллисекунд, без «T», без часового пояса",
        not wrong,
        str(wrong[:3]),
    )

    check(
        "ответ отдаётся потоком, а не собирается в памяти целиком",
        "content-length" not in response.headers,
        f"content-length: {response.headers.get('content-length')}",
    )
    return header, rows


def filename_checks(title: str, response: httpx.Response, expected: str) -> None:
    header = response.headers.get("content-disposition", "")
    encoded = re.search(r"filename\*=UTF-8''([^;]+)", header)
    plain = re.search(r'filename="([^"]+)"', header)
    check(
        f"{title}: русское имя файла не ломается",
        bool(encoded) and unquote(encoded.group(1)) == expected,
        unquote(encoded.group(1)) if encoded else header,
    )
    check(
        f"{title}: есть запасное латинское имя — заголовок обязан быть latin-1",
        bool(plain) and plain.group(1).isascii() and header.isascii(),
        header,
    )


async def run() -> int:
    print("Проверка выгрузок")
    today = date.today().isoformat()

    async with (
        httpx.AsyncClient(base_url=BASE, timeout=60) as admin,
        httpx.AsyncClient(base_url=BASE, timeout=60) as manager,
    ):
        await admin.post("/auth/login", json=ADMIN)
        await manager.post("/auth/login", json=MANAGER)

        # ── Оплаты ────────────────────────────────────────────────────────
        deals = await admin.get("/deals/export")
        excel_checks(
            "Выгрузка оплат",
            deals,
            money=["Сумма (руб)"],
            moments=["Дата события", "Отправлено", "Оплачено", "Срок действия"],
        )
        filename_checks("Оплаты", deals, f"Оплаты {today}.csv")

        paid_header, paid_rows = parse(await admin.get("/deals/export", params={"status": "paid"}))
        summary = (await admin.get("/deals/summary")).json()
        total = sum(kopecks(cell) for cell in column(paid_header, "Сумма (руб)", paid_rows))
        check(
            "сумма оплат в файле сходится с плиткой над списком",
            total == summary["paid_amount"],
            f"файл {total}, плитка {summary['paid_amount']}",
        )
        check(
            "число оплаченных строк сходится с плиткой",
            len(paid_rows) == summary["paid_count"],
            f"файл {len(paid_rows)}, плитка {summary['paid_count']}",
        )

        # ── Клиенты ───────────────────────────────────────────────────────
        clients = await admin.get("/clients/export")
        _, client_rows = excel_checks(
            "Выгрузка клиентов",
            clients,
            money=["Оплачено (руб)", "Ждёт оплаты (руб)"],
            moments=["Первое обращение"],
            days=["Дата рождения"],
            clocks=["Время рождения"],
        )
        filename_checks("Клиенты", clients, f"Клиенты {today}.csv")

        # ── Файл совпадает с экраном ──────────────────────────────────────
        print("\nФайл совпадает с тем, что человек видит на экране")
        for client, who in ((admin, "руководитель"), (manager, "менеджер")):
            _, rows = parse(await client.get("/deals/export"))
            on_screen = await collect(client, "/deals", {})
            check(
                f"оплаты: файл = список ({who})",
                deal_ids(rows) == on_screen,
                f"лишние {sorted(deal_ids(rows) - on_screen)[:3]}, "
                f"потерянные {sorted(on_screen - deal_ids(rows))[:3]}",
            )

            _, rows = parse(
                await client.get("/clients/export", params={"date_from": "2000-01-01"})
            )
            in_file = {row[0] for row in rows}
            on_screen = await collect(client, "/clients", {})
            check(
                f"клиенты: файл = список ({who})",
                in_file == on_screen,
                f"лишние {sorted(in_file - on_screen)[:3]}, "
                f"потерянные {sorted(on_screen - in_file)[:3]}",
            )

        # Фильтры экрана обязаны доезжать до файла. Фильтр по каналу до него
        # не доезжал: на экране оплаты одного канала, в файле — все.
        print("\nФильтры экрана доезжают до файла")
        accounts = (await admin.get("/accounts")).json()
        for account in accounts if isinstance(accounts, list) else accounts["items"]:
            params = {"account_id": account["id"]}
            _, rows = parse(await admin.get("/deals/export", params=params))
            on_screen = await collect(admin, "/deals", params)
            check(
                f"канал «{account['title']}»",
                deal_ids(rows) == on_screen,
                f"в файле {len(rows)}, на экране {len(on_screen)}",
            )

        for params in ({"status": "paid"}, {"status": "cancelled"}, {"status": "awaiting"}):
            _, rows = parse(await admin.get("/deals/export", params=params))
            on_screen = await collect(admin, "/deals", params)
            check(
                f"статус {params['status']}",
                deal_ids(rows) == on_screen,
                f"в файле {len(rows)}, на экране {len(on_screen)}",
            )

        if client_rows:
            params = {"client_id": client_rows[0][0]}
            _, rows = parse(await admin.get("/deals/export", params=params))
            on_screen = await collect(admin, "/deals", params)
            check(
                "клиент",
                deal_ids(rows) == on_screen,
                f"в файле {len(rows)}, на экране {len(on_screen)}",
            )

        # ── Права ─────────────────────────────────────────────────────────
        print("\nПрава: менеджер выгружает только своё")
        for path, what in (("/deals/export", "оплат"), ("/clients/export", "клиентов")):
            _, boss_rows = parse(await admin.get(path))
            _, mine_rows = parse(await manager.get(path))
            boss = {row[0] for row in boss_rows}
            mine = {row[0] for row in mine_rows}
            check(
                f"менеджер выгружает меньше {what}, чем руководитель",
                len(mine) < len(boss),
                f"менеджер {len(mine)}, руководитель {len(boss)}",
            )
            check(
                f"в файле менеджера нет строк, которых нет у руководителя ({what})",
                mine <= boss,
                str(sorted(mine - boss)[:3]),
            )

        # ── Экранирование ─────────────────────────────────────────────────
        print("\nОпасное значение в поле не ломает файл")
        if not client_rows:
            check("есть на ком проверить экранирование", False, "в базе нет клиентов")
        else:
            victim = client_rows[0][0]
            original = (await admin.get(f"/clients/{victim}")).json()["name"]
            try:
                await admin.patch(f"/clients/{victim}", json={"display_name": NASTY})
                header, rows = parse(await admin.get("/clients/export"))
                check(
                    "файл остался разборным: столько же строк и колонок",
                    len(rows) == len(client_rows) and all(len(r) == len(header) for r in rows),
                    f"было строк {len(client_rows)}, стало {len(rows)}",
                )
                cell = next((row[1] for row in rows if row[0] == victim), "")
                check("формула обезврежена апострофом", cell.startswith("'"), repr(cell[:40]))
                check("значение не потеряно и не обрезано", cell.lstrip("'") == NASTY, repr(cell))
                check(
                    "перенос строки внутри ячейки не стал новой строкой файла",
                    "\n" in cell and len(rows) == len(client_rows),
                    repr(cell[-20:]),
                )
            finally:
                await admin.patch(f"/clients/{victim}", json={"display_name": original})
                restored = (await admin.get(f"/clients/{victim}")).json()["name"]
                check("исходное имя клиента возвращено", restored == original, restored)

    print(f"\nИтог: {len(ok)} выполнено, {len(bad)} не выполнено")
    for line in bad:
        print(f"  — {line}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
