"""Кто какие события видит.

Аудитория считается один раз при публикации: менеджеры аккаунта плюс все руководители.
Так фронтенд не получает событий по чужим диалогам — это требование прав доступа,
а не оптимизация.
"""

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redis_bus import publish
from app.models import AccountManager, Conversation, User, UserRole


async def account_audience(db: AsyncSession, account_id: int) -> list[int]:
    managers = await db.execute(
        select(AccountManager.user_id).where(AccountManager.account_id == account_id)
    )
    admins = await db.execute(
        select(User.id).where(User.role == UserRole.ADMIN, User.is_active.is_(True))
    )
    return sorted({*managers.scalars().all(), *admins.scalars().all()})


async def conversation_audience(db: AsyncSession, conversation_id: int) -> list[int]:
    """Кому уходят живые события диалога.

    Ровно тем, кто вправе его видеть: руководителям и ответственному менеджеру.
    Ничейный диалог — всем менеджерам аккаунта: он в общей очереди, и они его
    видят в списке.

    Раньше события уходили всем менеджерам аккаунта. В событии `message.new`
    лежит текст сообщения, то есть чужая переписка утекала по вебсокету —
    на экране её не было, а в браузере она оказывалась.
    """
    row = (
        await db.execute(
            select(Conversation.account_id, Conversation.responsible_id).where(
                Conversation.id == conversation_id
            )
        )
    ).first()
    if row is None:
        return []
    account_id, responsible_id = row
    if responsible_id is None:
        return await account_audience(db, account_id)

    admins = await db.execute(
        select(User.id).where(User.role == UserRole.ADMIN, User.is_active.is_(True))
    )
    return sorted({responsible_id, *admins.scalars().all()})


async def emit(
    event_type: str, data: dict[str, Any], user_ids: list[int] | None = None
) -> None:
    await publish(event_type, data, {"user_ids": user_ids} if user_ids else None)


async def emit_to_conversation(
    db: AsyncSession, conversation_id: int, event_type: str, data: dict[str, Any]
) -> None:
    await emit(event_type, data, await conversation_audience(db, conversation_id))


async def emit_to_account(
    db: AsyncSession, account_id: int, event_type: str, data: dict[str, Any]
) -> None:
    await emit(event_type, data, await account_audience(db, account_id))
