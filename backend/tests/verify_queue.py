"""Очередь ответов: порядок списков, «Взять первого», одно число везде (D-34, BUG-04, BUG-05).

Что доказываем сквозь настоящий API и базу:

1. «Ждут ответа» и «Ждут >N мин» идут от самого давнего ожидания (BUG-04);
   «Все» остаётся по свежести сообщения.
2. Постраничная загрузка (курсор) не теряет и не дублирует строки, в том числе
   при одинаковом времени ожидания.
3. «Взять первого в очереди» с `skip` проходит всю очередь без повторов:
   сначала непросмотренные, потом просмотренные; когда очередь пройдена — 404
   `queue_done`. Без `skip` поведение прежнее (старый фронтенд живёт).
4. Баннер, плитка статистики и длина фильтра — одно число для руководителя и
   для менеджера: с просмотренными, ничейными и чужими диалогами, при смене
   порога и при включённых рабочих часах (BUG-05).
5. Просмотр руководителем не ставит «просмотрено»; просмотр менеджером ставит
   (гасит значок на строке), но из баннера диалог не уходит.

Данные — с префиксом `vq-queue-`, убираются за собой (и перед прогоном, если
прошлый оборвался). Настройки порога и рабочих часов возвращаются как были.

Запуск (под замком — см. docs/18, «Правила для всех исполнителей»):
    docker exec astra-crm-api-1 python -m tests.verify_queue
"""

import asyncio
import sys
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import text

from app.core.db import SessionLocal
from app.models import Client, Conversation
from tests.support import ADMIN, BASE, MANAGER, Checks, login

PREFIX = "vq-queue-"
TG_BASE = 9_270_000_000_000
SETTING_KEYS = (
    "awaiting_banner_minutes",
    "working_hours_enabled",
    "working_hours_start",
    "working_hours_end",
    "working_days",
)


# ------------------------------------------------------------ данные


async def cleanup() -> None:
    async with SessionLocal() as db:
        ids = [
            row[0]
            for row in await db.execute(
                text(
                    "select c.id from conversations c join clients cl on cl.id = c.client_id "
                    "where cl.display_name like :p"
                ),
                {"p": PREFIX + "%"},
            )
        ]
        if ids:
            await db.execute(
                text(
                    "delete from event_log where entity_type = 'conversation' "
                    "and entity_id = any(:ids)"
                ),
                {"ids": ids},
            )
            await db.execute(text("delete from conversations where id = any(:ids)"), {"ids": ids})
        await db.execute(
            text("delete from clients where display_name like :p"), {"p": PREFIX + "%"}
        )
        await db.commit()


async def discover() -> dict[str, int] | None:
    """Пользователи и три вида аккаунтов: менеджера, чужой, этап «Бот»."""
    async with SessionLocal() as db:
        users = dict(
            (
                await db.execute(
                    text(
                        "select email, id from users "
                        "where email in ('marina@astra.ru', 'anna@astra.ru')"
                    )
                )
            ).all()
        )
        marina, anna = users.get("marina@astra.ru"), users.get("anna@astra.ru")
        if not marina or not anna:
            return None

        async def pick(sql: str) -> int | None:
            return (await db.execute(text(sql), {"m": marina, "a": anna})).scalar()

        own = await pick(
            "select a.id from telegram_accounts a join account_managers am on am.account_id = a.id "
            "where am.user_id = :m and a.funnel_stage <> 'warmup' and a.deleted_at is null "
            "order by a.id limit 1"
        )
        other = await pick(
            "select a.id from telegram_accounts a where a.funnel_stage <> 'warmup' "
            "and a.deleted_at is null and not exists (select 1 from account_managers am "
            "where am.account_id = a.id and am.user_id = :m) order by a.id limit 1"
        )
        warm = await pick(
            "select a.id from telegram_accounts a where a.funnel_stage = 'warmup' "
            "and a.deleted_at is null order by a.id limit 1"
        )
    if not (own and other and warm):
        return None
    return {"marina": marina, "anna": anna, "own": own, "other": other, "warm": warm}


