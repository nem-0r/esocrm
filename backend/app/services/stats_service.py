"""Статистика.

Показатели считаются прямо по исходным таблицам. Это честнее предагрегата на
текущем объёме и даёт ровно те цифры, которые видит пользователь в списках.
Каждая метрика — отдельная функция, поэтому переход на предагрегат `daily_stats`
позже будет правкой одного файла, а не всего раздела.

Определения (docs/03-business-rules.md §10):
  · продажа считается по дате оплаты, а не по дате создания сделки;
  · среднее время ответа — от входящего, начавшего ожидание, до первого исходящего;
  · единица измерения одна на весь стек и подписана в интерфейсе.
"""

from datetime import date, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import visible_account_ids
from app.core.errors import Invalid, NotFound
from app.models import DealStatus, User, UserRole
from app.services import settings_service, stats_extra, worktime
from app.services.worktime import day_bounds

# Ниже несколько запросов собираются склейкой строк. Это безопасно и помечено
# noqa: S608 — подставляются только внутренние константы из _seller_clause и
# _account_clause, а гранулярность проверена по белому списку. Всё, что приходит
# от пользователя, уходит параметрами запроса.
MAX_DAYS_FOR_DAILY = 62


class Scope:
    """Что попадает в расчёт: свои сделки и свои диалоги либо всё.

    `user_id` нужен, чтобы ограничить диалоги ответственным: менеджер отвечает
    за свои чаты, и его показатели считаются по ним, а не по всему аккаунту.
    """

    def __init__(
        self,
        seller_ids: list[int] | None,
        account_ids: list[int] | None,
        user_id: int | None = None,
    ) -> None:
        self.seller_ids = seller_ids
        self.account_ids = account_ids
        self.user_id = user_id

    @property
    def empty(self) -> bool:
        """Считать нечего вовсе — к человеку не может быть привязано ни одной сделки.

        Пустой список аккаунтов сюда не относится. Продажа засчитывается тому,
        кто создал оплату (docs/03-business-rules.md §5: «снятие менеджера
        не меняет прошлую статистику»), а аккаунты ограничивают только показатели
        по диалогам. Раньше человек без аккаунтов — снятый со всех аккаунтов
        менеджер или руководитель, которого на аккаунты не назначают вовсе, —
        получал нули по всем показателям, включая собственные продажи: в разрезе
        по менеджерам деньги за ним числились, а в его карточке был ноль.
        Показатели по диалогам при пустом списке дадут ноль сами:
        `account_id = any('{}')` не совпадает ни с чем.
        """
        return self.seller_ids == []


async def build_scope(db: AsyncSession, user: User, user_id: int | None) -> Scope:
    if user.role == UserRole.ADMIN:
        if user_id is None:
            return Scope(None, None)
        exists = await db.scalar(
            text("select exists(select 1 from users where id = :uid and deleted_at is null)"),
            {"uid": user_id},
        )
        if not exists:
            # Без этой проверки несуществующий сотрудник давал бы честные нули —
            # неотличимые от настоящего сотрудника без активности за период.
            raise NotFound("Сотрудник не найден")
        accounts = await db.execute(
            text("select account_id from account_managers where user_id = :uid"), {"uid": user_id}
        )
        return Scope([user_id], [row[0] for row in accounts], user_id)
    if user_id is not None and user_id != user.id:
        raise Invalid("Чужая статистика недоступна")
    return Scope([user.id], await visible_account_ids(db, user) or [], user.id)


def _seller_clause(scope: Scope, prefix: str = "d") -> str:
    return "" if scope.seller_ids is None else f" and {prefix}.sold_by_id = any(:seller_ids)"


def _account_clause(scope: Scope, prefix: str = "c") -> str:
    """Диалоги в расчёте показателей — только те, за которые человек отвечает.

    Ничейные сюда не входят намеренно: в списке чатов они видны, чтобы их можно
    было взять в работу, но пока никто не взял — это не чья-то работа и не
    должно влиять ни на чьё среднее время ответа.
    """
    if scope.account_ids is None:
        return ""
    own = f" and {prefix}.responsible_id = :scope_uid" if scope.user_id is not None else ""
    return f" and {prefix}.account_id = any(:account_ids)" + own


