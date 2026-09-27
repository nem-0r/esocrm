"""Показатели статистики, добавленные 27.09.2026 (docs/14-release-plan-2026-09-27.md, §8).

Формулы — docs/03-business-rules.md §10. Коротко:

- **Скорость ответа** — ожидания клиента в периоде (входящее, начавшее
  ожидание): среднее время ответа, доля ответов не позже цели (15 мин),
  просрочки — ответ позже порога плашки (30 мин) или ответа нет, а ждёт дольше
  порога. Рабочие часы, если включены, действуют на всё это одной функцией.
- **Конверсия счетов** — из сделок, отправленных клиенту в периоде, доля
  оплаченных на сегодня. Когорта по дате отправки: так видно, сколько из
  выставленного за неделю в итоге превратилось в деньги.
- **Конверсия новых клиентов** — из клиентов с первым обращением в периоде
  доля тех, у кого есть оплаченная сделка.
- **По услугам** — позиции оплаченных в периоде сделок: сколько, на какую сумму,
  доля выручки; и когорта отправленных в периоде — какая доля оплачена.
- **По аккаунтам** — те же продажи, диалоги и скорость, но в разрезе аккаунта
  диалога (направления воронки).

Права — те же, что у существующих показателей (`stats_service.Scope`):
менеджер видит своё, руководитель — всё.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import settings_service, worktime

# Строк в разрезе по услугам: остальное складывается в «Остальные» — таблица
# на 200 позиций не читается, а деньги в сумме не теряются.
SERVICES_TOP = 15


def _seller(scope: Any, prefix: str = "d") -> str:
    return "" if scope.seller_ids is None else f" and {prefix}.sold_by_id = any(:seller_ids)"


def _accounts(scope: Any, prefix: str = "c") -> str:
    if scope.account_ids is None:
        return ""
    own = f" and {prefix}.responsible_id = :scope_uid" if scope.user_id is not None else ""
    return f" and {prefix}.account_id = any(:account_ids)" + own


def _no_admin(scope: Any, prefix: str = "c") -> str:
    """Ответы руководителя не красят скорость менеджеров (§10, 2026-09-17)."""
    if scope.account_ids is not None:
        return ""
    return (
        f" and not exists (select 1 from users ru where ru.id = {prefix}.responsible_id"
        " and ru.role = 'admin')"
    )


def _params(scope: Any, start: datetime, end: datetime) -> dict[str, Any]:
    params: dict[str, Any] = {"start": start, "end": end}
    if scope.seller_ids is not None:
        params["seller_ids"] = scope.seller_ids
    if scope.account_ids is not None:
        params["account_ids"] = scope.account_ids
        if scope.user_id is not None:
            params["scope_uid"] = scope.user_id
    return params


# ------------------------------------------------------------ скорость ответа


@dataclass(slots=True)
class ResponseStats:
    avg_seconds: int | None = None
    waits: int = 0
    answered: int = 0
    in_goal: int = 0
    # Ожидания, по которым уже ясно, уложились ли: ответили, либо без ответа
    # и уже дольше цели. Свежее ожидание без ответа ещё может успеть.
    decided: int = 0
    late: int = 0

    @property
    def in_goal_pct(self) -> float | None:
        return round(self.in_goal / self.decided * 100, 1) if self.decided else None


async def _elapsed(db: AsyncSession, start_col: str, end_col: str) -> tuple[str, dict[str, Any]]:
    hours = await worktime.load(db)
    if not hours.enabled:
        return f"extract(epoch from ({end_col} - {start_col}))", {}
    return (
        f"astra_working_seconds({start_col}, {end_col}, :wh_days, :wh_start, :wh_end, :wh_tz)",
        hours.sql_params(),
    )


async def response_stats(
    db: AsyncSession,
    scope: Any,
    start: datetime,
    end: datetime,
    group_by: str | None = None,
) -> dict[int, ResponseStats]:
    """Ожидания клиента в периоде. Ключ — 0, id ответственного или id аккаунта.

    Проход идёт только по диалогам, где клиент писал в периоде, но по ВСЕЙ их
    переписке: предыдущее сообщение (началось ли ожидание) и следующий ответ
    могут лежать за краями периода.
    """
    group = {"responsible": "c.responsible_id", "account": "c.account_id"}.get(group_by or "", "0")
    only_owned = " and c.responsible_id is not null" if group_by == "responsible" else ""
    elapsed, extra = await _elapsed(db, "created_at", "until_at")
    goal = int(await settings_service.get_value(db, "response_time_goal_minutes") or 15)
    late = int(await settings_service.get_value(db, "awaiting_banner_minutes") or 30)
    sql = f"""
        with active as (
            select distinct m.conversation_id
            from messages m
            join conversations c on c.id = m.conversation_id
            where m.direction = 'in' and m.is_internal = false and m.deleted_at is null
              and m.created_at >= :start and m.created_at < :end
              {_accounts(scope)}{_no_admin(scope)}{only_owned}
        ),
        base as (
            select {group} as grp, m.direction, m.created_at,
                   lag(m.direction) over (
                       partition by m.conversation_id order by m.created_at, m.id
                   ) as prev_direction,
                   min(m.created_at) filter (where m.direction = 'out') over (
                       partition by m.conversation_id order by m.created_at, m.id
                       rows between 1 following and unbounded following
                   ) as next_out
            from messages m
            join active a on a.conversation_id = m.conversation_id
            join conversations c on c.id = m.conversation_id
            where m.deleted_at is null and m.is_internal = false
        ),
        waits as (
            select grp, created_at, next_out, coalesce(next_out, :now) as until_at
            from base
            where direction = 'in'
              and (prev_direction is null or prev_direction = 'out')
              and created_at >= :start and created_at < :end
        ),
        measured as (
            select grp, next_out, {elapsed} as elapsed from waits
        )
        select grp,
               avg(elapsed) filter (where next_out is not null) as avg_seconds,
               count(*) as waits,
               count(*) filter (where next_out is not null) as answered,
               count(*) filter (where next_out is not null and elapsed <= :goal) as in_goal,
               count(*) filter (where next_out is not null or elapsed > :goal) as decided,
               count(*) filter (where elapsed > :late) as late
        from measured
        group by grp
    """  # noqa: S608 — склейка только внутренних констант, пользовательское — параметрами
    params = {
        **_params(scope, start, end),
        **extra,
        "now": datetime.now(UTC),
        "goal": goal * 60,
        "late": late * 60,
    }
    rows = (await db.execute(text(sql), params)).all()
    result: dict[int, ResponseStats] = {}
    for row in rows:
        if row.grp is None:
            continue
        result[int(row.grp)] = ResponseStats(
            avg_seconds=None if row.avg_seconds is None else int(round(float(row.avg_seconds))),
            waits=int(row.waits),
            answered=int(row.answered),
            in_goal=int(row.in_goal),
            decided=int(row.decided),
            late=int(row.late),
        )
    return result


async def awaiting_now(db: AsyncSession, scope: Any) -> tuple[int, int]:
    """Сколько диалогов ждут ответа прямо сейчас и сколько из них дольше порога.

    Это состояние очереди, а не скорость менеджеров: у руководителя считаются
    все диалоги, как в счётчике «ждут ответа» над списком чатов.
    """
    late = int(await settings_service.get_value(db, "awaiting_banner_minutes") or 30)
    now = datetime.now(UTC)
    hours = await worktime.load(db)
    if hours.enabled:
        over = (
            "astra_working_seconds(c.awaiting_reply_since, :now, :wh_days, :wh_start, "
            ":wh_end, :wh_tz) > :late"
        )
        extra: dict[str, Any] = {**hours.sql_params(), "now": now, "late": late * 60}
    else:
        over = "c.awaiting_reply_since < :cutoff"
        extra = {"cutoff": now - timedelta(minutes=late)}
    sql = (
        "select count(*) as total, count(*) filter (where " + over + ") as over "
        "from conversations c where c.awaiting_reply_since is not null" + _accounts(scope)
    )
    row = (await db.execute(text(sql), {**_params(scope, now, now), **extra})).one()
    return int(row.total), int(row.over)


# ------------------------------------------------------------ продажи


async def invoice_conversion(
    db: AsyncSession, scope: Any, start: datetime, end: datetime
) -> dict[str, int]:
    """Сделки, отправленные клиенту в периоде, и что с ними стало к сегодняшнему дню."""
    sql = (
        "select count(*) as sent, "
        "count(*) filter (where d.status = 'paid') as paid, "
        "count(*) filter (where d.status = 'awaiting') as awaiting, "
        "count(*) filter (where d.status = 'cancelled') as cancelled, "
        "count(*) filter (where d.status = 'expired') as expired, "
        "coalesce(sum(d.total_amount), 0) as sent_amount, "
        "coalesce(sum(d.total_amount) filter (where d.status = 'paid'), 0) as paid_amount "
        "from deals d where d.sent_at >= :start and d.sent_at < :end" + _seller(scope)
    )
    row = (await db.execute(text(sql), _params(scope, start, end))).one()
    return {key: int(getattr(row, key)) for key in row._fields}


async def new_client_conversion(
    db: AsyncSession, scope: Any, start: datetime, end: datetime
) -> tuple[int, int]:
    """Новые клиенты периода и сколько из них уже заплатили (на сегодня)."""
    paid = (
        "exists (select 1 from deals d where d.client_id = cl.id and d.status = 'paid'"
        + _seller(scope)
        + ")"
    )
    if scope.account_ids is None:
        sql = (
            f"select count(*) as total, count(*) filter (where {paid}) as paying "
            "from clients cl where cl.deleted_at is null "
            "and cl.first_contact_at >= :start and cl.first_contact_at < :end"
        )
    else:
        sql = (
            f"select count(distinct cl.id) as total, "
            f"count(distinct cl.id) filter (where {paid}) as paying "
            "from clients cl join conversations c on c.client_id = cl.id "
            "where cl.deleted_at is null "
            "and cl.first_contact_at >= :start and cl.first_contact_at < :end" + _accounts(scope)
        )
    row = (await db.execute(text(sql), _params(scope, start, end))).one()
    return int(row.total), int(row.paying)


# ------------------------------------------------------------ по услугам


_ITEM_KEY = (
    "case when i.service_id is not null then 's' || i.service_id "
    "else 'n' || regexp_replace(lower(trim(i.name)), '\\s+', ' ', 'g') end"
)
_ITEM_LABEL = "coalesce(s.name, regexp_replace(trim(i.name), '\\s+', ' ', 'g'))"


async def by_service(
    db: AsyncSession, scope: Any, start: datetime, end: datetime
) -> dict[str, Any]:
    """Продажи в разрезе услуг. Одна услуга справочника — одна строка, даже если
    название в сделках писали по-разному; вписанные вручную склеиваются по
    написанию без учёта регистра и лишних пробелов."""
    sold_sql = f"""
        select {_ITEM_KEY} as key, min({_ITEM_LABEL}) as name, max(i.service_id) as service_id,
               count(*) as sold, sum(i.amount) as revenue
        from deal_items i
        join deals d on d.id = i.deal_id
        left join services s on s.id = i.service_id
        where d.status = 'paid' and d.paid_at >= :start and d.paid_at < :end {_seller(scope)}
        group by 1
    """  # noqa: S608
    offered_sql = f"""
        select {_ITEM_KEY} as key, min({_ITEM_LABEL}) as name, max(i.service_id) as service_id,
               count(*) as offered,
               count(*) filter (where d.status = 'paid') as offered_paid
        from deal_items i
        join deals d on d.id = i.deal_id
        left join services s on s.id = i.service_id
        where d.sent_at >= :start and d.sent_at < :end {_seller(scope)}
        group by 1
    """  # noqa: S608
    params = _params(scope, start, end)
    rows: dict[str, dict[str, Any]] = {}
    for row in (await db.execute(text(sold_sql), params)).all():
        rows[row.key] = {
            "key": row.key,
            "name": row.name,
            "service_id": row.service_id,
            "sold_count": int(row.sold),
            "revenue": int(row.revenue),
            "offered": 0,
            "offered_paid": 0,
        }
    for row in (await db.execute(text(offered_sql), params)).all():
        entry = rows.setdefault(
            row.key,
            {
                "key": row.key,
                "name": row.name,
                "service_id": row.service_id,
                "sold_count": 0,
                "revenue": 0,
                "offered": 0,
                "offered_paid": 0,
            },
        )
        entry["offered"] = int(row.offered)
        entry["offered_paid"] = int(row.offered_paid)

    total_revenue = sum(entry["revenue"] for entry in rows.values())
    ordered = sorted(rows.values(), key=lambda e: (-e["revenue"], -e["offered"], e["name"]))
    top, rest = ordered[:SERVICES_TOP], ordered[SERVICES_TOP:]
    if rest:
        top.append(
            {
                "key": "other",
                "name": f"Остальные ({len(rest)})",
                "service_id": None,
                "sold_count": sum(e["sold_count"] for e in rest),
                "revenue": sum(e["revenue"] for e in rest),
                "offered": sum(e["offered"] for e in rest),
                "offered_paid": sum(e["offered_paid"] for e in rest),
            }
        )
    for entry in top:
        entry["share_pct"] = (
            round(entry["revenue"] / total_revenue * 100, 1) if total_revenue else None
        )
        entry["avg_price"] = (
            round(entry["revenue"] / entry["sold_count"]) if entry["sold_count"] else None
        )
        entry["conversion_pct"] = (
            round(entry["offered_paid"] / entry["offered"] * 100, 1) if entry["offered"] else None
        )
    return {"rows": top, "total_revenue": total_revenue}


# ------------------------------------------------------------ по аккаунтам


async def by_account(
    db: AsyncSession, scope: Any, start: datetime, end: datetime
) -> list[dict[str, Any]]:
    """Разрез по аккаунтам: продажи — по аккаунту диалога сделки, диалоги — по
    своему аккаунту. У менеджера — только его аккаунты и его работа на них."""
    accounts_filter = "" if scope.account_ids is None else " and a.id = any(:account_ids)"
    params = _params(scope, start, end)
    sql = f"""
        select a.id, a.title, a.funnel_stage,
               coalesce(sales.amount, 0) as sales_amount,
               coalesce(sales.cnt, 0) as sales_count,
               coalesce(fresh.cnt, 0) as new_conversations,
               coalesce(active.cnt, 0) as active_conversations
        from telegram_accounts a
        left join (
            select c.account_id, sum(d.total_amount) as amount, count(*) as cnt
            from deals d join conversations c on c.id = d.conversation_id
            where d.status = 'paid' and d.paid_at >= :start and d.paid_at < :end {_seller(scope)}
            group by 1
        ) sales on sales.account_id = a.id
        left join (
            select c.account_id, count(*) as cnt
            from conversations c
            where c.started_at >= :start and c.started_at < :end {_accounts(scope)}
            group by 1
        ) fresh on fresh.account_id = a.id
        left join (
            select c.account_id, count(distinct c.id) as cnt
            from conversations c join messages m on m.conversation_id = c.id
            where m.deleted_at is null and m.created_at >= :start and m.created_at < :end
            {_accounts(scope)}
            group by 1
        ) active on active.account_id = a.id
        where a.deleted_at is null {accounts_filter}
        order by coalesce(sales.amount, 0) desc, a.title
    """  # noqa: S608
    rows = (await db.execute(text(sql), params)).all()
    speed = await response_stats(db, scope, start, end, group_by="account")
    result = []
    for row in rows:
        stats = speed.get(int(row.id))
        count = int(row.sales_count)
        result.append(
            {
                "account": {"id": row.id, "title": row.title, "funnel_stage": row.funnel_stage},
                "sales_amount": int(row.sales_amount),
                "sales_count": count,
                "avg_check": round(int(row.sales_amount) / count) if count else None,
                "new_conversations": int(row.new_conversations),
                "active_conversations": int(row.active_conversations),
                "avg_response_seconds": stats.avg_seconds if stats else None,
                "in_goal_pct": stats.in_goal_pct if stats else None,
            }
        )
    return result
