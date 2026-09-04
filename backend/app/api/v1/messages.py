"""Переписка внутри чата. Подключается с тем же префиксом `/conversations`."""

from typing import Annotated

from fastapi import APIRouter, Path, Query

from app.core.deps import CurrentUser, Db
from app.schemas.common import CursorPage
from app.schemas.message import MessageCreate, MessageOut
from app.services import message_service

router = APIRouter()

ConversationId = Annotated[int, Path(gt=0, description="Идентификатор чата")]


@router.get("/{conversation_id}/messages", summary="Переписка, свежие сверху")
async def list_messages(
    db: Db,
    user: CurrentUser,
    conversation_id: ConversationId,
    cursor: Annotated[str | None, Query(description="Курсор следующей страницы")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 40,
) -> CursorPage[MessageOut]:
    return await message_service.list_messages(
        db, user, conversation_id, cursor=cursor, limit=limit
    )


@router.post("/{conversation_id}/messages", status_code=201, summary="Отправить сообщение")
async def create_message(
    db: Db, user: CurrentUser, conversation_id: ConversationId, payload: MessageCreate
) -> MessageOut:
    return await message_service.post_message(db, user, conversation_id, payload)
