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
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import (
    Select,
    and_,
    exists,
    func,
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
    Attachment,
    AttachmentKind,
    Client,
    Conversation,
    Deal,
    DealStatus,
    Direction,
    Message,
    MessageKind,
    TelegramAccount,
    User,
    UserRole,
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
from app.services import name_match, reply_queue, worktime
from app.services.audit import log_event

PREVIEW_LIMIT = 120
DEFAULT_LIMIT = 30
MAX_LIMIT = 100
DEFAULT_BANNER_MINUTES = reply_queue.DEFAULT_BANNER_MINUTES
QUEUE_DONE = "Больше нет чатов в очереди"

# `awaiting_over` — ждут ответа дольше порога из настроек: те же диалоги, что в
# баннере над списком и в плитке статистики (D-34). Оба «ждущих» фильтра идут
# от самого давнего ожидания к свежему.
ListFilter = Literal["all", "awaiting", "awaiting_over", "awaiting_payment"]

# Чем подписать строку списка, если в последнем сообщении нет текста.
KIND_PREVIEW: dict[str, str] = {
    MessageKind.PHOTO.value: "Фото",
    MessageKind.VIDEO.value: "Видео",
    MessageKind.VOICE.value: "Голосовое сообщение",
    MessageKind.AUDIO.value: "Аудио",
    MessageKind.STICKER.value: "Стикер",
    MessageKind.DOCUMENT.value: "Файл",
    MessageKind.SERVICE.value: "Служебное сообщение",
}
# Вид сообщения грубый (музыка и стикер — «документ», см. MessageKind), точный —
# у вложения. Где он уточняет подпись, берём его — как делает сам Telegram.
ATTACHMENT_PREVIEW: dict[str, str] = {
    AttachmentKind.AUDIO.value: "Аудио",
    AttachmentKind.STICKER.value: "Стикер",
    AttachmentKind.VIDEO_NOTE.value: "Видеосообщение",
    AttachmentKind.ANIMATION.value: "GIF",
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
    first_attachment_kind = (
        select(Attachment.kind)
        .where(Attachment.message_id == Message.id, Attachment.deleted_at.is_(None))
        .order_by(Attachment.id)
        .limit(1)
        .scalar_subquery()
    )
    last = (
        select(
            Message.text,
            Message.direction,
            Message.kind,
            first_attachment_kind.label("attachment_kind"),
            # Для значка статуса в превью (D-36): тем же подзапросом, без N+1.
            Message.id.label("last_id"),
            Message.status.label("last_status"),
            Message.is_internal.label("last_is_internal"),
        )
        .where(Message.conversation_id == Conversation.id, Message.deleted_at.is_(None))
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(1)
        .lateral("last_msg")
    )
    return (
        select(
            Conversation,
            User,
            last.c.text,
            last.c.direction,
            last.c.kind,
            last.c.attachment_kind,
            last.c.last_id,
            last.c.last_status,
            last.c.last_is_internal,
        )
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
    """Поиск по клиенту (то же правило слов, что в «Клиентах» и глобальном поиске,
    D-35) и по тексту переписки (запрос целиком, как набран)."""
    conds: list[ColumnElement[bool]] = [
        exists(
            select(Message.id).where(
                Message.conversation_id == Conversation.id,
                Message.text.ilike(name_match.like_contains(q)),
            )
        ),
    ]
    by_client = name_match.client_match_orm(q)
    if by_client is not None:
        conds.append(by_client)
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


def _preview(
    text: str | None,
    direction: Direction | None,
    kind: MessageKind | None,
    attachment_kind: str | None = None,
) -> str | None:
    if direction is None:
        return None
    body = (text or "").strip()
    if not body and attachment_kind:
        body = ATTACHMENT_PREVIEW.get(attachment_kind, "")
    if not body and kind is not None:
        body = KIND_PREVIEW.get(str(kind), "")
    if not body:
        return None
    if len(body) > PREVIEW_LIMIT:
        body = body[: PREVIEW_LIMIT - 1].rstrip() + "…"
    return f"Вы: {body}" if direction == Direction.OUT else body


def can_see_account_reason(user: User) -> bool:
    """Причину обрыва связи аккаунта видит только руководитель.

    Рядом с ней у руководителя кнопка «Переподключить»; менеджеру нужен сам факт
    («аккаунт отключён, сообщите руководителю»), а не технические подробности.
    """
    return user.role == UserRole.ADMIN


def _account_brief(account: TelegramAccount, *, reveal_reason: bool) -> AccountBrief:
    brief = AccountBrief.model_validate(account)
    return brief if reveal_reason else brief.model_copy(update={"status_reason": None})


def _row_data(
    row: Any,
    deal_amount: int | None,
    now: datetime,
    hours: worktime.WorkHours | None = None,
    *,
    reveal_reason: bool = False,
) -> dict[str, Any]:
    """Данные строки. Причина обрыва аккаунта по умолчанию скрыта (`reveal_reason`
    включают только там, где ответ получает руководитель)."""
    conv, responsible, text, direction, kind, attachment_kind, last_id, last_status, internal = row
    awaiting = conv.awaiting_reply_since
    # Значок «ждёт N мин» на строке скрываем, если менеджер уже открывал чат после начала
    # ожидания (баннер и счётчики это не затрагивает, D-34).
    seen = conv.awaiting_seen_at is not None and (
        awaiting is None or conv.awaiting_seen_at >= awaiting
    )
    return {
        "id": conv.id,
        "client": ClientBrief.model_validate(conv.client),
        "account": _account_brief(conv.account, reveal_reason=reveal_reason),
        "last_message_at": conv.last_message_at,
        "last_message_preview": _preview(text, direction, kind, attachment_kind),
        "last_message_id": last_id,
        "last_message_direction": direction,
        # Статус доставки есть только у исходящего клиенту: у входящего и у
        # служебной заметки (в Telegram не уходит) значка в превью нет.
        "last_message_status": (
            last_status if direction == Direction.OUT and not internal else None
        ),
        "unread_count": conv.unread_count,
        "awaiting_reply_since": awaiting,
        # Менеджер уже открывал чат после начала ожидания: диалог по-прежнему ждёт
        # ответа и входит в баннер и счётчики, но значок на строке свою роль отыграл.
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
    elif filter_ == "awaiting_over":
        stmt = stmt.where(reply_queue.over_threshold(await reply_queue.load_threshold(db)))
    elif filter_ == "awaiting_payment":
        stmt = stmt.where(_awaiting_deal_exists())
    if q and q.strip():
        stmt = stmt.where(_search_condition(q.strip()))
    parsed = decode_cursor(cursor)
    # «Ждущие» списки — самые давно ждущие сверху (BUG-04); остальные — по свежести.
    by_waiting = filter_ in ("awaiting", "awaiting_over")
    sort_col = Conversation.awaiting_reply_since if by_waiting else Conversation.last_message_at
    if parsed and by_waiting:
        stmt = stmt.where(reply_queue.keyset_after(sort_col, Conversation.id, parsed))
    elif parsed:
        stmt = stmt.where(keyset_condition(sort_col, Conversation.id, parsed))
    if by_waiting:
        stmt = stmt.order_by(sort_col.asc(), Conversation.id.asc())
    else:
        stmt = stmt.order_by(sort_col.desc().nulls_last(), Conversation.id.desc())
    stmt = stmt.limit(limit + 1)

    rows = (await db.execute(stmt)).unique().all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    deals = await _awaiting_deal_amounts(db, [row[0].id for row in rows])
    now = datetime.now(UTC)
    hours = await worktime.load(db)
    reveal = can_see_account_reason(user)
    items = [
        ConversationRow(**_row_data(row, deals.get(row[0].id), now, hours, reveal_reason=reveal))
        for row in rows
    ]
    last = rows[-1][0] if rows else None
    next_cursor = (
        encode_cursor(last.awaiting_reply_since if by_waiting else last.last_message_at, last.id)
        if has_more and last is not None
        else None
    )
    return CursorPage[ConversationRow](items=items, next_cursor=next_cursor)


async def counters(db: AsyncSession, user: User) -> Counters:
    """Счётчики над списком. `over_threshold` — те, кто ждёт дольше порога из настроек.

    «Дольше порога» и область видимости — из `reply_queue`, как у плитки
    статистики и фильтра списка: во всех трёх одно и то же число (D-34).
    """
    threshold = await reply_queue.load_threshold(db)
    stmt = _scoped(
        select(
            func.count(),
            func.count().filter(Conversation.awaiting_reply_since.isnot(None)),
            func.count().filter(_awaiting_deal_exists()),
            func.count().filter(reply_queue.over_threshold(threshold)),
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
        over_threshold_minutes=threshold.minutes,
    )


async def next_in_queue(
    db: AsyncSession, user: User, skip: Sequence[int] = ()
) -> ConversationRow:
    """«Взять первого в очереди»: сначала тот, кто ждёт дольше всех.

    Очередь — диалоги, ждущие ответа: сперва ещё не открытые (по давности
    ожидания), затем уже открытые, но оставшиеся без ответа. `skip` — чаты,
    которые сотрудник уже брал в этой сессии: они не повторяются, пока в очереди
    есть другие, — иначе повторное нажатие открывало бы тот же чат, пока на него
    не ответят. Когда ждущих нет вовсе — самый свежий по последнему сообщению.
    Когда все ждущие уже взяты — 404 `queue_done`: фронтенд сообщает об этом и
    начинает круг заново.
    """
    scoped = _scoped(_row_stmt(), user, await visible_account_ids(db, user))
    skipped = list(skip)
    waiting = Conversation.awaiting_reply_since.isnot(None)

    stmt = scoped.where(waiting)
    if skipped:
        stmt = stmt.where(Conversation.id.notin_(skipped))
    stmt = stmt.order_by(
        *reply_queue.next_order(
            Conversation.awaiting_reply_since,
            Conversation.awaiting_seen_at,
            Conversation.last_message_at,
            Conversation.id,
        )
    ).limit(1)
    row = (await db.execute(stmt)).unique().first()

    if row is None:
        anyone_waits = await db.scalar(
            _scoped(
                select(func.count()).select_from(Conversation).where(waiting),
                user,
                await visible_account_ids(db, user),
            )
        )
        if anyone_waits:
            raise NotFound(QUEUE_DONE, code="queue_done")
        recent = scoped
        if skipped:
            recent = recent.where(Conversation.id.notin_(skipped))
        row = (
            (
                await db.execute(
                    recent.order_by(
                        Conversation.last_message_at.desc().nulls_last(), Conversation.id.desc()
                    ).limit(1)
                )
            )
            .unique()
            .first()
        )
    if row is None:
        if skipped:
            raise NotFound(QUEUE_DONE, code="queue_done")
        raise NotFound("Нет чатов в работе")
    deals = await _awaiting_deal_amounts(db, [row[0].id])
    return ConversationRow(
        **_row_data(
            row,
            deals.get(row[0].id),
            datetime.now(UTC),
            reveal_reason=can_see_account_reason(user),
        )
    )


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


async def lock_for_write(db: AsyncSession, conversation_id: int) -> None:
    """Занять строку чата, пока идёт запись в него, — ПЕРВЫМ действием запроса.

    Вставка сообщения берёт слабую блокировку строки чата (внешний ключ), а
    потом чат обновляется (время последнего сообщения, ответственный) — для
    этого нужна сильная. Два запроса в один чат разом оба успевали взять слабую
    и оба ждали сильную: база разрывала это ошибкой «deadlock detected», а
    менеджер видел 500 на обычной отправке (двойной клик, две вкладки, ответ
    одновременно с пересылкой). Если сильную блокировку взять до всякой записи,
    запросы просто выстраиваются в очередь.
    """
    await db.execute(
        select(Conversation.id).where(Conversation.id == conversation_id).with_for_update()
    )


async def get_detail(db: AsyncSession, user: User, conversation_id: int) -> ConversationDetail:
    await load_visible(db, user, conversation_id)
    return await build_detail(db, conversation_id, reveal_reason=can_see_account_reason(user))


async def build_detail(
    db: AsyncSession, conversation_id: int, *, reveal_reason: bool = False
) -> ConversationDetail:
    """Карточка чата с данными рождения и итогами оплат клиента.

    Причина обрыва аккаунта в ней по умолчанию пуста: `reveal_reason=True` ставят
    только при ответе руководителю (`can_see_account_reason`)."""
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
    data = _row_data(row, deals.get(conv.id), datetime.now(UTC), reveal_reason=reveal_reason)
    data["client"] = ClientInChat.model_validate(conv.client)
    return ConversationDetail(
        **data, client_paid_amount=paid_amount, client_paid_count=paid_count
    )


async def emit_updated(db: AsyncSession, detail: ConversationDetail) -> None:
    """Одна аудитория на оба события: считать её дважды незачем.

    В `counters.updated` не числа: счётчики зависят от прав получателя,
    поэтому событие — сигнал перезапросить `/conversations/counters`.

    Тело одно на всю аудиторию (руководители и менеджер аккаунта), поэтому причина
    обрыва аккаунта в него не попадает никогда — даже если `detail` собран для
    руководителя. Экран, которому она нужна, получает её обычным запросом карточки.
    """
    audience = await conversation_audience(db, detail.id)
    body = detail.model_dump(mode="json")
    body["account"]["status_reason"] = None
    await emit("conversation.updated", {"conversation": body}, audience)
    await emit("counters.updated", {"conversation_id": detail.id, "stale": True}, audience)


async def mark_read(db: AsyncSession, user: User, conversation_id: int) -> ConversationDetail:
    conv = await load_visible(db, user, conversation_id)
    before = conv.unread_count
    conv.unread_count = 0
    # ТЗ п. 2.1: менеджер открыл чат — значок «ждёт N мин» на строке своё
    # отработал. Само ожидание остаётся: клиент по-прежнему без ответа, диалог
    # остаётся и в «ждут ответа», и в баннере (D-34). Руководитель же открывает
    # чаты, чтобы посмотреть: его просмотр не должен гасить напоминание менеджеру.
    if conv.awaiting_reply_since is not None and user.role != UserRole.ADMIN:
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

    # Настоящую отметку «прочитано» в Telegram отправляет только менеджер —
    # тот, кто реально ведёт диалог и должен ответить. Если чат просто открыл
    # руководитель, клиент не должен видеть две галочки без ответа: это
    # читалось бы как «увидели и проигнорировали». Внутри CRM непрочитанное
    # гасится в любом случае — не сбрасывается только сигнал наружу.
    if before and not settings.demo_mode and user.role == UserRole.MANAGER:
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

    detail = await build_detail(db, conversation_id, reveal_reason=can_see_account_reason(user))
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
    detail = await build_detail(db, conversation_id, reveal_reason=can_see_account_reason(user))
    await emit_updated(db, detail)
    return detail
