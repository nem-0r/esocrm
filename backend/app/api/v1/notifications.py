"""Уведомления сотрудника. В MVP — оплаты и назначение на аккаунт."""

from typing import Annotated, Any

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.core.deps import CurrentUser, Db
from app.schemas.common import CursorPage
from app.services import notification_service

router = APIRouter()


class MarkReadIn(BaseModel):
    """Пустой список идентификаторов означает «отметить все»."""

    ids: list[int] | None = None


@router.get("", summary="Список уведомлений")
async def list_notifications(
    db: Db,
    user: CurrentUser,
    only_unread: Annotated[bool, Query(description="Только непрочитанные")] = False,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> CursorPage[dict[str, Any]]:
    return await notification_service.list_for(
        db, user, only_unread=only_unread, cursor=cursor, limit=limit
    )


@router.get("/unread-count", summary="Сколько непрочитанных")
async def count_unread(db: Db, user: CurrentUser) -> dict[str, int]:
    return {"count": await notification_service.unread_count(db, user)}


@router.post("/read", summary="Отметить прочитанными")
async def mark_read(db: Db, user: CurrentUser, payload: MarkReadIn) -> dict[str, int]:
    return {"updated": await notification_service.mark_read(db, user, payload.ids)}
