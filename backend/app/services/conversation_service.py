"""Диалоги: список чатов, счётчики, очередь, чтение и передача.

Список чатов — самый горячий экран системы, поэтому строка собирается одним
запросом (диалог + клиент + аккаунт + ответственный + последнее сообщение)
и одним запросом по сделкам на всю страницу. Дозагрузок на строку нет.

Видимость: руководитель видит всё, менеджер — только диалоги своих аккаунтов.
Чужой диалог для менеджера не существует: 404, а не 403.
"""

import base64
import binascii
import contextlib
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from sqlalchemy import (
    ARRAY,
    Integer,
    Select,
    String,
    Time,
    and_,
    case,
    cast,
    exists,
    func,
    literal,
    or_,
    select,
    true,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, contains_eager
from sqlalchemy.sql.elements import ColumnElement

from app.core import command_bus
from app.core.config import settings
from app.core.deps import conversation_scope_orm, visible_account_ids
from app.core.errors import Invalid, NotFound
from app.models import (
    AccountManager,
    Client,
    Conversation,
    Deal,
    DealStatus,
    Direction,
    Message,
    MessageKind,
    TelegramAccount,
    User,
)
from app.realtime.events import conversation_audience, emit
from app.schemas.common import CursorPage
from app.schemas.conversation import (
    AccountBrief,
    ClientBrief,
    ClientInChat,
    ConversationDetail,
    ConversationRow,
    Counters,
    UserBrief,
)
from app.services import settings_service, worktime
from app.services.audit import log_event

PREVIEW_LIMIT = 120
DEFAULT_LIMIT = 30
MAX_LIMIT = 100
DEFAULT_BANNER_MINUTES = 30

ListFilter = Literal["all", "awaiting", "awaiting_payment"]

# Чем подписать строку списка, если в последнем сообщении нет текста.
KIND_PREVIEW: dict[str, str] = {
    MessageKind.PHOTO.value: "Фото",
    MessageKind.VIDEO.value: "Видео",
    MessageKind.VOICE.value: "Голосовое сообщение",
    MessageKind.DOCUMENT.value: "Файл",
    MessageKind.SERVICE.value: "Служебное сообщение",
}


# --- курсор -----------------------------------------------------------------


def encode_cursor(sort_value: datetime | None, row_id: int) -> str:
    """Курсор непрозрачен для клиента: внутри — значение сортировки и id.

    Пара нужна потому, что времена повторяются, а курсор обязан быть однозначным.
    """
    raw = f"{sort_value.isoformat() if sort_value else ''}|{row_id}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def decode_cursor(cursor: str | None) -> tuple[datetime | None, int] | None:
    """Битый курсор — не ошибка запроса: просто читаем с начала."""
    if not cursor:
        return None
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        stamp, _, row_id = raw.partition("|")
        return (datetime.fromisoformat(stamp) if stamp else None, int(row_id))
    except (ValueError, binascii.Error, UnicodeDecodeError):
        return None


def keyset_condition(
    sort_col: Any, id_col: Any, cursor: tuple[datetime | None, int]
) -> ColumnElement[bool]:
    """Условие «строго после курсора» для сортировки по убыванию с NULLS LAST."""
    stamp, row_id = cursor
    if stamp is None:
        return and_(sort_col.is_(None), id_col < row_id)
    return or_(
        sort_col < stamp,
        and_(sort_col == stamp, id_col < row_id),
        sort_col.is_(None),
    )


# --- запросы ----------------------------------------------------------------


def _scoped(stmt: Select[Any], user: User, account_ids: list[int] | None) -> Select[Any]:
    """Ограничение выдачи правами сотрудника.

    Руководитель видит всё. Менеджер — свои диалоги (где он ответственный) и
    ничейные на своих аккаунтах: последние нужны, чтобы работала кнопка «Взять
    первого в очереди». Чужой назначенный диалог для него не существует.
    """
    conditions = conversation_scope_orm(user, account_ids, Conversation)
    return stmt.where(*conditions) if conditions else stmt


def _row_stmt() -> Select[Any]:
    last = (
        select(Message.text, Message.direction, Message.kind)
        .where(Message.conversation_id == Conversation.id, Message.deleted_at.is_(None))
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(1)
        .lateral("last_msg")
    )
    return (
        select(Conversation, User, last.c.text, last.c.direction, last.c.kind)
        .join(Client, Client.id == Conversation.client_id)
        .join(TelegramAccount, TelegramAccount.id == Conversation.account_id)
        .outerjoin(User, User.id == Conversation.responsible_id)
        .outerjoin(last, true())
        .options(contains_eager(Conversation.client), contains_eager(Conversation.account))
    )


def _awaiting_deal_exists() -> ColumnElement[bool]:
    return exists(
        select(Deal.id).where(
            Deal.conversation_id == Conversation.id,
            Deal.status == DealStatus.AWAITING,
        )
    )


def _search_condition(q: str) -> ColumnElement[bool]:
    """Поиск по имени, телефону, id клиента и тексту переписки."""
    like = f"%{q}%"
    conds: list[ColumnElement[bool]] = [
        Client.display_name.ilike(like),
        Client.tg_first_name.ilike(like),
        Client.tg_last_name.ilike(like),
        Client.tg_username.ilike(like),
        Client.phone.ilike(like),
        exists(
            select(Message.id).where(
                Message.conversation_id == Conversation.id,
                Message.text.ilike(like),
            )
        ),
    ]
    if q.isdigit():
        conds.append(Client.id == int(q))
    return or_(*conds)


async def _awaiting_deal_amounts(db: AsyncSession, ids: list[int]) -> dict[int, int]:
    """Последняя сделка «ждёт оплаты» по каждому диалогу — одним запросом на страницу."""
    if not ids:
        return {}
    stmt = (
        select(Deal.conversation_id, Deal.total_amount)
        .distinct(Deal.conversation_id)
        .where(Deal.conversation_id.in_(ids), Deal.status == DealStatus.AWAITING)
        .order_by(Deal.conversation_id, Deal.created_at.desc(), Deal.id.desc())
    )
    return {row[0]: row[1] for row in (await db.execute(stmt)).all()}


# --- сборка строки ----------------------------------------------------------


def _preview(text: str | None, direction: Direction | None, kind: MessageKind | None) -> str | None:
    if direction is None:
        return None
    body = (text or "").strip()
    if not body and kind is not None:
        body = KIND_PREVIEW.get(str(kind), "")
    if not body:
        return None
    if len(body) > PREVIEW_LIMIT:
        body = body[: PREVIEW_LIMIT - 1].rstrip() + "…"
    return f"Вы: {body}" if direction == Direction.OUT else body


def _row_data(
    row: Any, deal_amount: int | None, now: datetime, hours: worktime.WorkHours | None = None
) -> dict[str, Any]:
    conv, responsible, text, direction, kind = row
    awaiting = conv.awaiting_reply_since
    # Плашку не показываем, если менеджер уже открывал чат после начала ожидания.
    seen = conv.awaiting_seen_at is not None and (
        awaiting is None or conv.awaiting_seen_at >= awaiting
    )
    return {
        "id": conv.id,
        "client": ClientBrief.model_validate(conv.client),
        "account": AccountBrief.model_validate(conv.account),
        "last_message_at": conv.last_message_at,
        "last_message_preview": _preview(text, direction, kind),
        "unread_count": conv.unread_count,
        "awaiting_reply_since": awaiting,
        # Менеджер уже открывал чат после начала ожидания: диалог по-прежнему
        # ждёт ответа, но напоминание своё отработало (ТЗ 2.1).
        "awaiting_seen": seen,
        # Время ожидания в рабочих часах, если они включены в настройках: клиент,
        # написавший в 23:40, не ждёт «десять часов» — компания просто не работала.
        "awaiting_minutes": (
            None
            if (awaiting is None or seen)
            else int(
                (
                    hours.seconds_between(awaiting, now)
                    if hours is not None
                    else (now - awaiting).total_seconds()
                )
                // 60
            )
        ),
        "responsible": UserBrief.model_validate(responsible) if responsible else None,
        "has_awaiting_deal": deal_amount is not None,
        "awaiting_deal_amount": deal_amount,
        "is_blocked_by_client": conv.is_blocked_by_client,
        "updated_at": conv.updated_at,
    }


# --- сценарии ---------------------------------------------------------------


async def list_conversations(
    db: AsyncSession,
    user: User,
    *,
    filter_: ListFilter = "all",
    account_id: int | None = None,
    q: str | None = None,
    cursor: str | None = None,
    limit: int = DEFAULT_LIMIT,
) -> CursorPage[ConversationRow]:
    limit = max(1, min(limit, MAX_LIMIT))
    stmt = _scoped(_row_stmt(), user, await visible_account_ids(db, user))
    if account_id is not None:
        stmt = stmt.where(Conversation.account_id == account_id)
    if filter_ == "awaiting":
        stmt = stmt.where(Conversation.awaiting_reply_since.isnot(None))
    elif filter_ == "awaiting_payment":
        stmt = stmt.where(_awaiting_deal_exists())
    if q and q.strip():
        stmt = stmt.where(_search_condition(q.strip()))
    parsed = decode_cursor(cursor)
    if parsed:
        stmt = stmt.where(keyset_condition(Conversation.last_message_at, Conversation.id, parsed))
    stmt = stmt.order_by(
        Conversation.last_message_at.desc().nulls_last(), Conversation.id.desc()
    ).limit(limit + 1)

    rows = (await db.execute(stmt)).unique().all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    deals = await _awaiting_deal_amounts(db, [row[0].id for row in rows])
    now = datetime.now(UTC)
    hours = await worktime.load(db)
    items = [ConversationRow(**_row_data(row, deals.get(row[0].id), now, hours)) for row in rows]
    next_cursor = (
        encode_cursor(rows[-1][0].last_message_at, rows[-1][0].id) if has_more and rows else None
    )
    return CursorPage[ConversationRow](items=items, next_cursor=next_cursor)


async def counters(db: AsyncSession, user: User) -> Counters:
    """Счётчики над списком. `over_threshold` — те, кто ждёт дольше порога из настроек."""
    minutes = int(await settings_service.get_value(db, "awaiting_banner_minutes") or 0)
    minutes = minutes or DEFAULT_BANNER_MINUTES
    hours = await worktime.load(db)
    now = datetime.now(UTC)
    cutoff = now - timedelta(minutes=minutes)
    if hours.enabled:
        # Порог считаем в рабочих секундах: ночью ожидание не растёт, и плашка
        # не встречает менеджера утром сообщением «клиент ждёт 11 часов».
        params = hours.sql_params()
        waited = func.astra_working_seconds(
            Conversation.awaiting_reply_since,
            now,
            cast(literal(params["wh_days"]), ARRAY(Integer)),
            cast(literal(params["wh_start"]), Time),
            cast(literal(params["wh_end"]), Time),
            cast(literal(params["wh_tz"]), String),
        )
        over_condition = waited > minutes * 60
    else:
        over_condition = Conversation.awaiting_reply_since < cutoff
    stmt = _scoped(
        select(
            func.count(),
            func.count().filter(Conversation.awaiting_reply_since.isnot(None)),
            func.count().filter(_awaiting_deal_exists()),
            # Баннер тоже гаснет для просмотренных: ТЗ п. 2.1 — срабатывает один раз.
            func.count().filter(
                over_condition,
                or_(
                    Conversation.awaiting_seen_at.is_(None),
                    Conversation.awaiting_seen_at < Conversation.awaiting_reply_since,
                ),
            ),
        ).select_from(Conversation),
        user,
        await visible_account_ids(db, user),
    )
    total, awaiting, awaiting_payment, over_threshold = (await db.execute(stmt)).one()
    return Counters(
        total=total,
        awaiting=awaiting,
        awaiting_payment=awaiting_payment,
        over_threshold=over_threshold,
        over_threshold_minutes=minutes,
    )


async def next_in_queue(db: AsyncSession, user: User) -> ConversationRow:
    """«Взять первого в очереди»: сначала тот, кто ждёт дольше всех.

    Если никто не ждёт ответа — самый свежий по последнему сообщению.
    """
    stmt = (
        _scoped(_row_stmt(), user, await visible_account_ids(db, user))
        .order_by(
            case((Conversation.awaiting_reply_since.is_(None), 1), else_=0),
            Conversation.awaiting_reply_since.asc(),
            Conversation.last_message_at.desc().nulls_last(),
            Conversation.id.desc(),
        )
        .limit(1)
    )
    row = (await db.execute(stmt)).unique().first()
    if row is None:
        raise NotFound("Нет чатов в работе")
    deals = await _awaiting_deal_amounts(db, [row[0].id])
    return ConversationRow(**_row_data(row, deals.get(row[0].id), datetime.now(UTC)))


async def load_visible(db: AsyncSession, user: User, conversation_id: int) -> Conversation:
    """Диалог, который этот пользователь вправе видеть. Чужой — 404, а не 403."""
    stmt = _scoped(
        select(Conversation).where(Conversation.id == conversation_id),
        user,
        await visible_account_ids(db, user),
    )
    conv = (await db.execute(stmt)).unique().scalar_one_or_none()
    if conv is None:
        raise NotFound("Чат не найден")
    return conv


async def get_detail(db: AsyncSession, user: User, conversation_id: int) -> ConversationDetail:
    await load_visible(db, user, conversation_id)
    return await build_detail(db, conversation_id)


async def build_detail(db: AsyncSession, conversation_id: int) -> ConversationDetail:
    """Карточка чата с данными рождения и итогами оплат клиента."""
    row = (await db.execute(_row_stmt().where(Conversation.id == conversation_id))).unique().first()
    if row is None:
        raise NotFound("Чат не найден")
    conv = row[0]
    deals = await _awaiting_deal_amounts(db, [conv.id])
    # ТЗ п. 2.3: в шапке чата считаются оплаты только этого аккаунта. Клиент может
    # писать в несколько аккаунтов, и складывать их в одну цифру нельзя — менеджер
    # видит чужие продажи как свои.
    deal_conv = aliased(Conversation)
    paid_amount, paid_count = (
        await db.execute(
            select(func.coalesce(func.sum(Deal.total_amount), 0), func.count())
            .select_from(Deal)
            .join(deal_conv, deal_conv.id == Deal.conversation_id)
            .where(
                Deal.client_id == conv.client_id,
                Deal.status == DealStatus.PAID,
                deal_conv.account_id == conv.account_id,
            )
        )
    ).one()
    data = _row_data(row, deals.get(conv.id), datetime.now(UTC))
    data["client"] = ClientInChat.model_validate(conv.client)
    return ConversationDetail(
        **data, client_paid_amount=paid_amount, client_paid_count=paid_count
    )


async def emit_updated(db: AsyncSession, detail: ConversationDetail) -> None:
    """Одна аудитория на оба события: считать её дважды незачем.

    В `counters.updated` не числа: счётчики зависят от прав получателя,
    поэтому событие — сигнал перезапросить `/conversations/counters`.
    """
    audience = await conversation_audience(db, detail.id)
    await emit("conversation.updated", {"conversation": detail.model_dump(mode="json")}, audience)
    await emit("counters.updated", {"conversation_id": detail.id, "stale": True}, audience)


async def mark_read(db: AsyncSession, user: User, conversation_id: int) -> ConversationDetail:
    conv = await load_visible(db, user, conversation_id)
    before = conv.unread_count
    conv.unread_count = 0
    # ТЗ п. 2.1: менеджер открыл чат — плашка ожидания своё отработала.
    # Само ожидание остаётся: клиент по-прежнему без ответа, и в счётчике
    # «ждут ответа» диалог тоже остаётся. Гаснет именно напоминание.
    if conv.awaiting_reply_since is not None:
        conv.awaiting_seen_at = datetime.now(UTC)
    await log_event(
        db,
        action="conversation.read",
        entity_type="conversation",
        entity_id=conv.id,
        actor=user,
        before={"unread_count": before},
        after={"unread_count": 0},
    )
    await db.commit()

    # Прочитано в CRM — гасим непрочитанное и в самом Telegram, иначе владелец
    # аккаунта видит на телефоне счётчик, который никто не убирает. Недоступный
    # шлюз здесь не ошибка: сообщения прочитаны, состояние в CRM уже сохранено.
    if before and not settings.demo_mode:
        last_tg_id = await db.scalar(
            select(func.max(Message.tg_message_id)).where(
                Message.conversation_id == conv.id, Message.direction == Direction.IN
            )
        )
        if last_tg_id:
            with contextlib.suppress(Exception):
                await command_bus.call(
                    conv.account_id,
                    "mark_read",
                    {"chat_id": conv.tg_chat_id, "max_id": int(last_tg_id)},
                    timeout=10,
                )

    detail = await build_detail(db, conversation_id)
    await emit_updated(db, detail)
    return detail


async def transfer(
    db: AsyncSession, user: User, conversation_id: int, target_user_id: int
) -> ConversationDetail:
    """Передать чат другому менеджеру. Передать можно только тому, кто ведёт аккаунт."""
    conv = await load_visible(db, user, conversation_id)
    target = await db.get(User, target_user_id)
    if target is None or not target.is_active or target.deleted_at is not None:
        raise Invalid("Менеджер не назначен на этот аккаунт")
    assigned = await db.scalar(
        select(func.count())
        .select_from(AccountManager)
        .where(
            AccountManager.account_id == conv.account_id,
            AccountManager.user_id == target.id,
        )
    )
    if not assigned:
        raise Invalid("Менеджер не назначен на этот аккаунт")

    before = conv.responsible_id
    conv.responsible_id = target.id
    conv.responsible_since = datetime.now(UTC)
    await log_event(
        db,
        action="conversation.transferred",
        entity_type="conversation",
        entity_id=conv.id,
        actor=user,
        before={"responsible_id": before},
        after={"responsible_id": target.id},
    )
    await db.commit()
    detail = await build_detail(db, conversation_id)
    await emit_updated(db, detail)
    return detail
