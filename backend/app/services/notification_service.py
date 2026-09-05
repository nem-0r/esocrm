"""Уведомления.

Текст собирается здесь, а не во фронтенде: одно уведомление должно читаться
одинаково и в списке, и в пуше, и в письме — где бы оно потом ни появилось.
В MVP это оплаты и назначение-снятие менеджера на аккаунт.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Notification, NotificationKind, User
from app.realtime.events import emit
from app.schemas.common import CursorPage, decode_cursor, encode_cursor
from app.services.money import format_rubles


def render(notification: Notification) -> tuple[str, str]:
    """Заголовок и текст на русском — по виду события и его данным."""
    payload: dict[str, Any] = notification.payload or {}
    match notification.kind:
        case NotificationKind.DEAL_PAID:
            number = payload.get("number") or f"DEAL-{notification.entity_id}"
            amount = payload.get("amount")
            client = payload.get("client_name") or "клиент"
            parts = [number]
            if amount is not None:
                parts.append(format_rubles(int(amount)))
            parts.append(str(client))
            return "Оплата получена", " · ".join(parts)
        case NotificationKind.ACCOUNT_ASSIGNED:
            return "Вас назначили на аккаунт", str(payload.get("account_title") or "Аккаунт")
        case NotificationKind.ACCOUNT_UNASSIGNED:
            return "Вас сняли с аккаунта", str(payload.get("account_title") or "Аккаунт")
        case NotificationKind.ACCOUNT_ERROR:
            title = payload.get("account_title") or "Аккаунт"
            reason = payload.get("reason") or "нет связи"
            return "Аккаунт потерял связь", f"{title} · {reason}"
        case NotificationKind.PAYMENT_MISMATCH:
            number = payload.get("number") or f"DEAL-{notification.entity_id}"
            got = payload.get("amount_received")
            expected = payload.get("amount_expected")
            detail = (
                f"пришло {format_rubles(int(got))}, в сделке {format_rubles(int(expected))}"
                if got is not None and expected is not None
                else "сумма не совпала со сделкой"
            )
            return "Оплата не сошлась по сумме — деньги у провайдера", f"{number} · {detail}"
        case NotificationKind.PAYMENT_ORPHANED:
            number = payload.get("number") or f"DEAL-{notification.entity_id}"
            amount = payload.get("amount_received")
            status_label = payload.get("deal_status_label") or "не ждёт оплаты"
            detail = (
                f"пришло {format_rubles(int(amount))}, сделка — {status_label}"
                if amount is not None
                else f"сделка — {status_label}"
            )
            return "Оплата пришла по сделке, которая её не ждёт", f"{number} · {detail}"
    return "Уведомление", ""


def to_out(notification: Notification) -> dict[str, Any]:
    title, body = render(notification)
    return {
        "id": notification.id,
        "kind": notification.kind,
        "entity_type": notification.entity_type,
        "entity_id": notification.entity_id,
        "title": title,
        "text": body,
        "read_at": notification.read_at,
        "created_at": notification.created_at,
    }


# Окно показа уведомлений: ТЗ п. 1.3 — «только за последние 31 день».
NOTIFICATIONS_WINDOW_DAYS = 31


async def list_for(
    db: AsyncSession,
    user: User,
    *,
    only_unread: bool = False,
    cursor: str | None = None,
    limit: int = 20,
) -> CursorPage[dict[str, Any]]:
    limit = max(1, min(limit, 100))
    # ТЗ п. 1.3: показываем только за последний месяц. Уведомление годовой
    # давности не несёт смысла, а список из-за него растёт бесконечно.
    since = datetime.now(UTC) - timedelta(days=NOTIFICATIONS_WINDOW_DAYS)
    stmt = select(Notification).where(
        Notification.user_id == user.id,
        Notification.created_at >= since,
    )
    if only_unread:
        stmt = stmt.where(Notification.read_at.is_(None))
    if (last_id := decode_cursor(cursor)) is not None:
        stmt = stmt.where(Notification.id < last_id)
    stmt = stmt.order_by(Notification.id.desc()).limit(limit + 1)

    rows = list((await db.execute(stmt)).scalars().all())
    has_more = len(rows) > limit
    rows = rows[:limit]
    return CursorPage[dict[str, Any]](
        items=[to_out(row) for row in rows],
        next_cursor=encode_cursor(rows[-1].id) if has_more and rows else None,
    )


async def unread_count(db: AsyncSession, user: User) -> int:
    rows = await db.execute(
        select(Notification.id).where(
            Notification.user_id == user.id, Notification.read_at.is_(None)
        )
    )
    return len(rows.scalars().all())


async def mark_read(db: AsyncSession, user: User, ids: list[int] | None) -> int:
    stmt = (
        update(Notification)
        .where(Notification.user_id == user.id, Notification.read_at.is_(None))
        .values(read_at=datetime.now(UTC))
    )
    if ids:
        stmt = stmt.where(Notification.id.in_(ids))
    result = await db.execute(stmt)
    await db.commit()
    return result.rowcount or 0


async def notify(
    db: AsyncSession,
    *,
    user_ids: list[int],
    kind: NotificationKind,
    entity_type: str,
    entity_id: int,
    payload: dict[str, Any],
) -> None:
    """Создаёт уведомления и шлёт событие. Транзакцию не закрывает — коммитит вызывающий."""
    unique_ids = sorted(set(user_ids))
    if not unique_ids:
        return
    rows = [
        Notification(
            user_id=user_id,
            kind=kind,
            entity_type=entity_type,
            entity_id=entity_id,
            payload=payload,
        )
        for user_id in unique_ids
    ]
    db.add_all(rows)
    await db.flush()
    for row in rows:
        await emit("notification.new", {"notification": to_out(row)}, [row.user_id])
