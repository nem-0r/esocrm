"""Глобальный поиск. Открывается из любого раздела, где есть кнопка поиска."""

from typing import Annotated, Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from app.core.deps import CurrentUser, Db
from app.services import search_service

router = APIRouter()

VALID_TYPES = set(search_service.ALL_TYPES)


class HistoryEntry(BaseModel):
    q: str = Field(min_length=1, max_length=255, description="Запрос, который запомнить")


@router.get("", summary="Поиск по клиентам, оплатам, чатам и файлам")
async def search(
    db: Db,
    user: CurrentUser,
    q: Annotated[str, Query(description="Запрос, от двух символов")] = "",
    types: Annotated[str | None, Query(description="clients,deals,chats,files")] = None,
    limit: Annotated[int, Query(ge=1, le=50, description="Строк в группе")] = 5,
) -> dict[str, Any]:
    """Группы результатов и `totals` — сколько найдено всего в каждой (51 — «больше 50»).
    Историю поиска не пишет: её запоминает `POST /search/history`."""
    selected = None
    if types:
        selected = [t.strip() for t in types.split(",") if t.strip() in VALID_TYPES]
    return await search_service.search(db, user, q, selected, limit)


@router.get("/history", summary="Последние запросы")
async def history(db: Db, user: CurrentUser) -> list[dict[str, Any]]:
    return await search_service.history(db, user)


@router.post("/history", summary="Запомнить запрос: Enter или клик по результату")
async def remember_query(db: Db, user: CurrentUser, body: HistoryEntry) -> list[dict[str, Any]]:
    """Тот же запрос (без учёта регистра, «ё» и пробелов) не дублируется, а
    поднимается наверх. Отвечает обновлённой историей."""
    return await search_service.remember(db, user, body.q)


@router.delete("/history", status_code=204, summary="Очистить историю поиска")
async def clear_history(db: Db, user: CurrentUser) -> None:
    await search_service.clear_history(db, user)
