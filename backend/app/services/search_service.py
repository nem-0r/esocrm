"""Глобальный поиск по клиентам, оплатам, переписке и файлам.

Всё ограничено тем, что сотруднику разрешено видеть. Поиск по тексту сообщений
идёт через полнотекстовый индекс Postgres — на миллионе строк перебор подстрокой
не укладывается ни в какие полсекунды.

Имена, телефоны и @username ищутся одним правилом на весь продукт —
`name_match` (D-35): слова запроса в любом порядке, «ё» = «е», телефон по
цифрам. В выдаче имя клиента — по той же лестнице, что `Client.name`: у всех
клиентов в проде пусто `display_name`, и сырое поле показывало бы «id 703».

История запросов пишется не отсюда: поиск сам по себе (набор по буквам, смена
фильтра, возврат на вкладку) историю не наполняет — только явное действие
пользователя через `remember` (L4).
"""

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import conversation_scope_sql, visible_account_ids
from app.core.errors import Invalid
from app.models import SearchHistory, User, UserRole
from app.services import name_match

MIN_QUERY = 2
HISTORY_LIMIT = 10
ONLINE_WINDOW_MINUTES = 5
ALL_TYPES = ("clients", "deals", "chats", "files", "managers")
# Предел строк одной группы («Показать все»). Читаем на одну больше, чтобы знать,
# что результатов больше предела, и честно написать «50+», а не молча обрезать.
MAX_ROWS = 50
HISTORY_LOCK = 7301  # пространство advisory-замка: один пользователь — одна очередь записей
SNIPPET_LENGTH = 160
# Больше целого bigint в запрос не отдаём: иначе драйвер падает, а не отвечает «не найдено».
MAX_EXACT_DIGITS = 18


def _numeric(query: str) -> int | None:
    """Системные маркеры: «84271» — клиент или сделка, «DEAL-1042» — сделка."""
    cleaned = query.strip().upper().removeprefix("DEAL-")
    if not cleaned.isdigit() or len(cleaned) > MAX_EXACT_DIGITS:
        return None
    return int(cleaned)


def _is_online(last_seen_at: datetime | None) -> bool:
    """То же правило, что и везде: более пяти минут отсутствия — офлайн."""
    if last_seen_at is None:
        return False
    return datetime.now(UTC) - last_seen_at < timedelta(minutes=ONLINE_WINDOW_MINUTES)


def _scope_params(user: User, account_ids: list[int] | None) -> tuple[str, dict[str, Any]]:
    """Поиск обязан подчиняться тем же правам, что и списки: чужой диалог
    не должен всплывать ни клиентом, ни сделкой, ни сообщением."""
    return conversation_scope_sql(user, account_ids, "c")


def _cut(rows: list[Any], limit: int) -> tuple[list[Any], int]:
    """Строки для показа и общее число (не больше MAX_ROWS + 1: «51» значит «больше 50»)."""
    return rows[:limit], min(len(rows), MAX_ROWS + 1)


