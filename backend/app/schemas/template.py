"""Схемы шаблонов сообщений: общие (без владельца) и личные."""

from datetime import datetime

from app.schemas.common import ApiModel


class TemplateOut(ApiModel):
    id: int
    title: str
    text: str
    owner_id: int | None = None
    # Личный — у шаблона есть владелец; общий виден всем.
    is_personal: bool
    sort_order: int
    is_active: bool
    created_at: datetime
    updated_at: datetime


class TemplateCreate(ApiModel):
    title: str
    text: str
    # Общий шаблон заводит только руководитель.
    is_personal: bool = True
    sort_order: int = 0


class TemplateUpdate(ApiModel):
    title: str | None = None
    text: str | None = None
    sort_order: int | None = None
    is_active: bool | None = None
