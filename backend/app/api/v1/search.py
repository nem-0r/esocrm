"""Глобальный поиск. Открывается из любого раздела, где есть кнопка поиска."""

from typing import Annotated, Any

from fastapi import APIRouter, Query

from app.core.deps import CurrentUser, Db
from app.services import search_service

router = APIRouter()

VALID_TYPES = set(search_service.ALL_TYPES)


@router.get("", summary="Поиск по клиентам, оплатам, чатам и файлам")
async def search(
    db: Db,
    user: CurrentUser,
    q: Annotated[str, Query(description="Запрос, от двух символов")] = "",
    types: Annotated[str | None, Query(description="clients,deals,chats,files")] = None,
    limit: Annotated[int, Query(ge=1, le=20)] = 5,
) -> dict[str, list[dict[str, Any]]]:
    selected = None
    if types:
        selected = [t.strip() for t in types.split(",") if t.strip() in VALID_TYPES]
    return await search_service.search(db, user, q, selected, limit)


@router.get("/history", summary="Последние запросы")
async def history(db: Db, user: CurrentUser) -> list[dict[str, Any]]:
    return await search_service.history(db, user)


@router.delete("/history", status_code=204, summary="Очистить историю поиска")
async def clear_history(db: Db, user: CurrentUser) -> None:
    await search_service.clear_history(db, user)