def _params(scope: Scope, start: datetime, end: datetime) -> dict[str, Any]:
    params: dict[str, Any] = {"start": start, "end": end, "paid": DealStatus.PAID.value}
    if scope.seller_ids is not None:
        params["seller_ids"] = scope.seller_ids
    if scope.account_ids is not None:
        params["account_ids"] = scope.account_ids
        if scope.user_id is not None:
            params["scope_uid"] = scope.user_id
    return params


async def _sales(db: AsyncSession, scope: Scope, start: datetime, end: datetime) -> tuple[int, int]:
    row = (
        await db.execute(
            text(
                "select coalesce(sum(d.total_amount), 0) as amount, count(*) as cnt "
                "from deals d where d.status = :paid "
                "and d.paid_at >= :start and d.paid_at < :end" + _seller_clause(scope)
            ),
            _params(scope, start, end),
        )
    ).one()
    return int(row.amount), int(row.cnt)


async def _elapsed_sql(db: AsyncSession) -> tuple[str, dict[str, Any]]:
    """Как считать длительность ожидания: календарно или в рабочих часах.

    Возвращает кусок SQL и параметры к нему. Кусок один на все три запроса о
    времени ответа — иначе настройка «Учитывать рабочие часы» действовала бы
    на сводку и не действовала на разбивку по менеджерам, и цифры разошлись бы.
    """
    hours = await worktime.load(db)
    if not hours.enabled:
        return "extract(epoch from (next_out - created_at))", {}
    return (
        "astra_working_seconds(created_at, next_out, :wh_days, :wh_start, :wh_end, :wh_tz)",
        hours.sql_params(),
    )


async def _active_conversations(
    db: AsyncSession, scope: Scope, start: datetime, end: datetime
) -> int:
    sql = text(
        "select count(distinct m.conversation_id) from messages m "
        "join conversations c on c.id = m.conversation_id "
        "where m.deleted_at is null and m.created_at >= :start and m.created_at < :end"
        + _account_clause(scope)
    )
    return int((await db.execute(sql, _params(scope, start, end))).scalar_one())


async def _awaiting(db: AsyncSession, scope: Scope) -> tuple[int, int]:
    sql = text(
        "select coalesce(sum(d.total_amount), 0) as amount, count(*) as cnt from deals d "
        "where d.status = 'awaiting'" + _seller_clause(scope)
    )
    params = {} if scope.seller_ids is None else {"seller_ids": scope.seller_ids}
    row = (await db.execute(sql, params)).one()
    return int(row.amount), int(row.cnt)


