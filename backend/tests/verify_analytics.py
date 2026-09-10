"""Сверка аналитики с независимым подсчётом по базе.

Смысл проверки: сервис считает показатели своими запросами, а здесь те же
величины считаются заново, другими запросами, прямо по таблицам. Если цифры
разошлись — виновата одна из двух сторон, и это надо увидеть до того, как
руководитель сверит отчёт с реальностью и перестанет доверять системе.

Проверяются все пресеты периода с доски (неделя / месяц / квартал / год / свой),
границы корзин графика, разрез по менеджерам, плитки раздела «Оплаты», сверка
по реквизитам, граничные случаи (пустой период, один день, будущий год) и
сохранность копеек на всём пути от создания сделки до цифры в отчёте.

Скрипт создаёт одну сделку на 1 234,56 ₽, проводит по ней оплату и удаляет её
за собой: демо-данные после запуска остаются прежними.

Запуск: docker compose exec -T api python -m tests.verify_analytics
"""

import asyncio
import io
import math
import sys
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import text

from app.core.db import SessionLocal
from app.services.money import format_rubles

BASE = "http://localhost:8000/api/v1"
ADMIN = {"email": "elena@astra.ru", "password": "demo1234"}
MANAGER = {"email": "marina@astra.ru", "password": "demo1234"}

# Минимальный валидный PNG 1×1 — реальный файл, не заглушка с произвольными байтами.
RECEIPT_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000100000001080600000"
    "01f15c4890000000a4944415478da6360000002000155ff2ba00000000049454e44ae426082"
)

ok: list[str] = []
bad: list[str] = []


def check(name: str, expected: Any, actual: Any, hint: str = "") -> bool:
    if expected == actual:
        ok.append(name)
        print(f"  ✓ {name}: {actual}")
        return True
    bad.append(f"{name}: ожидалось {expected}, получено {actual}. {hint}")
    print(f"  ✗ {name}: ожидалось {expected}, получено {actual} {hint}")
    return False


def close_enough(name: str, expected: float | None, actual: float | None, tol: float = 1.0) -> None:
    if expected is None and actual is None:
        ok.append(name)
        print(f"  ✓ {name}: оба пусты")
        return
    if expected is None or actual is None:
        bad.append(f"{name}: одно пусто — ожидалось {expected}, получено {actual}")
        print(f"  ✗ {name}: ожидалось {expected}, получено {actual}")
        return
    if abs(expected - actual) <= tol:
        ok.append(name)
        print(f"  ✓ {name}: {actual} (свой расчёт {expected})")
    else:
        bad.append(f"{name}: ожидалось ~{expected}, получено {actual}")
        print(f"  ✗ {name}: ожидалось ~{expected}, получено {actual}")


DEFAULT_TZ = ZoneInfo("Europe/Moscow")


def bounds(date_from: date, date_to: date) -> tuple[datetime, datetime]:
    """Границы периода — по часовому поясу организации (в демо-данных не
    переопределён, значит действует умолчание "Europe/Moscow"), верхняя —
    исключающая. Считается независимо от сервиса, через ZoneInfo напрямую."""
    return (
        datetime.combine(date_from, time.min, tzinfo=DEFAULT_TZ).astimezone(UTC),
        datetime.combine(date_to + timedelta(days=1), time.min, tzinfo=DEFAULT_TZ).astimezone(UTC),
    )


def numbers_are_sane(name: str, payload: Any) -> None:
    """Ни NaN, ни бесконечности: в JSON они пролезают молча и ломают интерфейс."""
    broken: list[str] = []

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                walk(value, f"{path}.{key}" if path else str(key))
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, f"{path}[{index}]")
        elif isinstance(node, float) and not math.isfinite(node):
            broken.append(f"{path}={node}")

    walk(payload, "")
    check(f"{name}: чисел вида NaN/inf нет", [], broken)


# ------------------------------------------------------------- независимый счёт


async def sql_sales(
    db: Any, lo: datetime, hi: datetime, seller: int | None = None
) -> tuple[int, int]:
    clause = "" if seller is None else " and sold_by_id = :uid"
    row = (
        await db.execute(
            text(
                "select coalesce(sum(total_amount),0) s, count(*) c from deals "
                "where status='paid' and paid_at >= :lo and paid_at < :hi" + clause
            ),
            {"lo": lo, "hi": hi, "uid": seller},
        )
    ).one()
    return int(row.s), int(row.c)


