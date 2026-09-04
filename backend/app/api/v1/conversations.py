"""Чаты: список, счётчики, очередь, карточка, чтение и передача.

Роутер только разбирает запрос и зовёт сервис: права и логика — в
`app.services.conversation_service`.
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Path, Query

from app.core.deps import CurrentUser, Db
from app.schemas.common import CursorPage
from app.schemas.conversation import (
    ConversationDetail,
    ConversationRow,
    Counters,
    TransferIn,
)
from app.services import conversation_service

router = APIRouter()

ConversationId = Annotated[int, Path(gt=0, description="Идентификатор чата")]


@router.get("", summary="Список чатов")
async def list_conversations(
    db: Db,
    user: CurrentUser,
    filter_: Annotated[
        Literal["all", "awaiting", "awaiting_payment"],
        Query(alias="filter", description="Все, ждут ответа или ожидают оплаты"),
    ] = "all",
    account_id: Annotated[int | None, Query(description="Только этот аккаунт")] = None,
    q: Annotated[
        str | None, Query(max_length=200, description="Имя, телефон, id клиента или текст")
    ] = None,
    cursor: Annotated[str | None, Query(description="Курсор следующей страницы")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> CursorPage[ConversationRow]:
    return await conversation_service.list_conversations(
        db, user, filter_=filter_, account_id=account_id, q=q, cursor=cursor, limit=limit
    )


@router.get("/counters", summary="Счётчики над списком чатов")
async def get_counters(db: Db, user: CurrentUser) -> Counters:
    return await conversation_service.counters(db, user)


@router.get("/next", summary="Взять первого в очереди")
async def next_conversation(db: Db, user: CurrentUser) -> ConversationRow:
    return await conversation_service.next_in_queue(db, user)


@router.get("/{conversation_id}", summary="Карточка чата")
async def get_conversation(
    db: Db, user: CurrentUser, conversation_id: ConversationId
) -> ConversationDetail:
    return await conversation_service.get_detail(db, user, conversation_id)


@router.post("/{conversation_id}/read", summary="Сбросить непрочитанные")
async def mark_read(
    db: Db, user: CurrentUser, conversation_id: ConversationId
) -> ConversationDetail:
    return await conversation_service.mark_read(db, user, conversation_id)


@router.post("/{conversation_id}/transfer", summary="Передать чат другому менеджеру")
async def transfer(
    db: Db, user: CurrentUser, conversation_id: ConversationId, payload: TransferIn
) -> ConversationDetail:
    return await conversation_service.transfer(db, user, conversation_id, payload.user_id)
