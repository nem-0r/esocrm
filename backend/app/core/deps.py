"""Зависимости запроса: текущий пользователь, роль, видимые аккаунты.

Права проверяются здесь и в сервисах, но никогда во фронтенде.
Правило: чужая сущность для менеджера — 404, а не 403. 403 подтверждает,
что запись существует, и это уже утечка.
"""

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import Cookie, Depends, Request
from sqlalchemy import false, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.errors import Forbidden, Unauthorized
from app.models import (
    AccountManager,
    AuthSession,
    FunnelStage,
    TelegramAccount,
    User,
    UserRole,
)

SESSION_COOKIE = "astra_session"


async def get_current_user(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    astra_session: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
) -> User:
    from app.core.crypto import token_hash

    token = astra_session or _bearer(request)
    if not token:
        raise Unauthorized()

    row = await db.execute(
        select(AuthSession, User)
        .join(User, User.id == AuthSession.user_id)
        .where(
            AuthSession.token_hash == token_hash(token),
            AuthSession.revoked_at.is_(None),
            AuthSession.expires_at > datetime.now(UTC),
        )
    )
    found = row.first()
    if not found:
        raise Unauthorized("Сессия истекла, войдите заново")

    _, user = found
    if not user.is_active or user.deleted_at is not None:
        raise Unauthorized("Учётная запись отключена")
    return user


def _bearer(request: Request) -> str | None:
    header = request.headers.get("Authorization", "")
    return header[7:] if header.startswith("Bearer ") else None


CurrentUser = Annotated[User, Depends(get_current_user)]
Db = Annotated[AsyncSession, Depends(get_db)]


async def require_admin(user: CurrentUser) -> User:
    if user.role != UserRole.ADMIN:
        raise Forbidden("Раздел доступен только руководителю")
    return user


AdminUser = Annotated[User, Depends(require_admin)]


def conversation_scope_orm(user: User, account_ids: list[int] | None, model: Any) -> list[Any]:
    """Какие диалоги видит сотрудник — условия для ORM-запроса.

    Руководитель видит всё. Менеджер — только те диалоги, где он ответственный,
    плюс ничейные на его аккаунтах: без ничейных сломалась бы кнопка «Взять
    первого в очереди» — брать было бы нечего, менеджер их просто не видел бы.

    Чужой назначенный диалог для менеджера не существует: ни в списке, ни в
    поиске, ни в карточке клиента, ни в счётчиках.
    """
    if account_ids is None:
        return []
    if not account_ids:
        return [false()]
    return [
        model.account_id.in_(account_ids),
        or_(model.responsible_id == user.id, model.responsible_id.is_(None)),
    ]


def conversation_scope_sql(
    user: User, account_ids: list[int] | None, prefix: str = "c", *, only_mine: bool = False
) -> tuple[str, dict[str, Any]]:
    """То же правило для запросов, написанных текстом SQL.

    `only_mine=True` — без ничейных диалогов: в статистике «мои диалоги» это
    именно те, за которые человек отвечает, а не те, которые он может взять.
    """
    if account_ids is None:
        return "", {}
    if not account_ids:
        return " and false", {}
    own = (
        f" and {prefix}.responsible_id = :scope_uid"
        if only_mine
        else f" and ({prefix}.responsible_id = :scope_uid or {prefix}.responsible_id is null)"
    )
    return (
        f" and {prefix}.account_id = any(:account_ids)" + own,
        {"account_ids": account_ids, "scope_uid": user.id},
    )


async def visible_account_ids(db: AsyncSession, user: User) -> list[int] | None:
    """Какие аккаунты видит пользователь.

    None — видит все (руководитель). Список — только назначенные (менеджер).
    Пустой список означает «не видит ничего», и это не то же самое, что None.
    """
    if user.role == UserRole.ADMIN:
        return None
    # ТЗ п. 1.4: этап «Бот» (в базе `warmup`) — это боты прогрева, первый уровень
    # воронки. Они сами доводят клиента до наших юзерботов. Менеджеру там делать
    # нечего, поэтому такие аккаунты для него не существуют вовсе — ни чатов,
    # ни клиентов, ни сделок. Руководитель видит их по-прежнему.
    rows = await db.execute(
        select(AccountManager.account_id)
        .join(TelegramAccount, TelegramAccount.id == AccountManager.account_id)
        .where(
            AccountManager.user_id == user.id,
            TelegramAccount.funnel_stage != FunnelStage.WARMUP,
        )
    )
    return list(rows.scalars().all())