async def sql_active(db: Any, lo: datetime, hi: datetime) -> int:
    return int(
        (
            await db.execute(
                text(
                    "select count(distinct conversation_id) from messages "
                    "where deleted_at is null and created_at >= :lo and created_at < :hi"
                ),
                {"lo": lo, "hi": hi},
            )
        ).scalar_one()
    )


async def sql_new_clients(db: Any, lo: datetime, hi: datetime) -> int:
    return int(
        (
            await db.execute(
                text(
                    "select count(*) from clients where deleted_at is null "
                    "and first_contact_at >= :lo and first_contact_at < :hi"
                ),
                {"lo": lo, "hi": hi},
            )
        ).scalar_one()
    )


# Независимый расчёт времени ответа: для каждого входящего, начавшего ожидание,
# ближайшее следующее исходящее ищется подзапросом, а не оконной функцией, —
# иначе повторилась бы та же ошибка, что и в сервисе, и сверка ничего бы не дала.
WAITS_SQL = """
select avg(extract(epoch from (nxt - m.created_at)))::numeric
from messages m
cross join lateral (
    select min(o.created_at) as nxt from messages o
    where o.conversation_id = m.conversation_id
      and o.direction = 'out' and o.is_internal = false and o.deleted_at is null
      and (o.created_at, o.id) > (m.created_at, m.id)
) n
where m.direction = 'in' and m.is_internal = false and m.deleted_at is null
  and m.created_at >= :lo and m.created_at < :hi
  and n.nxt is not null
  and not exists (
    select 1 from messages p
    where p.conversation_id = m.conversation_id
      and p.direction = 'in' and p.is_internal = false and p.deleted_at is null
      and (p.created_at, p.id) < (m.created_at, m.id)
      and not exists (
        select 1 from messages q
        where q.conversation_id = m.conversation_id
          and q.direction = 'out' and q.is_internal = false and q.deleted_at is null
          and (q.created_at, q.id) > (p.created_at, p.id)
          and (q.created_at, q.id) < (m.created_at, m.id)
      )
  )
"""


async def sql_avg_response(db: Any, lo: datetime, hi: datetime) -> float | None:
    value = (await db.execute(text(WAITS_SQL), {"lo": lo, "hi": hi})).scalar_one_or_none()
    return None if value is None else round(float(value))


def expected_buckets(date_from: date, date_to: date, granularity: str) -> int:
    if granularity == "day":
        return (date_to - date_from).days + 1
    if granularity == "week":
        first = date_from - timedelta(days=date_from.weekday())
        last = date_to - timedelta(days=date_to.weekday())
        return (last - first).days // 7 + 1
    return (date_to.year - date_from.year) * 12 + date_to.month - date_from.month + 1


# --------------------------------------------------------------- блоки проверок


async def check_period(
    api: httpx.AsyncClient, db: Any, title: str, since: date, until: date
) -> None:
    """Сводка за период против независимого пересчёта, включая дельты."""
    print(f"\nСводка · {title} ({since} — {until})")
    params = {"date_from": since.isoformat(), "date_to": until.isoformat()}
    overview = (await api.get(f"{BASE}/stats/overview", params=params)).json()
    numbers_are_sane(f"сводка · {title}", overview)

    lo, hi = bounds(since, until)
    span = hi - lo
    amount, count = await sql_sales(db, lo, hi)
    prev_amount, prev_count = await sql_sales(db, lo - span, lo)

    check(f"{title}: сумма продаж", amount, overview["sales_amount"])
    check(f"{title}: число продаж", count, overview["sales_count"])
    close_enough(
        f"{title}: прирост суммы, %",
        round((amount - prev_amount) / prev_amount * 100, 1) if prev_amount else None,
        overview["sales_amount_delta_pct"],
        0.11,
    )
    check(
        f"{title}: прирост числа продаж",
        count - prev_count if prev_count else None,
        overview["sales_count_delta"],
    )
    check(
        f"{title}: чатов в работе",
        await sql_active(db, lo, hi),
        overview["active_conversations"],
    )
    check(f"{title}: новых клиентов", await sql_new_clients(db, lo, hi), overview["new_clients"])
    close_enough(
        f"{title}: среднее время ответа, сек",
        await sql_avg_response(db, lo, hi),
        overview["avg_response_seconds"],
        2,
    )

    # «Ждут оплаты» — состояние на сейчас, период на него не действует.
    awaiting = (
        await db.execute(
            text(
                "select coalesce(sum(total_amount),0) s, "
                "count(*) c from deals where status='awaiting'"
            )
        )
    ).one()
    check(f"{title}: сумма ожидающих оплаты", int(awaiting.s), overview["awaiting_amount"])
    check(f"{title}: число ожидающих оплаты", int(awaiting.c), overview["awaiting_count"])


