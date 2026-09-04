"""Схемы раздела «Статистика»: сводка, ряд для графика, разбивка по менеджерам.

Формулы — `docs/03-business-rules.md`, раздел 10 «Аналитика»: следуем буквально.
Секунды — всегда календарные, если не включены рабочие часы в настройках
(`working_hours_enabled`); единица одна и та же от SQL до ответа API.
"""

from datetime import date
from typing import Literal

from app.schemas.common import ApiModel

Granularity = Literal["day", "week", "month"]


class OverviewOut(ApiModel):
    """Плитки над графиком продаж."""

    sales_amount: int
    sales_count: int
    # Прирост к предыдущему периоду той же длины. None — предыдущий период пуст,
    # делить не на что.
    sales_amount_delta_pct: float | None = None
    sales_count_delta: int | None = None
    avg_response_seconds: int | None = None
    response_goal_minutes: int
    active_conversations: int
    new_clients: int
    awaiting_amount: int
    awaiting_count: int


class SeriesPoint(ApiModel):
    date: date
    amount: int
    count: int


class SeriesOut(ApiModel):
    points: list[SeriesPoint]
    granularity: Granularity


class ManagerRef(ApiModel):
    id: int
    full_name: str
    avatar_color: str


class ManagerStatsRow(ApiModel):
    """Одна строка таблицы «по менеджерам» — только для руководителя."""

    user: ManagerRef
    sales_amount: int
    sales_count: int
    avg_response_seconds: int | None = None
    active_conversations: int
    awaiting_count: int
