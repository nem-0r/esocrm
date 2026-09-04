"""Статистика. Менеджер видит свои цифры, руководитель — все и в разрезе менеджеров."""

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Query

from app.core.deps import AdminUser, CurrentUser, Db
from app.services import stats_service

router = APIRouter()

DateFrom = Annotated[date | None, Query(description="Начало периода, ГГГГ-ММ-ДД")]
DateTo = Annotated[date | None, Query(description="Конец периода включительно")]
UserId = Annotated[int | None, Query(description="Разрез по сотруднику, только руководителю")]


def _period(date_from: date | None, date_to: date | None) -> tuple[date, date]:
    """По умолчанию — текущий месяц: именно он открыт на макете при входе."""
    today = date.today()
    end = date_to or today
    start = date_from or end.replace(day=1)
    return start, end


@router.get("/overview", summary="Сводка за период")
async def overview(
    db: Db,
    user: CurrentUser,
    date_from: DateFrom = None,
    date_to: DateTo = None,
    user_id: UserId = None,
) -> dict[str, Any]:
    start, end = _period(date_from, date_to)
    return await stats_service.overview(db, user, start, end, user_id)


@router.get("/series", summary="Ряд для графика продаж")
async def series(
    db: Db,
    user: CurrentUser,
    date_from: DateFrom = None,
    date_to: DateTo = None,
    granularity: Annotated[str, Query(pattern="^(day|week|month)$")] = "day",
    user_id: UserId = None,
) -> dict[str, Any]:
    start, end = _period(date_from, date_to)
    return await stats_service.series(db, user, start, end, granularity, user_id)


@router.get("/managers", summary="Разрез по менеджерам")
async def managers(
    db: Db,
    user: AdminUser,
    date_from: DateFrom = None,
    date_to: DateTo = None,
) -> list[dict[str, Any]]:
    _ = user
    start, end = _period(date_from, date_to)
    return await stats_service.managers(db, start, end)


@router.get("/response-goal", summary="Цель по времени ответа", include_in_schema=False)
async def response_goal(db: Db, user: CurrentUser) -> dict[str, int]:
    _ = user
    from app.services import settings_service

    return {"minutes": int(await settings_service.get_value(db, "response_time_goal_minutes"))}