async def check_series(
    api: httpx.AsyncClient, db: Any, title: str, since: date, until: date, granularity: str
) -> None:
    """График: сумма по точкам, границы корзин и честность подписей."""
    print(f"\nГрафик · {title} · {granularity} ({since} — {until})")
    params = {
        "date_from": since.isoformat(),
        "date_to": until.isoformat(),
        "granularity": granularity,
    }
    payload = (await api.get(f"{BASE}/stats/series", params=params)).json()
    if "points" not in payload:
        check(f"{title}/{granularity}: ряд получен", "points", payload)
        return
    numbers_are_sane(f"график · {title}/{granularity}", payload)
    points = payload["points"]

    lo, hi = bounds(since, until)
    amount, count = await sql_sales(db, lo, hi)
    check(
        f"{title}/{granularity}: сумма по точкам = итог периода",
        amount,
        sum(p["amount"] for p in points),
    )
    check(
        f"{title}/{granularity}: продаж по точкам = итог периода",
        count,
        sum(p["count"] for p in points),
    )
    check(
        f"{title}/{granularity}: число корзин",
        expected_buckets(since, until, granularity),
        len(points),
    )
    if not points:
        return

    # Начало корзины обязано быть началом недели/месяца, иначе подпись сдвинута.
    starts = [date.fromisoformat(p["date"]) for p in points]
    if granularity == "week":
        check(
            f"{title}/{granularity}: все корзины начинаются с понедельника",
            [],
            [str(d) for d in starts if d.weekday() != 0],
        )
    if granularity == "month":
        check(
            f"{title}/{granularity}: все корзины начинаются с первого числа",
            [],
            [str(d) for d in starts if d.day != 1],
        )
    check(f"{title}/{granularity}: точки идут по возрастанию", sorted(starts), starts)

    # Подпись строится по period_start/period_end — они и проверяются.
    heads = [date.fromisoformat(p["period_start"]) for p in points]
    tails = [date.fromisoformat(p["period_end"]) for p in points]
    check(f"{title}/{granularity}: первая корзина начинается с начала периода", since, heads[0])
    check(f"{title}/{granularity}: последняя корзина кончается концом периода", until, tails[-1])
    check(
        f"{title}/{granularity}: корзины стыкуются без дыр и нахлёстов",
        [],
        [
            f"{tails[i]} → {heads[i + 1]}"
            for i in range(len(points) - 1)
            if heads[i + 1] != tails[i] + timedelta(days=1)
        ],
    )
    check(
        f"{title}/{granularity}: подписанные границы не выходят за период",
        [],
        [f"{h}..{t}" for h, t in zip(heads, tails, strict=True) if h < since or t > until],
    )

    # Главное: столбик обязан равняться тому, что заработано между его подписями.
    # Без этого подпись «13–19 июл» может стоять над суммой за 15–19 июля.
    mismatched: list[str] = []
    for point, head, tail in zip(points, heads, tails, strict=True):
        real_amount, real_count = await sql_sales(db, *bounds(head, tail))
        if (real_amount, real_count) != (point["amount"], point["count"]):
            mismatched.append(
                f"{head}..{tail}: в базе {real_amount}/{real_count}, "
                f"в графике {point['amount']}/{point['count']}"
            )
    check(f"{title}/{granularity}: каждый столбик равен своей подписи", [], mismatched)


async def check_managers(
    api: httpx.AsyncClient, db: Any, title: str, since: date, until: date
) -> None:
    print(f"\nРазрез по менеджерам · {title} ({since} — {until})")
    params = {"date_from": since.isoformat(), "date_to": until.isoformat()}
    rows = (await api.get(f"{BASE}/stats/managers", params=params)).json()
    numbers_are_sane(f"менеджеры · {title}", rows)

    lo, hi = bounds(since, until)
    amount, count = await sql_sales(db, lo, hi)
    check(f"{title}: сумма по менеджерам = общая", amount, sum(r["sales_amount"] for r in rows))
    check(f"{title}: продаж по менеджерам = всего", count, sum(r["sales_count"] for r in rows))

    for row in rows:
        expected_amount, expected_count = await sql_sales(db, lo, hi, row["user"]["id"])
        check(
            f"{title}: продажи · {row['user']['full_name']}",
            expected_amount,
            row["sales_amount"],
        )
        check(f"{title}: продаж · {row['user']['full_name']}", expected_count, row["sales_count"])

    # Продавец мог быть кем угодно — руководителем, отключённым сотрудником.
    # Если его строки в разрезе нет, деньги есть в итоге и ни у кого в разбивке.
    sellers = {
        int(uid)
        for (uid,) in (
            await db.execute(
                text(
                    "select distinct sold_by_id from deals "
                    "where status='paid' and paid_at >= :lo and paid_at < :hi"
                ),
                {"lo": lo, "hi": hi},
            )
        ).all()
    }
    check(
        f"{title}: в разрезе есть все, кому засчитаны продажи",
        [],
        sorted(sellers - {r["user"]["id"] for r in rows}),
    )