async def make_fixtures(ctx: dict[str, int]) -> dict[str, int]:
    """name → id диалога. Минуты — «сколько назад»; `seen` — когда менеджер открыл чат."""
    base = datetime.now(UTC).replace(microsecond=0)

    def ago(minutes: int | None) -> datetime | None:
        return None if minutes is None else base - timedelta(minutes=minutes)

    marina, anna = ctx["marina"], ctx["anna"]
    spec: list[tuple[str, str, int | None, int | None, int | None, int]] = [
        # имя, аккаунт, ответственный, ждёт (мин назад), открыт (мин назад), последнее сообщение
        ("M1", "own", marina, 300, None, 300),  # свой, давно, не открыт
        ("M2", "own", marina, 200, 100, 200),  # свой, давно, УЖЕ открыт
        ("M3", "own", None, 250, None, 250),  # ничейный, не открыт
        ("M4", "own", None, 120, 60, 120),  # ничейный, уже открыт
        ("M5", "own", marina, 10, None, 10),  # ждёт, но меньше порога
        ("M6", "own", marina, None, None, 5),  # не ждёт
        ("M7", "own", anna, 400, None, 400),  # чужой назначенный: менеджеру не виден
        ("M8", "own", None, 60, None, 60),  # равное время ожидания с M9
        ("M9", "own", marina, 60, None, 61),
        ("A1", "other", anna, 500, None, 500),  # чужой аккаунт
        ("A2", "other", None, 90, None, 90),
        ("W1", "warm", None, 600, None, 600),  # аккаунт этапа «Бот»: менеджеру не виден
    ]
    ids: dict[str, int] = {}
    async with SessionLocal() as db:
        for number, (name, account, responsible, waits, seen, last) in enumerate(spec):
            client = Client(telegram_id=TG_BASE + number, display_name=f"{PREFIX}{name}")
            db.add(client)
            await db.flush()
            conv = Conversation(
                client_id=client.id,
                account_id=ctx[account],
                tg_chat_id=TG_BASE + number,
                responsible_id=responsible,
                awaiting_reply_since=ago(waits),
                awaiting_seen_at=ago(seen),
                last_message_at=ago(last),
                unread_count=0,
            )
            db.add(conv)
            await db.flush()
            ids[name] = conv.id
        await db.commit()
    return ids


# ------------------------------------------------------------ вызовы API


async def walk(
    api: httpx.AsyncClient, params: dict[str, Any], limit: int
) -> tuple[list[dict[str, Any]], int]:
    """Весь список страницами по `limit`, как «Показать ещё». → строки и число страниц."""
    rows: list[dict[str, Any]] = []
    cursor: str | None = None
    pages = 0
    while True:
        query = {**params, "limit": limit}
        if cursor:
            query["cursor"] = cursor
        body = (await api.get(f"{BASE}/conversations", params=query)).json()
        rows += body["items"]
        pages += 1
        cursor = body["next_cursor"]
        if not cursor or pages > 400:
            return rows, pages


async def counters(api: httpx.AsyncClient) -> dict[str, Any]:
    return (await api.get(f"{BASE}/conversations/counters")).json()


async def plate(api: httpx.AsyncClient, user_id: int | None = None) -> dict[str, Any]:
    """Плитка «ждут ответа сейчас» в статистике; руководитель может смотреть «глазами» менеджера."""
    params = {"user_id": user_id} if user_id else {}
    return (await api.get(f"{BASE}/stats/overview", params=params)).json()


async def take_all(api: httpx.AsyncClient) -> tuple[list[int], int, str]:
    """Нажимаем «Взять первого», пока очередь не кончится, как фронтенд: с растущим skip."""
    taken: list[int] = []
    for _ in range(200):
        params = {"skip": ",".join(map(str, taken))} if taken else {}
        response = await api.get(f"{BASE}/conversations/next", params=params)
        if response.status_code != 200:
            error = response.json()["error"]
            return taken, response.status_code, error["code"]
        taken.append(response.json()["id"])
    return taken, 0, "limit"


async def db_expected(sql_scope: str, params: dict[str, Any], minutes: int) -> tuple[int, int]:
    """Независимый подсчёт прямо из таблицы: ждут и ждут дольше порога (календарно)."""
    async with SessionLocal() as db:
        row = (
            await db.execute(
                text(
                    "select count(*), count(*) filter (where c.awaiting_reply_since "  # noqa: S608
                    "< now() - make_interval(mins => :mins)) from conversations c "
                    "where c.awaiting_reply_since is not null"
                    + sql_scope  # только внутренние константы этого файла
                ),
                {**params, "mins": minutes},
            )
        ).one()
    return int(row[0]), int(row[1])


async def db_queue_order(sql_scope: str, params: dict[str, Any]) -> list[int]:
    """Порядок очереди «как должен быть» — отдельным SQL, не кодом сервиса."""
    async with SessionLocal() as db:
        rows = (
            await db.execute(
                text(
                    "select c.id from conversations c where c.awaiting_reply_since is not null"  # noqa: S608
                    + sql_scope  # только внутренние константы этого файла
                    + " order by (c.awaiting_seen_at is not null and c.awaiting_seen_at "
                    ">= c.awaiting_reply_since), c.awaiting_reply_since, c.id"
                ),
                params,
            )
        ).all()
    return [row[0] for row in rows]


