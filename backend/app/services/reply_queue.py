"""Очередь ответов: кто и как давно ждёт (D-34, docs/18-crm-remarks-2026-10.md).

Одно определение на весь продукт. Баннер над списком чатов, плитка «ждут ответа
сейчас» в статистике и фильтр «Ждут >N мин» считают одно и то же число, потому
что берут условие и область видимости отсюда:

- **«ждёт дольше порога»** — `awaiting_reply_since` не пуст и старше порога
  `awaiting_banner_minutes`; при включённых рабочих часах возраст считается в
  рабочих секундах (ночью ожидание не растёт);
- **область видимости** — руководитель видит всё, менеджер — свои диалоги и
  ничейные на своих аккаунтах (`conversation_scope_orm`);
- **«просмотрено»** (`awaiting_seen_at`) в число НЕ входит: оно нужно только
  значку на строке чата и порядку «Взять первого в очереди».

Модуль не знает про HTTP и про строки списка — только условия, порядок, курсор.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import (
    ARRAY,
    Integer,
    String,
    Time,
    and_,
    case,
    func,
    literal,
    or_,
    select,
    true,
)
from sqlalchemy import cast as sql_cast
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.core.deps import conversation_scope_orm, visible_account_ids
from app.core.errors import Invalid
from app.models import Conversation, User
from app.services import settings_service, worktime

DEFAULT_BANNER_MINUTES = 30
# Сколько уже взятых чатов клиент может передать в `GET /conversations/next`.
MAX_SKIP = 50


@dataclass(frozen=True, slots=True)
class Threshold:
    """Порог «ждёт долго» вместе с тем, как мерить время: на один запрос — одно «сейчас»."""

    minutes: int
    hours: worktime.WorkHours
    now: datetime


async def load_threshold(db: AsyncSession, now: datetime | None = None) -> Threshold:
    minutes = int(await settings_service.get_value(db, "awaiting_banner_minutes") or 0)
    return Threshold(
        minutes=minutes or DEFAULT_BANNER_MINUTES,
        hours=await worktime.load(db),
        now=now or datetime.now(UTC),
    )


def over_threshold(
    threshold: Threshold, since: Any = Conversation.awaiting_reply_since
) -> ColumnElement[bool]:
    """Ждёт ответа дольше порога. Пустое `since` (ответ дан) условию не удовлетворяет.

    Намеренно без `awaiting_seen_at`: просмотр не отменяет того, что клиент ждёт.
    """
    hours = threshold.hours
    if hours.enabled:
        # Порог считаем в рабочих секундах: ночью ожидание не растёт, и плашка
        # не встречает менеджера утром сообщением «клиент ждёт 11 часов».
        params = hours.sql_params()
        waited = func.astra_working_seconds(
            since,
            threshold.now,
            sql_cast(literal(params["wh_days"]), ARRAY(Integer)),
            sql_cast(literal(params["wh_start"]), Time),
            sql_cast(literal(params["wh_end"]), Time),
            sql_cast(literal(params["wh_tz"]), String),
        )
        return and_(since.isnot(None), waited > threshold.minutes * 60)
    return since < threshold.now - timedelta(minutes=threshold.minutes)


def unseen(
    since: Any = Conversation.awaiting_reply_since,
    seen_at: Any = Conversation.awaiting_seen_at,
) -> ColumnElement[bool]:
    """Менеджер ещё не открывал чат с начала этого ожидания (значок «ждёт N мин» на строке)."""
    return or_(seen_at.is_(None), seen_at < since)


async def scope_conditions(db: AsyncSession, user: User | None) -> list[Any]:
    """Какие диалоги видит сотрудник — условия для запроса.

    Это `conversation_scope_orm` с теми же видимыми аккаунтами, что у списка
    чатов (`visible_account_ids`): других правил нет. `None` и руководитель —
    видят всё. Статистика, показывая очередь «от имени» менеджера, передаёт
    сюда его самого — и получает ровно его баннер.
    """
    if user is None:
        return []
    return conversation_scope_orm(user, await visible_account_ids(db, user), Conversation)


async def count_waiting(db: AsyncSession, user: User | None) -> tuple[int, int]:
    """Сколько диалогов ждут ответа и сколько из них — дольше порога. Область — одна и та же."""
    threshold = await load_threshold(db)
    stmt = (
        select(func.count(), func.count().filter(over_threshold(threshold)))
        .select_from(Conversation)
        .where(Conversation.awaiting_reply_since.isnot(None))
        .where(*await scope_conditions(db, user))
    )
    total, over = (await db.execute(stmt)).one()
    return int(total), int(over)


# --- порядок и курсор -------------------------------------------------------


def keyset_after(
    sort_col: Any, id_col: Any, cursor: tuple[datetime | None, int]
) -> ColumnElement[bool]:
    """Условие «строго после курсора» для сортировки по ВОЗРАСТАНИЮ (давно ждущие сверху).

    Пара (время, id) однозначна даже при одинаковых временах. Только для
    столбцов без NULL — в списках «ждут» пустого ожидания не бывает. Курсор без
    времени пришёл не из этой сортировки — читаем с начала, как и с битым.
    """
    stamp, row_id = cursor
    if stamp is None:
        return true()
    return or_(sort_col > stamp, and_(sort_col == stamp, id_col > row_id))


def next_order(since: Any, seen_at: Any, last_message_at: Any, row_id: Any) -> list[Any]:
    """Порядок «Взять первого в очереди».

    1. Ждут и ещё не открыты — по давности ожидания;
    2. ждут, но уже открыты — тоже по давности;
    3. остальные — самый свежий разговор.

    Внутри ждущих при равном времени — по возрастанию id, как в списке чатов.
    """
    group = case((since.is_(None), 2), (unseen(since, seen_at), 0), else_=1)
    waiting_tie = case((since.is_(None), 0), else_=row_id)
    return [
        group,
        since.asc(),
        waiting_tie.asc(),
        last_message_at.desc().nulls_last(),
        row_id.desc(),
    ]


def parse_skip(raw: str | None) -> list[int]:
    """`?skip=5,3,9` → [5, 3, 9]. Порядок сохраняется, повторы убираются, не больше MAX_SKIP."""
    if raw is None:
        return []
    ids: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if not part.isascii() or not part.isdigit() or int(part) <= 0:
            raise Invalid("skip — список номеров чатов через запятую", code="invalid_skip")
        value = int(part)
        if value not in ids:
            ids.append(value)
    if len(ids) > MAX_SKIP:
        raise Invalid(f"В skip не больше {MAX_SKIP} чатов", code="invalid_skip")
    return ids