async def check_deals_tiles(
    api: httpx.AsyncClient, db: Any, title: str, since: date, until: date
) -> None:
    print(f"\nПлитки раздела «Оплаты» · {title} ({since} — {until})")
    params = {"date_from": since.isoformat(), "date_to": until.isoformat()}
    summary = (await api.get(f"{BASE}/deals/summary", params=params)).json()
    by_requisite = (await api.get(f"{BASE}/deals/by-requisite", params=params)).json()
    numbers_are_sane(f"плитки · {title}", summary)
    numbers_are_sane(f"реквизиты · {title}", by_requisite)

    lo, hi = bounds(since, until)
    amount, count = await sql_sales(db, lo, hi)
    check(f"{title}: оплачено за период", amount, summary["paid_amount"])
    check(f"{title}: сделок оплачено", count, summary["paid_count"])

    # Плитка подписана «клиентов с оплатами» — считаем оплативших, а не всех,
    # кому выставляли счёт.
    clients = int(
        (
            await db.execute(
                text(
                    "select count(distinct client_id) from deals "
                    "where status='paid' and paid_at >= :lo and paid_at < :hi"
                ),
                {"lo": lo, "hi": hi},
            )
        ).scalar_one()
    )
    check(f"{title}: клиентов с оплатами", clients, summary["clients_with_deals"])

    check(
        f"{title}: сверка по реквизитам сходится с оплаченным",
        amount,
        sum(r["amount"] for r in by_requisite),
    )
    check(
        f"{title}: сделок в сверке по реквизитам",
        count,
        sum(r["count"] for r in by_requisite),
    )
    # Разрез строится по фактическому счёту поступления, а не по счёту в письме.
    rows = (
        await db.execute(
            text(
                "select coalesce(paid_to_requisite_id, requisite_id) as rid, "
                "coalesce(sum(total_amount),0) s, count(*) c from deals "
                "where status='paid' and paid_at >= :lo and paid_at < :hi group by 1"
            ),
            {"lo": lo, "hi": hi},
        )
    ).all()
    expected = {r.rid: (int(r.s), int(r.c)) for r in rows}
    actual = {r["requisite_id"]: (r["amount"], r["count"]) for r in by_requisite}
    check(f"{title}: суммы по каждому реквизиту", expected, actual)