async def overview(
    db: AsyncSession, user: User, date_from: date, date_to: date, user_id: int | None
) -> dict[str, Any]:
    if date_from > date_to:
        raise Invalid("Начало периода позже его конца")

    scope = await build_scope(db, user, user_id)
    goal = int(await settings_service.get_value(db, "response_time_goal_minutes"))

    banner = int(await settings_service.get_value(db, "awaiting_banner_minutes") or 30)
    if scope.empty:
        return {
            "sales_amount": 0,
            "sales_count": 0,
            "sales_amount_delta_pct": None,
            "sales_count_delta": None,
            "avg_check": None,
            "avg_check_delta_pct": None,
            "avg_response_seconds": None,
            "response_goal_minutes": goal,
            "late_threshold_minutes": banner,
            "waits_total": 0,
            "waits_in_goal": 0,
            "waits_decided": 0,
            "in_goal_pct": None,
            "late_count": 0,
            "awaiting_reply_now": 0,
            "awaiting_reply_over_threshold": 0,
            "active_conversations": 0,
            "new_clients": 0,
            "new_clients_paying": 0,
            "new_clients_conversion_pct": None,
            "invoices_sent": 0,
            "invoices_paid": 0,
            "invoices_awaiting": 0,
            "invoices_cancelled": 0,
            "invoices_expired": 0,
            "invoices_sent_amount": 0,
            "invoices_paid_amount": 0,
            "invoice_conversion_pct": None,
            "awaiting_amount": 0,
            "awaiting_count": 0,
        }

    start, end = await day_bounds(db, date_from, date_to)
    span = end - start
    prev_start, prev_end = start - span, start

    amount, count = await _sales(db, scope, start, end)
    prev_amount, prev_count = await _sales(db, scope, prev_start, prev_end)
    awaiting_amount, awaiting_count = await _awaiting(db, scope)
    speed = (await stats_extra.response_stats(db, scope, start, end)).get(0) or (
        stats_extra.ResponseStats()
    )
    invoices = await stats_extra.invoice_conversion(db, scope, start, end)
    new_total, new_paying = await stats_extra.new_client_conversion(db, scope, start, end)
    waiting_now, waiting_over = await stats_extra.awaiting_now(db, scope)
    avg_check = round(amount / count) if count else None
    prev_avg_check = round(prev_amount / prev_count) if prev_count else None

    return {
        "sales_amount": amount,
        "sales_count": count,
        # Прирост не считается от нуля: делить не на что, честнее показать прочерк.
        "sales_amount_delta_pct": (
            round((amount - prev_amount) / prev_amount * 100, 1) if prev_amount else None
        ),
        "sales_count_delta": count - prev_count if prev_count else None,
        "avg_check": avg_check,
        "avg_check_delta_pct": (
            round((avg_check - prev_avg_check) / prev_avg_check * 100, 1)
            if avg_check is not None and prev_avg_check
            else None
        ),
        "avg_response_seconds": speed.avg_seconds,
        "response_goal_minutes": goal,
        "late_threshold_minutes": banner,
        "waits_total": speed.waits,
        "waits_in_goal": speed.in_goal,
        "waits_decided": speed.decided,
        "in_goal_pct": speed.in_goal_pct,
        "late_count": speed.late,
        "awaiting_reply_now": waiting_now,
        "awaiting_reply_over_threshold": waiting_over,
        "active_conversations": await _active_conversations(db, scope, start, end),
        "new_clients": new_total,
        "new_clients_paying": new_paying,
        "new_clients_conversion_pct": (
            round(new_paying / new_total * 100, 1) if new_total else None
        ),
        "invoices_sent": invoices["sent"],
        "invoices_paid": invoices["paid"],
        "invoices_awaiting": invoices["awaiting"],
        "invoices_cancelled": invoices["cancelled"],
        "invoices_expired": invoices["expired"],
        "invoices_sent_amount": invoices["sent_amount"],
        "invoices_paid_amount": invoices["paid_amount"],
        "invoice_conversion_pct": (
            round(invoices["paid"] / invoices["sent"] * 100, 1) if invoices["sent"] else None
        ),
        "awaiting_amount": awaiting_amount,
        "awaiting_count": awaiting_count,
    }


async def services(
    db: AsyncSession, user: User, date_from: date, date_to: date, user_id: int | None
) -> dict[str, Any]:
    """Продажи в разрезе услуг — у менеджера свои, у руководителя все."""
    if date_from > date_to:
        raise Invalid("Начало периода позже его конца")
    scope = await build_scope(db, user, user_id)
    if scope.empty:
        return {"rows": [], "total_revenue": 0}
    start, end = await day_bounds(db, date_from, date_to)
    return await stats_extra.by_service(db, scope, start, end)


async def accounts(
    db: AsyncSession, user: User, date_from: date, date_to: date, user_id: int | None
) -> list[dict[str, Any]]:
    """Разрез по аккаунтам (направлениям воронки) — ТЗ 01.09, «расширенная аналитика»."""
    if date_from > date_to:
        raise Invalid("Начало периода позже его конца")
    scope = await build_scope(db, user, user_id)
    if scope.empty:
        return []
    start, end = await day_bounds(db, date_from, date_to)
    return await stats_extra.by_account(db, scope, start, end)


