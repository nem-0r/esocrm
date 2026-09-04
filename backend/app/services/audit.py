"""Журнал действий. Пишется в той же транзакции, что и само изменение."""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ActorKind, EventLog, User


async def log_event(
    db: AsyncSession,
    *,
    action: str,
    entity_type: str,
    entity_id: int | None = None,
    actor: User | None = None,
    actor_kind: ActorKind = ActorKind.USER,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    ip: str | None = None,
) -> None:
    db.add(
        EventLog(
            actor_id=actor.id if actor else None,
            actor_kind=actor_kind,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            before=before,
            after=after,
            ip_address=ip,
        )
    )