async def check_money_roundtrip(api: httpx.AsyncClient, db: Any, admin_id: int) -> None:
    """Копейки не теряются нигде: ни в сделке, ни в отчёте, ни в подписи.

    Заодно это единственный способ проверить продажу, засчитанную руководителю:
    в демо-данных все оплаты числятся за менеджерами.
    """
    print("\nКопейки: сделка на 1 234,56 ₽ от руководителя")
    kopecks = 123_456
    # Разделитель разрядов — неразрывный пробел (U+00A0), не обычный: сумма не
    # должна переноситься по строке между «1» и «234». Строим ожидание из тех
    # же частей, что и сам format_rubles, а не подставляем текст руками.
    check("формат суммы не теряет копейки", "1 234,56 ₽", format_rubles(kopecks))

    # date.today() — по системному времени контейнера (UTC), а не по поясу
    # организации: с 21:00 до 24:00 UTC (00:00–03:00 по Москве) это уже вчера
    # для сервера и не совпадает с тем, что сервер сам считает «сегодня».
    today = datetime.now(DEFAULT_TZ).date()
    params = {"date_from": today.isoformat(), "date_to": today.isoformat()}
    month = {"date_from": today.replace(day=1).isoformat(), "date_to": today.isoformat()}

    async def snapshot() -> dict[str, int]:
        overview = (await api.get(f"{BASE}/stats/overview", params=params)).json()
        summary = (await api.get(f"{BASE}/deals/summary", params=params)).json()
        requisites = (await api.get(f"{BASE}/deals/by-requisite", params=params)).json()
        managers = (await api.get(f"{BASE}/stats/managers", params=params)).json()
        series = (
            await api.get(f"{BASE}/stats/series", params={**params, "granularity": "day"})
        ).json()
        mine = (
            await api.get(f"{BASE}/stats/overview", params={**params, "user_id": admin_id})
        ).json()
        me = (await api.get(f"{BASE}/auth/me")).json()
        return {
            "сводка": overview["sales_amount"],
            "плитка «оплачено»": summary["paid_amount"],
            "сверка по реквизитам": sum(r["amount"] for r in requisites),
            "разрез по менеджерам": sum(r["sales_amount"] for r in managers),
            "строка руководителя": sum(
                r["sales_amount"] for r in managers if r["user"]["id"] == admin_id
            ),
            "график за день": sum(p["amount"] for p in series["points"]),
            "разрез по себе": mine["sales_amount"],
            "продажи за месяц в профиле": me["month_sales_amount"],
        }

    conversation_id = int(
        (
            await db.execute(
                text(
                    "select c.id from conversations c "
                    "join telegram_accounts a on a.id = c.account_id "
                    "where a.deleted_at is null order by c.id limit 1"
                )
            )
        ).scalar_one()
    )
    requisite_id = int(
        (
            await db.execute(
                text(
                    "select id from payment_requisites "
                    "where is_active and deleted_at is null order by id limit 1"
                )
            )
        ).scalar_one()
    )

    before = await snapshot()
    created = await api.post(
        f"{BASE}/deals",
        json={
            "conversation_id": conversation_id,
            "payment_method": "requisites",
            "requisite_id": requisite_id,
            "intro_text": "Проверка копеек",
            "items": [{"name": "Сверка копеек", "amount": kopecks}],
        },
    )
    if created.status_code != 201:
        check("сделка на 1 234,56 ₽ создана", 201, created.status_code, created.text)
        return
    deal = created.json()
    deal_id = deal["id"]
    try:
        check("сумма сделки в копейках", kopecks, deal["total_amount"])
        await api.post(f"{BASE}/deals/{deal_id}/send")
        uploaded = (
            await api.post(
                f"{BASE}/files/upload",
                files={"file": ("чек.png", io.BytesIO(RECEIPT_PNG), "image/png")},
            )
        ).json()
        paid = await api.post(
            f"{BASE}/deals/{deal_id}/pay",
            json={
                "receipt_upload_key": uploaded["upload_key"],
                "receipt_file_name": uploaded["file_name"],
                "receipt_mime_type": uploaded["mime_type"],
                "receipt_size_bytes": uploaded["size_bytes"],
                "paid_to_requisite_id": requisite_id,
            },
        )
        check("оплата подтверждена", 200, paid.status_code, paid.text)
        check("сумма после оплаты не изменилась", kopecks, paid.json()["total_amount"])
        stored = int(
            (
                await db.execute(
                    text("select total_amount from deals where id=:id"), {"id": deal_id}
                )
            ).scalar_one()
        )
        check("в базе ровно 123456 копеек", kopecks, stored)
        check(
            "продажа засчитана тому, кто провёл оплату",
            admin_id,
            int(
                (
                    await db.execute(
                        text("select sold_by_id from deals where id=:id"), {"id": deal_id}
                    )
                ).scalar_one()
            ),
        )

        after = await snapshot()
        for name, value in after.items():
            check(f"копейки дошли до «{name}»", before[name] + kopecks, value)

        month_stats = (
            await api.get(f"{BASE}/stats/overview", params={**month, "user_id": admin_id})
        ).json()
        users = (await api.get(f"{BASE}/users", params={"limit": 50})).json()["items"]
        me_row = next((u for u in users if u["id"] == admin_id), None)
        check(
            "продажи руководителя за месяц совпадают со списком сотрудников",
            month_stats["sales_amount"],
            (me_row or {}).get("month_sales_amount"),
        )
    finally:
        message_id = (
            await db.execute(
                text("select sent_message_id from deals where id=:id"), {"id": deal_id}
            )
        ).scalar_one_or_none()
        conv_id = (
            await db.execute(
                text("select conversation_id from deals where id=:id"), {"id": deal_id}
            )
        ).scalar_one_or_none()
        await db.execute(
            text("update deals set sent_message_id = null where id=:id"), {"id": deal_id}
        )
        await db.execute(
            text("delete from notifications where entity_type='deal' and entity_id=:id"),
            {"id": deal_id},
        )
        await db.execute(
            text("delete from event_log where entity_type='deal' and entity_id=:id"),
            {"id": deal_id},
        )
        await db.execute(text("delete from payment_events where deal_id=:id"), {"id": deal_id})
        await db.execute(text("delete from deals where id=:id"), {"id": deal_id})
        if message_id is not None:
            # Шлюз лочит сначала outbox, потом messages (обрабатывая отправку).
            # Удаление messages первым — с каскадом на outbox — лочит их в
            # обратном порядке и может словить deadlock с ещё не завершившейся
            # отправкой. Чистим в том же порядке, что и шлюз.
            await db.execute(text("delete from outbox where message_id=:id"), {"id": message_id})
            await db.execute(text("delete from messages where id=:id"), {"id": message_id})
        if conv_id is not None:
            await db.execute(
                text(
                    "update conversations c set last_message_at = s.last_at, "
                    "last_manager_message_at = s.last_out from ("
                    "  select max(created_at) last_at, "
                    "         max(created_at) filter (where direction='out') last_out "
                    "  from messages where conversation_id = :cid and deleted_at is null"
                    ") s where c.id = :cid"
                ),
                {"cid": conv_id},
            )
        await db.commit()
        print("  · тестовая сделка удалена, демо-данные восстановлены")