async def series(
    db: AsyncSession,
    user: User,
    date_from: date,
    date_to: date,
    granularity: str,
    user_id: int | None,
) -> dict[str, Any]:
    if date_from > date_to:
        raise Invalid("Начало периода позже его конца")
    if granularity not in {"day", "week", "month"}:
        raise Invalid("Гранулярность может быть day, week или month")
    if granularity == "day" and (date_to - date_from).days > MAX_DAYS_FOR_DAILY:
        raise Invalid("При периоде больше двух месяцев дни недоступны")

    scope = await build_scope(db, user, user_id)
    if scope.empty:
        return {"points": [], "granularity": granularity}

    start, end = await day_bounds(db, date_from, date_to)
    tz_name = await settings_service.get_value(db, "timezone")
    # Корзины считаются в НАИВНОМ локальном времени (после одного AT TIME ZONE,
    # без обратного перевода в timestamptz): месяц/неделя — календарная
    # арифметика, а не сдвиг по UTC-смещению. Если шагать интервалом прямо по
    # timestamptz, "+1 month" переносится в часовом поясе СЕССИИ Postgres, а не
    # организации, и корзины уезжают на день, дальше — больше с каждым шагом.
    sql = text(
        f"""
        with buckets as (
            select generate_series(
                date_trunc('{granularity}', cast(:start as timestamptz) at time zone :tz),
                date_trunc(
                    '{granularity}',
                    (cast(:end as timestamptz) - interval '1 day') at time zone :tz
                ),
                interval '1 {granularity}'
            ) as bucket
        ),
        sold as (
            select date_trunc('{granularity}', d.paid_at at time zone :tz) as bucket,
                   sum(d.total_amount) as amount, count(*) as cnt
            from deals d
            where d.status = :paid and d.paid_at >= :start and d.paid_at < :end
            {_seller_clause(scope)}
            group by 1
        )
        select b.bucket::date as day,
               -- Границы, обрезанные выбранным периодом. Крайняя корзина почти
               -- всегда неполная: при периоде с 15 июля неделя начинается 13-го,
               -- а деньги за 13–14 июля в неё не попали. Подпись «13–19 июл»
               -- была бы враньём — столбик показывал бы меньше, чем неделя
               -- заработала. Отдаём честные границы, и подпись читается
               -- «15–19 июл»: ровно то, что посчитано.
               greatest(b.bucket, (cast(:start as timestamptz) at time zone :tz))::date
                   as period_start,
               (least(
                   b.bucket + interval '1 {granularity}',
                   (cast(:end as timestamptz) at time zone :tz)
               ) - interval '1 day')::date as period_end,
               coalesce(s.amount, 0) as amount,
               coalesce(s.cnt, 0) as cnt
        from buckets b left join sold s on s.bucket = b.bucket
        order by b.bucket
        """
    )
    rows = (await db.execute(sql, {**_params(scope, start, end), "tz": tz_name})).all()
    return {
        "points": [
            {
                "date": row.day.isoformat(),
                "period_start": row.period_start.isoformat(),
                "period_end": row.period_end.isoformat(),
                "amount": int(row.amount),
                "count": int(row.cnt),
            }
            for row in rows
        ],
        "granularity": granularity,
    }