async def search(  # noqa: PLR0912, PLR0915
    db: AsyncSession, user: User, query: str, types: list[str] | None, limit: int
) -> dict[str, Any]:
    """Группы результатов и `totals` — сколько всего найдено в каждой группе
    (больше MAX_ROWS обозначается числом MAX_ROWS + 1). Историю не пишет."""
    groups: dict[str, Any] = {name: [] for name in ALL_TYPES}
    totals = dict.fromkeys(ALL_TYPES, 0)
    groups["totals"] = totals
    query = (query or "").strip()
    if len(query) < MIN_QUERY or not name_match.parse(query):
        return groups

    limit = max(1, min(limit, MAX_ROWS))
    wanted = set(types or ALL_TYPES)
    account_ids = await visible_account_ids(db, user)
    scope_sql, scope_params = _scope_params(user, account_ids)
    exact = _numeric(query)
    params: dict[str, Any] = {
        "q": query,
        "exact": exact,
        "limit": limit,
        "fetch": MAX_ROWS + 1,
        **scope_params,
    }
    client_name = name_match.client_name_sql("cl")

    # Менеджер без аккаунтов не видит ничего — и это не то же самое, что «видит всё».
    if account_ids == []:
        return groups

    if "clients" in wanted:
        # Тот же критерий видимости, что у deals/chats/files ниже: клиент
        # находится, только если хотя бы один его диалог виден именно этому
        # сотруднику (свой или ничейный на своём аккаунте) — а не просто
        # «есть диалог на аккаунте, к которому есть доступ». Иначе чужой,
        # закреплённый за коллегой клиент всплывал бы в поиске (D-26).
        match = name_match.client_match_sql(query, "cl", prefix="nm")
        assert match is not None  # слова в запросе есть — проверено выше
        sql = text(
            f"""
            select cl.id, {client_name} as name, cl.tg_username, cl.phone,
                   coalesce(paid.amount, 0) as paid_amount
            from clients cl
            left join (
                select client_id, sum(total_amount) as amount
                from deals where status = 'paid' group by client_id
            ) paid on paid.client_id = cl.id
            where cl.deleted_at is null
              and {match[0]}
              and exists (
                    select 1 from conversations c
                    where c.client_id = cl.id
            """  # noqa: S608
            + scope_sql
            + """
                  )
            order by cl.id desc limit :fetch
            """
        )
        rows, totals["clients"] = _cut((await db.execute(sql, {**params, **match[1]})).all(), limit)
        groups["clients"] = [
            {
                "id": r.id,
                "name": r.name,
                "tg_username": r.tg_username,
                "phone": r.phone,
                "paid_amount": int(r.paid_amount),
            }
            for r in rows
        ]

    if "deals" in wanted:
        # Оплату находят по номеру, по названию позиции и по имени клиента (имя,
        # фамилия, @username — как в разделе «Клиенты»). Телефон и id клиента
        # здесь не участвуют: «1042» должно находить сделку DEAL-1042, а не
        # всё, у чего в номере телефона есть эти цифры.
        deal_match = name_match.client_match_sql(
            query,
            "cl",
            prefix="dm",
            phone=False,
            ids=False,
            extra_exists=(
                lambda p: (
                    "exists (select 1 from deal_items i where i.deal_id = d.id and "
                    f"{name_match.norm_sql('i.name')} like {p})"
                ),
            ),
        )
        assert deal_match is not None
        sql = text(
            f"""
            select d.id, d.total_amount, d.status, {client_name} as client_name,
                   (select name from deal_items i where i.deal_id = d.id
                    order by i.amount desc limit 1) as top_item,
                   (select count(*) from deal_items i where i.deal_id = d.id) as items_count
            from deals d
            join clients cl on cl.id = d.client_id
            join conversations c on c.id = d.conversation_id
            where ((cast(:exact as bigint) is not null and d.id = cast(:exact as bigint))
                   or {deal_match[0]})
            """  # noqa: S608
            + scope_sql
            + " order by d.id desc limit :fetch"
        )
        rows, totals["deals"] = _cut(
            (await db.execute(sql, {**params, **deal_match[1]})).all(), limit
        )
        groups["deals"] = [
            {
                "id": r.id,
                "number": f"DEAL-{r.id}",
                "title": (
                    f"{r.top_item} и др."
                    if (r.items_count or 0) > 1
                    else (r.top_item or "Без услуг")
                ),
                "total_amount": int(r.total_amount),
                "status": r.status,
                "client_name": r.client_name,
            }
            for r in rows
        ]

    if "chats" in wanted:
        groups["chats"], totals["chats"] = await _chats(
            db, query, params, scope_sql, client_name, limit
        )

    if "files" in wanted:
        # Файл находят и по имени файла, и по имени клиента, чей он: те же слова,
        # каждое — в имени файла либо в имени/@username клиента.
        file_match = name_match.client_match_sql(
            query,
            "cl",
            prefix="fm",
            phone=False,
            ids=False,
            extra_text=("at.file_name",),
        )
        assert file_match is not None
        sql = text(
            f"""
            select at.id, at.file_name, at.mime_type, at.created_at,
                   cl.id as client_id, {client_name} as client_name
            from attachments at
            join conversations c on c.id = at.conversation_id
            join clients cl on cl.id = at.client_id
            where at.deleted_at is null and {file_match[0]}
            """  # noqa: S608
            + scope_sql
            + " order by at.created_at desc limit :fetch"
        )
        rows, totals["files"] = _cut(
            (await db.execute(sql, {**params, **file_match[1]})).all(), limit
        )
        groups["files"] = [
            {
                "id": r.id,
                "file_name": r.file_name,
                "mime_type": r.mime_type,
                "client_id": r.client_id,
                "client_name": r.client_name,
                "created_at": r.created_at,
                "url": f"/api/v1/files/{r.id}",
            }
            for r in rows
        ]

    # ТЗ Б.13: поиск по менеджерам. Только руководителю — у менеджера раздела
    # сотрудников нет вовсе, и находить коллег через поиск он тоже не должен.
    if "managers" in wanted and user.role == UserRole.ADMIN:
        staff_match = name_match.text_match_sql(query, ("u.full_name", "u.email"), prefix="mg")
        assert staff_match is not None
        sql = text(
            f"""
            select u.id, u.full_name, u.email, u.role, u.is_active,
                   u.last_seen_at, u.avatar_color
            from users u
            where u.deleted_at is null
              and {staff_match[0]}
            order by u.is_active desc, u.full_name
            limit :fetch
            """  # noqa: S608
        )
        rows, totals["managers"] = _cut(
            (await db.execute(sql, {**params, **staff_match[1]})).all(), limit
        )
        groups["managers"] = [
            {
                "id": r.id,
                "full_name": r.full_name,
                "email": r.email,
                "role": r.role,
                "is_active": r.is_active,
                "online": _is_online(r.last_seen_at),
                "avatar_color": r.avatar_color,
            }
            for r in rows
        ]

    return groups