async def main() -> int:  # noqa: PLR0915
    # По поясу организации, не по системному времени контейнера (UTC) — иначе
    # с 21:00 до 24:00 UTC (00:00–03:00 по Москве) «сегодня» здесь и «сегодня»
    # на сервере — разные календарные дни, и сравнения с сервером расходятся
    # ровно на данные из этого узкого окна.
    today = datetime.now(DEFAULT_TZ).date()
    month_start = today.replace(day=1)

    async with httpx.AsyncClient(timeout=60) as api, SessionLocal() as db:
        if (await api.post(f"{BASE}/auth/login", json=ADMIN)).status_code != 200:
            print("Вход не удался — дальше смысла нет.")
            return 1
        me = (await api.get(f"{BASE}/auth/me")).json()
        admin_id = int(me["id"])

        periods: list[tuple[str, date, date]] = [
            ("неделя", today - timedelta(days=6), today),
            ("месяц", month_start, today),
            ("квартал", today - timedelta(days=89), today),
            ("год", date(today.year, 1, 1), date(today.year, 12, 31)),
            ("произвольный", today - timedelta(days=120), today - timedelta(days=30)),
            ("один день", today - timedelta(days=14), today - timedelta(days=14)),
            ("будущий год", date(today.year + 1, 1, 1), date(today.year + 1, 12, 31)),
            ("до появления данных", date(1900, 1, 1), date(1900, 12, 31)),
        ]
        for title, since, until in periods:
            await check_period(api, db, title, since, until)

        print("\nПериод по умолчанию — текущий месяц")
        default = (await api.get(f"{BASE}/stats/overview")).json()
        explicit = (
            await api.get(
                f"{BASE}/stats/overview",
                params={"date_from": month_start.isoformat(), "date_to": today.isoformat()},
            )
        ).json()
        check("сводка без параметров = сводка за текущий месяц", explicit, default)

        for title, since, until, granularity in [
            ("месяц", month_start, today, "day"),
            ("неделя", today - timedelta(days=6), today, "day"),
            ("один день", today, today, "day"),
            ("месяц", month_start, today, "week"),
            ("квартал", today - timedelta(days=89), today, "week"),
            ("год", date(today.year, 1, 1), date(today.year, 12, 31), "week"),
            ("квартал", today - timedelta(days=89), today, "month"),
            ("год", date(today.year, 1, 1), date(today.year, 12, 31), "month"),
            # Периоды, у которых край недели и край месяца заведомо обрезаны.
            ("рваные края", today - timedelta(days=49), today - timedelta(days=18), "week"),
            ("рваные края", today - timedelta(days=49), today - timedelta(days=18), "month"),
            ("будущий год", date(today.year + 1, 1, 1), date(today.year + 1, 12, 31), "month"),
        ]:
            await check_series(api, db, title, since, until, granularity)

        print("\nДни на длинном периоде закрыты — правило с доски")
        long_daily = await api.get(
            f"{BASE}/stats/series",
            params={
                "date_from": (today - timedelta(days=120)).isoformat(),
                "date_to": today.isoformat(),
                "granularity": "day",
            },
        )
        check("период больше двух месяцев по дням не строится", 422, long_daily.status_code)
        check(
            "период начинается позже, чем кончается",
            422,
            (
                await api.get(
                    f"{BASE}/stats/overview",
                    params={
                        "date_from": today.isoformat(),
                        "date_to": (today - timedelta(days=1)).isoformat(),
                    },
                )
            ).status_code,
        )

        for title, since, until in [
            ("месяц", month_start, today),
            ("год", date(today.year, 1, 1), date(today.year, 12, 31)),
            ("будущий год", date(today.year + 1, 1, 1), date(today.year + 1, 12, 31)),
        ]:
            await check_managers(api, db, title, since, until)
            await check_deals_tiles(api, db, title, since, until)

        print("\nСтатистика против раздела оплат — одна цифра на двух экранах")
        period = {"date_from": month_start.isoformat(), "date_to": today.isoformat()}
        overview = (await api.get(f"{BASE}/stats/overview", params=period)).json()
        summary = (await api.get(f"{BASE}/deals/summary", params=period)).json()
        check(
            "сумма продаж совпадает с разделом оплат",
            overview["sales_amount"],
            summary["paid_amount"],
        )
        check(
            "число продаж совпадает с разделом оплат",
            overview["sales_count"],
            summary["paid_count"],
        )
        check(
            "ждут оплаты — совпадает со статистикой",
            overview["awaiting_amount"],
            summary["awaiting_amount"],
        )

        print("\nСчётчики чатов")
        counters = (await api.get(f"{BASE}/conversations/counters")).json()
        total = (await db.execute(text("select count(*) from conversations"))).scalar_one()
        awaiting = (
            await db.execute(
                text("select count(*) from conversations where awaiting_reply_since is not null")
            )
        ).scalar_one()
        over = (
            await db.execute(
                text(
                    # Просмотренные диалоги в баннер не идут: ТЗ п. 2.1 —
                    # напоминание срабатывает один раз.
                    "select count(*) from conversations "
                    "where awaiting_reply_since is not null "
                    "and awaiting_reply_since < now() - interval '30 minutes' "
                    "and (awaiting_seen_at is null or awaiting_seen_at < awaiting_reply_since)"
                )
            )
        ).scalar_one()
        awaiting_pay = (
            await db.execute(
                text("select count(distinct conversation_id) from deals where status='awaiting'")
            )
        ).scalar_one()
        check("всего чатов", int(total), counters["total"])
        check("ждут ответа", int(awaiting), counters["awaiting"])
        check("ждут дольше порога", int(over), counters["over_threshold"])
        check("ожидают оплаты", int(awaiting_pay), counters["awaiting_payment"])

        print("\nСуммы в карточке клиента")
        clients = (await api.get(f"{BASE}/clients", params={"limit": 5, "sort": "amount"})).json()
        for row_client in clients["items"][:3]:
            paid = (
                await db.execute(
                    text(
                        "select coalesce(sum(total_amount),0) s, count(*) c from deals "
                        "where client_id=:cid and status='paid'"
                    ),
                    {"cid": row_client["id"]},
                )
            ).one()
            check(f"оплачено · {row_client['name']}", int(paid.s), row_client["paid_amount"])
            check(f"оплат · {row_client['name']}", int(paid.c), row_client["paid_count"])

        print("\nКлиент без единой сделки")
        empty_client = (
            await db.execute(
                text(
                    "select c.id from clients c left join deals d on d.client_id = c.id "
                    "where c.deleted_at is null and d.id is null order by c.id limit 1"
                )
            )
        ).scalar_one_or_none()
        if empty_client is None:
            print("  · таких клиентов в базе нет, проверять нечего")
        else:
            card = (await api.get(f"{BASE}/clients/{int(empty_client)}")).json()
            numbers_are_sane("карточка клиента без сделок", card)
            check("клиент без сделок: оплачено", 0, card["paid_amount"])
            check("клиент без сделок: число оплат", 0, card["paid_count"])
            check("клиент без сделок: ждут оплаты", 0, card["awaiting_amount"])
            deals = (
                await api.get(f"{BASE}/deals", params={"client_id": int(empty_client)})
            ).json()
            check("клиент без сделок: список оплат пуст", [], deals["items"])
            tiles = (
                await api.get(f"{BASE}/deals/summary", params={"client_id": int(empty_client)})
            ).json()
            check(
                "клиент без сделок: плитки нулевые",
                (0, 0, 0),
                (tiles["paid_amount"], tiles["paid_count"], tiles["clients_with_deals"]),
            )

        await check_money_roundtrip(api, db, admin_id)

        print("\nРабочие часы влияют на метрики, а не только на настройку")
        # Две независимые реализации одного расчёта: функция в базе и код на Python.
        # Расхождение означало бы, что список чатов и статистика считают по-разному.
        from datetime import time as _time

        from app.services.worktime import working_seconds

        sample = [
            # клиент написал ночью, ответили утром — ждал он двадцать минут, а не десять часов
            (datetime(2026, 6, 1, 20, 40, tzinfo=UTC), datetime(2026, 6, 2, 7, 5, tzinfo=UTC)),
            # выходные целиком выпадают
            (datetime(2026, 6, 5, 15, 0, tzinfo=UTC), datetime(2026, 6, 8, 8, 0, tzinfo=UTC)),
            # внутри рабочего дня расчёт совпадает с календарным
            (datetime(2026, 6, 3, 8, 0, tzinfo=UTC), datetime(2026, 6, 3, 9, 30, tzinfo=UTC)),
        ]
        for start_ts, end_ts in sample:
            sql_value = await db.scalar(
                text("select astra_working_seconds(:a,:b,:d,:s,:e,:tz)"),
                {
                    "a": start_ts,
                    "b": end_ts,
                    "d": [1, 2, 3, 4, 5],
                    "s": _time(10, 0),
                    "e": _time(19, 0),
                    "tz": "Europe/Moscow",
                },
            )
            py_value = working_seconds(
                start_ts, end_ts, [1, 2, 3, 4, 5], _time(10, 0), _time(19, 0), "Europe/Moscow"
            )
            check(
                f"расчёт в базе и в коде совпал ({start_ts:%d.%m %H:%M})",
                int(py_value),
                int(float(sql_value)),
            )

        quarter = {
            "date_from": (today - timedelta(days=89)).isoformat(),
            "date_to": today.isoformat(),
        }
        before_hours = (await api.get(f"{BASE}/stats/overview", params=quarter)).json()
        await api.patch(
            f"{BASE}/settings",
            json={
                "working_hours_enabled": True,
                "working_hours_start": "10:00",
                "working_hours_end": "19:00",
                "working_days": [1, 2, 3, 4, 5],
            },
        )
        after_hours = (await api.get(f"{BASE}/stats/overview", params=quarter)).json()
        await api.patch(f"{BASE}/settings", json={"working_hours_enabled": False})
        restored = (await api.get(f"{BASE}/stats/overview", params=quarter)).json()
        calendar_value = before_hours.get("avg_response_seconds")
        hours_value = after_hours.get("avg_response_seconds")
        # Ожидание в рабочих часах не может быть длиннее календарного. Строгое
        # неравенство здесь не проверяем: если все ответы пришлись на рабочий день,
        # цифры совпадут — и это правильный ответ, а не поломка.
        check(
            "рабочие часы не удлиняют ожидание",
            True,
            calendar_value is None or hours_value is None or hours_value <= calendar_value,
        )
        check(
            "выключение возвращает прежнюю цифру",
            calendar_value,
            restored.get("avg_response_seconds"),
        )

    print("\nПрава: менеджер видит только своё")

    async with httpx.AsyncClient(timeout=60) as mgr, SessionLocal() as db:
        await mgr.post(f"{BASE}/auth/login", json=MANAGER)
        manager_id = int((await mgr.get(f"{BASE}/auth/me")).json()["id"])
        period = {"date_from": month_start.isoformat(), "date_to": today.isoformat()}
        lo, hi = bounds(month_start, today)
        m_overview = (await mgr.get(f"{BASE}/stats/overview", params=period)).json()
        amount, count = await sql_sales(db, lo, hi, manager_id)
        check("продажи менеджера — только свои", amount, m_overview["sales_amount"])
        check("число продаж менеджера — только свои", count, m_overview["sales_count"])
        check(
            "разрез по менеджерам закрыт менеджеру",
            403,
            (await mgr.get(f"{BASE}/stats/managers", params=period)).status_code,
        )
        check(
            "чужой разрез в сводке закрыт менеджеру",
            422,
            (
                await mgr.get(
                    f"{BASE}/stats/overview", params={**period, "user_id": manager_id + 1000}
                )
            ).status_code,
        )
        check(
            "чужой разрез в графике закрыт менеджеру",
            422,
            (
                await mgr.get(
                    f"{BASE}/stats/series",
                    params={**period, "granularity": "day", "user_id": manager_id + 1000},
                )
            ).status_code,
        )
        own_response = await mgr.get(
            f"{BASE}/stats/overview", params={**period, "user_id": manager_id}
        )
        own = own_response.json()
        check("свой разрез менеджеру доступен", m_overview["sales_amount"], own["sales_amount"])

    print(f"\nИтог: {len(ok)} сошлось, {len(bad)} разошлось")
    for line in bad:
        print(f"  — {line}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