async def seen_at(conversation_id: int) -> datetime | None:
    async with SessionLocal() as db:
        return (
            await db.execute(
                text("select awaiting_seen_at from conversations where id = :i"),
                {"i": conversation_id},
            )
        ).scalar()


# ------------------------------------------------------------ сама проверка


def ours(rows: list[dict[str, Any]], fx: dict[str, int]) -> list[str]:
    """Только наши диалоги, в том порядке, в каком они пришли, — по именам."""
    names = {v: k for k, v in fx.items()}
    return [names[row["id"]] for row in rows if row["id"] in names]


async def same_number(
    c: Checks,
    title: str,
    api: httpx.AsyncClient,
    *,
    expected: tuple[int, int] | None = None,
) -> None:
    """Баннер == плитка == длина фильтра (оба числа: «ждут» и «ждут дольше порога»)."""
    banner = await counters(api)
    tile = await plate(api)
    awaiting, _ = await walk(api, {"filter": "awaiting"}, 7)
    over, _ = await walk(api, {"filter": "awaiting_over"}, 7)
    c.check(
        f"{title}: ждут дольше порога — баннер = плитка = фильтр",
        banner["over_threshold"] == tile["awaiting_reply_over_threshold"] == len(over),
        f"баннер {banner['over_threshold']}, плитка {tile['awaiting_reply_over_threshold']}, "
        f"фильтр {len(over)}",
    )
    c.check(
        f"{title}: ждут ответа — счётчик = плитка = фильтр",
        banner["awaiting"] == tile["awaiting_reply_now"] == len(awaiting),
        f"счётчик {banner['awaiting']}, плитка {tile['awaiting_reply_now']}, "
        f"фильтр {len(awaiting)}",
    )
    if expected is not None:
        c.check(
            f"{title}: число совпадает с независимым подсчётом по таблице",
            (banner["awaiting"], banner["over_threshold"]) == expected,
            f"API {banner['awaiting']}/{banner['over_threshold']}, таблица {expected}",
        )