async def managers(
    db: AsyncSession, date_from: date, date_to: date
) -> list[dict[str, Any]]:
    """Разрез по менеджерам — только для руководителя."""
    start, end = await day_bounds(db, date_from, date_to)
    sql = text(
        """
        select u.id, u.full_name, u.avatar_color,
               coalesce(sales.amount, 0) as sales_amount,
               coalesce(sales.cnt, 0) as sales_count,
               coalesce(awaiting.cnt, 0) as awaiting_count,
               coalesce(active.cnt, 0) as active_conversations,
               coalesce(invoices.sent, 0) as invoices_sent,
               coalesce(invoices.paid, 0) as invoices_paid,
               coalesce(waiting.cnt, 0) as awaiting_reply_now
        from users u
        left join (
            select d.sold_by_id as uid, sum(d.total_amount) as amount, count(*) as cnt
            from deals d
            where d.status = 'paid' and d.paid_at >= :start and d.paid_at < :end
            group by 1
        ) sales on sales.uid = u.id
        left join (
            select d.sold_by_id as uid, count(*) as cnt
            from deals d where d.status = 'awaiting' group by 1
        ) awaiting on awaiting.uid = u.id
        left join (
            select c.responsible_id as uid, count(distinct c.id) as cnt
            from conversations c
            join messages m on m.conversation_id = c.id
            where m.created_at >= :start and m.created_at < :end and m.deleted_at is null
            group by 1
        ) active on active.uid = u.id
        left join (
            select d.sold_by_id as uid, count(*) as sent,
                   count(*) filter (where d.status = 'paid') as paid
            from deals d
            where d.sent_at >= :start and d.sent_at < :end
            group by 1
        ) invoices on invoices.uid = u.id
        left join (
            select c.responsible_id as uid, count(*) as cnt
            from conversations c
            where c.awaiting_reply_since is not null
            group by 1
        ) waiting on waiting.uid = u.id
        -- Только менеджеры: это таблица их скорости и нагрузки, а не общей кассы.
        -- Руководитель сюда не входит, даже если сам закрыл продажу или подстраховал
        -- чат (docs/03-business-rules.md §10, 2026-09-17) — его деньги по-прежнему
        -- видны в общей сумме продаж наверху, просто не приписаны ни одному менеджеру.
        -- Сумма строк поэтому может быть меньше общей цифры — так же, как и с
        -- «чатов в работе» (см. пояснение в интерфейсе).
        where u.deleted_at is null and u.role = 'manager'
        order by sales_amount desc, u.full_name
        """
    )
    rows = (await db.execute(sql, {"start": start, "end": end})).all()
    speed = await stats_extra.response_stats(
        db, Scope(None, None), start, end, group_by="responsible"
    )

    result = []
    for row in rows:
        stats = speed.get(int(row.id))
        count = int(row.sales_count)
        sent = int(row.invoices_sent)
        result.append(
            {
                "user": {
                    "id": row.id,
                    "full_name": row.full_name,
                    "avatar_color": row.avatar_color,
                },
                "sales_amount": int(row.sales_amount),
                "sales_count": count,
                "avg_check": round(int(row.sales_amount) / count) if count else None,
                "avg_response_seconds": stats.avg_seconds if stats else None,
                "in_goal_pct": stats.in_goal_pct if stats else None,
                "late_count": stats.late if stats else 0,
                "active_conversations": int(row.active_conversations),
                "awaiting_count": int(row.awaiting_count),
                "awaiting_reply_now": int(row.awaiting_reply_now),
                "invoices_sent": sent,
                "invoices_paid": int(row.invoices_paid),
                "invoice_conversion_pct": (
                    round(int(row.invoices_paid) / sent * 100, 1) if sent else None
                ),
            }
        )
    return result


async def personal_avg_response_seconds(db: AsyncSession, user_id: int) -> int | None:
    """Среднее время ответа конкретного сотрудника за текущий месяц.

    Отвечающим считается автор исходящего: именно он закрыл ожидание клиента.
    Служебные заметки исключены — они клиенту не уходят.

    Важно: «следующее исходящее» ищем среди сообщений ЛЮБОГО автора (как и в
    остальных метриках времени ответа), а не сразу фильтруем по :uid — иначе
    при передаче диалога или подмене коллегой ожидание, которое коллега закрыл
    за минуту, приписалось бы этому сотруднику по времени его следующего,
    никак не связанного сообщения в том же диалоге — то есть завышало бы
    его личное время часами. `array_agg(...)[1]` в окне с тем же порядком,
    что и next_out, — это автор именно ТОГО сообщения, что и закрыло ожидание
    (FILTER для window-функций работает только с агрегатами, поэтому не
    first_value, а array_agg с выборкой первого элемента).
    """
    start, end = await worktime.current_month_bounds(db)
    sql = text(
        """
        with base as (
            select m.conversation_id, m.direction, m.created_at,
                   lag(m.direction) over (
                       partition by m.conversation_id order by m.created_at, m.id
                   ) as prev_direction,
                   min(m.created_at) filter (where m.direction = 'out') over (
                       partition by m.conversation_id order by m.created_at, m.id
                       rows between 1 following and unbounded following
                   ) as next_out,
                   (array_agg(m.author_id) filter (where m.direction = 'out') over (
                       partition by m.conversation_id order by m.created_at, m.id
                       rows between 1 following and unbounded following
                   ))[1] as next_out_author_id
            from messages m
            where m.deleted_at is null and m.is_internal = false
              and m.created_at >= :start and m.created_at < :end
        )
        select avg(ELAPSED)::numeric
        from base
        where direction = 'in'
          and (prev_direction is null or prev_direction = 'out')
          and next_out is not null
          and next_out_author_id = :uid
        """
    )
    elapsed, extra = await _elapsed_sql(db)
    value = (
        await db.execute(
            text(str(sql).replace("ELAPSED", elapsed)),
            {"uid": user_id, "start": start, "end": end, **extra},
        )
    ).scalar_one_or_none()
    return None if value is None else int(round(float(value)))
