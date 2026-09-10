"""Глобальный поиск по клиентам, оплатам, переписке и файлам.

Всё ограничено тем, что сотруднику разрешено видеть. Поиск по тексту сообщений
идёт через полнотекстовый индекс Postgres — на миллионе строк перебор подстрокой
не укладывается ни в какие полсекунды.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import conversation_scope_sql, visible_account_ids
from app.models import SearchHistory, User, UserRole

MIN_QUERY = 2
HISTORY_LIMIT = 10
ONLINE_WINDOW_MINUTES = 5
ALL_TYPES = ("clients", "deals", "chats", "files", "managers")


def _numeric(query: str) -> int | None:
    """Системные маркеры: «84271» — клиент или сделка, «DEAL-1042» — сделка."""
    cleaned = query.strip().upper().removeprefix("DEAL-")
    return int(cleaned) if cleaned.isdigit() else None


def _is_online(last_seen_at: datetime | None) -> bool:
    """То же правило, что и везде: более пяти минут отсутствия — офлайн."""
    if last_seen_at is None:
        return False
    return datetime.now(UTC) - last_seen_at < timedelta(minutes=ONLINE_WINDOW_MINUTES)


def _scope_params(user: User, account_ids: list[int] | None) -> tuple[str, dict[str, Any]]:
    """Поиск обязан подчиняться тем же правам, что и списки: чужой диалог
    не должен всплывать ни клиентом, ни сделкой, ни сообщением."""
    return conversation_scope_sql(user, account_ids, "c")


async def search(
    db: AsyncSession, user: User, query: str, types: list[str] | None, limit: int
) -> dict[str, list[dict[str, Any]]]:
    groups = {name: [] for name in ALL_TYPES}
    query = (query or "").strip()
    if len(query) < MIN_QUERY:
        return groups

    wanted = set(types or ALL_TYPES)
    account_ids = await visible_account_ids(db, user)
    scope_sql, scope_params = _scope_params(user, account_ids)
    exact = _numeric(query)
    params: dict[str, Any] = {
        "q": query,
        "like": f"%{query}%",
        # tg_username хранится без «@» — если ввели «@ivan», ищем «ivan».
        "username_like": f"%{query.lstrip('@')}%",
        "exact": exact,
        "limit": limit,
        **scope_params,
    }

    # Менеджер без аккаунтов не видит ничего — и это не то же самое, что «видит всё».
    if account_ids == []:
        return groups

    if "clients" in wanted:
        # Тот же критерий видимости, что у deals/chats/files ниже: клиент
        # находится, только если хотя бы один его диалог виден именно этому
        # сотруднику (свой или ничейный на своём аккаунте) — а не просто
        # «есть диалог на аккаунте, к которому есть доступ». Иначе чужой,
        # закреплённый за коллегой клиент всплывал бы в поиске (D-26).
        sql = text(
            """
            select cl.id, cl.display_name, cl.phone,
                   coalesce(paid.amount, 0) as paid_amount
            from clients cl
            left join (
                select client_id, sum(total_amount) as amount
                from deals where status = 'paid' group by client_id
            ) paid on paid.client_id = cl.id
            where cl.deleted_at is null
              and (cl.display_name ilike :like or cl.phone ilike :like
                   or cl.tg_username ilike :username_like
                   or (cast(:exact as bigint) is not null and cl.id = cast(:exact as bigint)))
              and exists (
                    select 1 from conversations c
                    where c.client_id = cl.id
            """
            + scope_sql
            + """
                  )
            order by cl.id desc limit :limit
            """
        )
        rows = (await db.execute(sql, params)).all()
        groups["clients"] = [
            {
                "id": r.id,
                "name": r.display_name,
                "phone": r.phone,
                "paid_amount": int(r.paid_amount),
            }
            for r in rows
        ]

    if "deals" in wanted:
        sql = text(
            """
            select d.id, d.total_amount, d.status, cl.display_name as client_name,
                   (select name from deal_items i where i.deal_id = d.id
                    order by i.amount desc limit 1) as top_item,
                   (select count(*) from deal_items i where i.deal_id = d.id) as items_count
            from deals d
            join clients cl on cl.id = d.client_id
            join conversations c on c.id = d.conversation_id
            where ((cast(:exact as bigint) is not null and d.id = cast(:exact as bigint))
                   or cl.display_name ilike :like
                   or exists (select 1 from deal_items i
                              where i.deal_id = d.id and i.name ilike :like))
            """
            + scope_sql
            + " order by d.id desc limit :limit"
        )
        rows = (await db.execute(sql, params)).all()
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
        sql = text(
            """
            select m.id as message_id, m.conversation_id, m.created_at,
                   cl.display_name as client_name, a.title as account_title,
                   ts_headline('russian', m.text, plainto_tsquery('russian', :q),
                               'StartSel=«, StopSel=», MaxWords=14, MinWords=4') as snippet
            from messages m
            join conversations c on c.id = m.conversation_id
            join clients cl on cl.id = c.client_id
            join telegram_accounts a on a.id = c.account_id
            where m.deleted_at is null and m.text is not null
              and to_tsvector('russian', m.text) @@ plainto_tsquery('russian', :q)
            """
            + scope_sql
            + " order by m.created_at desc limit :limit"
        )
        rows = (await db.execute(sql, params)).all()
        groups["chats"] = [
            {
                "conversation_id": r.conversation_id,
                "client_name": r.client_name,
                "account_title": r.account_title,
                "snippet": r.snippet,
                "message_id": r.message_id,
                "created_at": r.created_at,
            }
            for r in rows
        ]

    if "files" in wanted:
        sql = text(
            """
            select at.id, at.file_name, at.mime_type, at.created_at,
                   cl.id as client_id, cl.display_name as client_name
            from attachments at
            join conversations c on c.id = at.conversation_id
            join clients cl on cl.id = at.client_id
            where at.deleted_at is null and at.file_name ilike :like
            """
            + scope_sql
            + " order by at.created_at desc limit :limit"
        )
        rows = (await db.execute(sql, params)).all()
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
        sql = text(
            """
            select u.id, u.full_name, u.email, u.role, u.is_active,
                   u.last_seen_at, u.avatar_color
            from users u
            where u.deleted_at is null
              and (u.full_name ilike :like or u.email ilike :like)
            order by u.is_active desc, u.full_name
            limit :limit
            """
        )
        rows = (await db.execute(sql, params)).all()
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

    await remember(db, user, query)
    return groups


async def remember(db: AsyncSession, user: User, query: str) -> None:
    """История поиска: держим последние десять запросов, остальные удаляем."""
    db.add(SearchHistory(user_id=user.id, query=query))
    await db.flush()
    keep = (
        select(SearchHistory.id)
        .where(SearchHistory.user_id == user.id)
        .order_by(SearchHistory.id.desc())
        .limit(HISTORY_LIMIT)
        .scalar_subquery()
    )
    await db.execute(
        delete(SearchHistory).where(
            SearchHistory.user_id == user.id, SearchHistory.id.not_in(keep)
        )
    )
    await db.commit()


async def history(db: AsyncSession, user: User) -> list[dict[str, Any]]:
    rows = (
        await db.execute(
            select(SearchHistory)
            .where(SearchHistory.user_id == user.id)
            .order_by(SearchHistory.id.desc())
            .limit(HISTORY_LIMIT)
        )
    ).scalars()
    return [{"id": r.id, "query": r.query, "created_at": r.created_at} for r in rows]


async def clear_history(db: AsyncSession, user: User) -> None:
    await db.execute(delete(SearchHistory).where(SearchHistory.user_id == user.id))
    await db.commit()