async def run() -> int:
    c = Checks("Очередь ответов: порядок, «Взять первого», одно число везде")
    ctx = await discover()
    if ctx is None:
        print("Нет нужных пользователей/аккаунтов в стенде — проверка пропущена")
        return 0

    admin = await login(ADMIN)
    manager = await login(MANAGER)
    current = (await admin.get(f"{BASE}/settings")).json()
    saved = {key: value for key, value in current.items() if key in SETTING_KEYS}
    await cleanup()
    try:
        await admin.patch(
            f"{BASE}/settings",
            json={"awaiting_banner_minutes": 30, "working_hours_enabled": False},
        )
        fx = await make_fixtures(ctx)
        marina = ctx["marina"]
        visible_to_marina = (
            " and c.account_id in (select am.account_id from account_managers am "
            "join telegram_accounts a on a.id = am.account_id "
            "where am.user_id = :m and a.funnel_stage <> 'warmup') "
            "and (c.responsible_id = :m or c.responsible_id is null)"
        )

        # ---------------------------------------------------- 1. порядок
        c.section("Порядок списков")
        full = ["W1", "A1", "M7", "M1", "M3", "M2", "M4", "A2", "M8", "M9", "M5"]
        rows, _ = await walk(admin, {"filter": "awaiting"}, 100)
        c.check(
            "руководитель: «Ждут ответа» — самые давние сверху",
            ours(rows, fx) == full,
            f"{ours(rows, fx)}",
        )
        stamps = [(r["awaiting_reply_since"], r["id"]) for r in rows]
        c.check("и весь список упорядочен по возрастанию ожидания", stamps == sorted(stamps))
        over_rows, _ = await walk(admin, {"filter": "awaiting_over"}, 100)
        c.check(
            "«Ждут >30 мин»: те же, но без ждущих меньше порога",
            ours(over_rows, fx) == [n for n in full if n != "M5"],
            f"{ours(over_rows, fx)}",
        )
        m_rows, _ = await walk(manager, {"filter": "awaiting"}, 100)
        c.check(
            "менеджер: свои и ничейные на его аккаунте, чужих и этапа «Бот» нет",
            ours(m_rows, fx) == ["M1", "M3", "M2", "M4", "M8", "M9", "M5"],
            f"{ours(m_rows, fx)}",
        )
        every, _ = await walk(admin, {"filter": "all"}, 100)
        last = [r["last_message_at"] for r in every if r["last_message_at"]]
        c.check("«Все» по-прежнему по свежести сообщения", last == sorted(last, reverse=True))

        # ---------------------------------------------------- 2. пагинация
        c.section("Постраничная загрузка")
        for name, params in (
            ("Ждут ответа", {"filter": "awaiting"}),
            ("Ждут >30 мин", {"filter": "awaiting_over"}),
            ("Все", {"filter": "all"}),
        ):
            whole, _ = await walk(admin, params, 100)
            expected_ids = [r["id"] for r in whole]
            ok = True
            detail = ""
            for limit in (1, 2, 3, 5, 13):
                paged, pages = await walk(admin, params, limit)
                ids = [r["id"] for r in paged]
                if ids != expected_ids or len(set(ids)) != len(ids):
                    ok = False
                    detail = f"limit={limit}: {len(ids)} из {len(expected_ids)}, страниц {pages}"
                    break
            c.check(f"«{name}»: без пропусков и дублей при любом размере страницы", ok, detail)

        # ---------------------------------------------------- 3. «Взять первого»
        c.section("Взять первого в очереди")
        admin_scope: tuple[str, dict[str, Any]] = ("", {})
        expected_admin = await db_queue_order(*admin_scope)
        taken, status, code = await take_all(admin)
        c.check(
            "руководитель: очередь пройдена целиком, без повторов, в нужном порядке",
            taken == expected_admin and len(set(taken)) == len(taken),
            f"взято {len(taken)} из {len(expected_admin)}",
        )
        c.check(
            "затем 404 «очередь пройдена»", (status, code) == (404, "queue_done"), (status, code)
        )
        first = (await admin.get(f"{BASE}/conversations/next")).json()
        again = (await admin.get(f"{BASE}/conversations/next")).json()
        c.check(
            "без skip поведение прежнее (старый фронтенд): тот же чат, самый давно ждущий",
            first["id"] == again["id"] == expected_admin[0],
            f"{first['id']}, {again['id']}, ожидали {expected_admin[0]}",
        )

        expected_marina = await db_queue_order(visible_to_marina, {"m": marina})
        taken_m, status, code = await take_all(manager)
        c.check(
            "менеджер: свои и ничейные, непросмотренные первыми, затем просмотренные",
            taken_m == expected_marina and len(set(taken_m)) == len(taken_m),
            f"{taken_m} против {expected_marina}",
        )
        names = {v: k for k, v in fx.items()}
        c.check(
            "порядок в нашем срезе: M1, M3, M8, M9, M5, потом открытые M2, M4",
            [names[i] for i in taken_m if i in names] == ["M1", "M3", "M8", "M9", "M5", "M2", "M4"],
            [names.get(i, i) for i in taken_m],
        )
        c.check("и тоже 404 в конце", (status, code) == (404, "queue_done"), (status, code))
        bad = await manager.get(f"{BASE}/conversations/next", params={"skip": "1,abc"})
        c.check("мусор в skip — 422, а не 500", bad.status_code == 422, bad.status_code)
        many = await manager.get(
            f"{BASE}/conversations/next", params={"skip": ",".join(str(i) for i in range(1, 52))}
        )
        c.check("больше 50 номеров в skip — 422", many.status_code == 422, many.status_code)

        # ---------------------------------------------------- 4. просмотр
        c.section("Просмотр: руководитель и менеджер")
        before = await counters(admin)
        row_before = (await admin.get(f"{BASE}/conversations/{fx['M1']}")).json()
        await admin.post(f"{BASE}/conversations/{fx['M1']}/read")
        row_admin = (await admin.get(f"{BASE}/conversations/{fx['M1']}")).json()
        c.check(
            "руководитель открыл чат — «просмотрено» не появилось",
            await seen_at(fx["M1"]) is None,
        )
        c.check(
            "значок «ждёт N мин» на строке остался",
            row_admin["awaiting_minutes"] is not None
            and row_admin["awaiting_seen"] is False
            and row_before["awaiting_minutes"] is not None,
            f"{row_admin['awaiting_minutes']}, seen={row_admin['awaiting_seen']}",
        )
        after = await counters(admin)
        c.check(
            "счётчики после просмотра руководителем не изменились",
            (before["awaiting"], before["over_threshold"])
            == (after["awaiting"], after["over_threshold"]),
        )
        nxt = (await admin.get(f"{BASE}/conversations/next")).json()
        c.check("и очередь не сдвинулась", nxt["id"] == expected_admin[0], nxt["id"])

        await manager.post(f"{BASE}/conversations/{fx['M1']}/read")
        row_mgr = (await manager.get(f"{BASE}/conversations/{fx['M1']}")).json()
        c.check(
            "менеджер открыл чат — «просмотрено» поставлено",
            await seen_at(fx["M1"]) is not None,
        )
        c.check(
            "значок на строке погас",
            row_mgr["awaiting_seen"] is True and row_mgr["awaiting_minutes"] is None,
            f"seen={row_mgr['awaiting_seen']}, minutes={row_mgr['awaiting_minutes']}",
        )
        c.check(
            "клиент по-прежнему ждёт ответа",
            row_mgr["awaiting_reply_since"] is not None,
        )
        after_mgr = await counters(manager)
        before_mgr_over = (await walk(manager, {"filter": "awaiting_over"}, 100))[0]
        c.check(
            "из баннера и фильтра диалог не ушёл",
            "M1" in ours(before_mgr_over, fx)
            and after_mgr["over_threshold"] == len(before_mgr_over),
            f"баннер {after_mgr['over_threshold']}, фильтр {len(before_mgr_over)}",
        )
        first_for_marina = (await manager.get(f"{BASE}/conversations/next")).json()
        c.check(
            "«Взять первого» у менеджера теперь уходит к следующему непросмотренному",
            first_for_marina["id"] == fx["M3"],
            names.get(first_for_marina["id"], first_for_marina["id"]),
        )

        # ---------------------------------------------------- 5. одно число
        c.section("Одно число: баннер, плитка статистики, фильтр")
        admin_expected = await db_expected("", {}, 30)
        await same_number(c, "руководитель", admin, expected=admin_expected)
        marina_expected = await db_expected(visible_to_marina, {"m": marina}, 30)
        await same_number(c, "менеджер", manager, expected=marina_expected)
        # Руководитель открывает статистику менеджера: плитка обязана показать ровно
        # то, что видит сам менеджер в баннере и в своём фильтре.
        tile = await plate(admin, marina)
        marina_banner = await counters(manager)
        marina_over, _ = await walk(manager, {"filter": "awaiting_over"}, 100)
        c.check(
            "статистика менеджера у руководителя = баннер и фильтр самого менеджера",
            (tile["awaiting_reply_now"], tile["awaiting_reply_over_threshold"])
            == (marina_banner["awaiting"], marina_banner["over_threshold"])
            and tile["awaiting_reply_over_threshold"] == len(marina_over),
            f"плитка {tile['awaiting_reply_now']}/{tile['awaiting_reply_over_threshold']}, "
            f"баннер {marina_banner['awaiting']}/{marina_banner['over_threshold']}, "
            f"фильтр {len(marina_over)}",
        )

        await admin.patch(f"{BASE}/settings", json={"awaiting_banner_minutes": 100})
        banner = await counters(admin)
        over100, _ = await walk(admin, {"filter": "awaiting_over"}, 100)
        c.check(
            "порог 100 мин: подпись и число идут за настройкой",
            banner["over_threshold_minutes"] == 100
            and ours(over100, fx) == ["W1", "A1", "M7", "M1", "M3", "M2", "M4"],
            f"{banner['over_threshold_minutes']}, {ours(over100, fx)}",
        )
        await same_number(
            c, "руководитель, порог 100", admin, expected=await db_expected("", {}, 100)
        )
        await same_number(
            c, "менеджер, порог 100", manager,
            expected=await db_expected(visible_to_marina, {"m": marina}, 100),
        )
        await admin.patch(f"{BASE}/settings", json={"awaiting_banner_minutes": 30})

        await admin.patch(
            f"{BASE}/settings",
            json={
                "working_hours_enabled": True,
                "working_hours_start": "00:00",
                "working_hours_end": "00:00",
                "working_days": [1, 2, 3, 4, 5, 6, 7],
            },
        )
        # Круглые сутки без выходных = календарное время, но запрос идёт через
        # рабочую SQL-функцию, а не через простое сравнение.
        await same_number(c, "рабочие часы вкл., руководитель", admin, expected=admin_expected)
        await same_number(c, "рабочие часы вкл., менеджер", manager, expected=marina_expected)
        paged, _ = await walk(admin, {"filter": "awaiting_over"}, 3)
        direct, _ = await walk(admin, {"filter": "awaiting_over"}, 100)
        c.check(
            "рабочие часы: страницы фильтра не теряют строк",
            [r["id"] for r in paged] == [r["id"] for r in direct],
        )
    finally:
        await admin.patch(f"{BASE}/settings", json=saved)
        await cleanup()
        await admin.aclose()
        await manager.aclose()
    return c.finish()


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