async def _chats(
    db: AsyncSession,
    query: str,
    params: dict[str, Any],
    scope_sql: str,
    client_name: str,
    limit: int,
) -> tuple[list[dict[str, Any]], int]:
    """Группа «Чаты»: диалоги, чей клиент подходит под запрос по имени (как в
    списке чатов), и сообщения, где встретился текст запроса. Диалоги по имени
    идут первыми — человек чаще ищет собеседника, а не фразу."""
    by_name: list[Any] = []
    match = name_match.client_match_sql(query, "cl", prefix="cm")
    if match is not None:
        sql = text(
            f"""
            select c.id as conversation_id, lm.id as message_id,
                   coalesce(lm.created_at, c.last_message_at, c.created_at) as created_at,
                   {client_name} as client_name, a.title as account_title,
                   left(lm.text, {SNIPPET_LENGTH}) as snippet
            from conversations c
            join clients cl on cl.id = c.client_id
            join telegram_accounts a on a.id = c.account_id
            left join lateral (
                select m.id, m.text, m.created_at from messages m
                where m.conversation_id = c.id and m.deleted_at is null
                order by m.created_at desc, m.id desc limit 1
            ) lm on true
            where cl.deleted_at is null and {match[0]}
            """  # noqa: S608
            + scope_sql
            + " order by c.last_message_at desc nulls last, c.id desc limit :fetch"
        )
        by_name = list((await db.execute(sql, {**params, **match[1]})).all())

    # Подсветка (ts_headline) дорогая, поэтому считаем её только для тех строк,
    # что уйдут в ответ, а не для всех 51, прочитанных ради счётчика.
    sql = text(
        """
        with hits as (
            select m.id as message_id, m.conversation_id, m.created_at, m.text,
                   """
        + client_name
        + """ as client_name, a.title as account_title
            from messages m
            join conversations c on c.id = m.conversation_id
            join clients cl on cl.id = c.client_id
            join telegram_accounts a on a.id = c.account_id
            where m.deleted_at is null and m.text is not null
              and to_tsvector('russian', m.text) @@ plainto_tsquery('russian', :q)
        """
        + scope_sql
        + """
            order by m.created_at desc limit :fetch
        )
        select h.message_id, h.conversation_id, h.created_at, h.client_name, h.account_title,
               case when row_number() over (order by h.created_at desc, h.message_id desc) <= :limit
                    then ts_headline('russian', h.text, plainto_tsquery('russian', :q),
                                     'StartSel=«, StopSel=», MaxWords=14, MinWords=4')
               end as snippet
        from hits h
        order by h.created_at desc, h.message_id desc
        """
    )
    by_text = list((await db.execute(sql, params)).all())

    named_messages = {r.message_id for r in by_name if r.message_id is not None}
    merged = by_name + [r for r in by_text if r.message_id not in named_messages]
    shown, total = _cut(merged, limit)
    return (
        [
            {
                "conversation_id": r.conversation_id,
                "client_name": r.client_name,
                "account_title": r.account_title,
                "snippet": r.snippet,
                "message_id": r.message_id,
                "created_at": r.created_at,
            }
            for r in shown
        ],
        total,
    )


async def remember(db: AsyncSession, user: User, query: str) -> list[dict[str, Any]]:
    """История поиска (L4): запрос запоминается по явному действию человека —
    Enter в строке поиска или клик по результату.

    Регистр, «ё»/«е» и лишние пробелы запросы не различают: «Ксения  Сергеева» и
    «ксения сергеева» — одна запись. Повторный запрос не плодит дубль, а
    поднимает запись наверх (она пересоздаётся — новый id — с последним написанием).
    Остаются последние десять. Дубли, накопленные старой версией, при этом
    тоже убираются."""
    clean = " ".join((query or "").split())[:255]
    key = name_match.normalize(clean)
    if len(key) < MIN_QUERY:
        raise Invalid("Запрос слишком короткий")

    # Двойное нажатие Enter шлёт два запроса почти одновременно; без замка
    # оба не увидели бы друг друга и записали бы дубль.
    await db.execute(
        text("select pg_advisory_xact_lock(:space, :uid)"),
        {"space": HISTORY_LOCK, "uid": user.id},
    )
    rows = (
        (
            await db.execute(
                select(SearchHistory)
                .where(SearchHistory.user_id == user.id)
                .order_by(SearchHistory.id.desc())
            )
        )
        .scalars()
        .all()
    )
    seen: set[str] = set()
    drop: list[int] = []
    for row in rows:  # от новых к старым: из дублей остаётся самая свежая запись
        row_key = name_match.normalize(row.query)
        if row_key == key or row_key in seen:
            drop.append(row.id)
        seen.add(row_key)
    if drop:
        await db.execute(delete(SearchHistory).where(SearchHistory.id.in_(drop)))

    db.add(SearchHistory(user_id=user.id, query=clean))
    await db.flush()
    keep = (
        select(SearchHistory.id)
        .where(SearchHistory.user_id == user.id)
        .order_by(SearchHistory.id.desc())
        .limit(HISTORY_LIMIT)
        .scalar_subquery()
    )
    await db.execute(
        delete(SearchHistory).where(SearchHistory.user_id == user.id, SearchHistory.id.not_in(keep))
    )
    await db.commit()
    return await history(db, user)


async def history(db: AsyncSession, user: User) -> list[dict[str, Any]]:
    """Последние запросы, новые сверху. Дубли, оставшиеся от старой версии
    (она писала историю на каждый поиск), показываются один раз."""
    rows = (
        await db.execute(
            select(SearchHistory)
            .where(SearchHistory.user_id == user.id)
            .order_by(SearchHistory.id.desc())
            .limit(HISTORY_LIMIT * 5)
        )
    ).scalars()
    seen: set[str] = set()
    items: list[dict[str, Any]] = []
    for row in rows:
        key = name_match.normalize(row.query)
        if key in seen:
            continue
        seen.add(key)
        items.append({"id": row.id, "query": row.query, "created_at": row.created_at})
        if len(items) == HISTORY_LIMIT:
            break
    return items


async def clear_history(db: AsyncSession, user: User) -> None:
    await db.execute(delete(SearchHistory).where(SearchHistory.user_id == user.id))
    await db.commit()
