"""Схемы справочника услуг. Цены — в копейках, как все деньги в API."""

from datetime import datetime

from pydantic import Field

from app.schemas.common import ApiModel


class ServiceOut(ApiModel):
    id: int
    name: str
    # Пусто — «цена по договорённости»: название подставится, сумму впишут.
    price: int | None = None
    description: str | None = None
    is_active: bool
    sort_order: int
    updated_at: datetime


class ServiceCreate(ApiModel):
    name: str = Field(min_length=1, max_length=255)
    price: int | None = None
    description: str | None = Field(default=None, max_length=2000)
    is_active: bool = True
    sort_order: int = Field(default=0, ge=0, le=32000)


class ServiceUpdate(ApiModel):
    """Все поля необязательны: применяются только те, что реально пришли.

    Чтобы стереть цену, её передают явно как null — отличаем от «не передали».
    """

    name: str | None = Field(default=None, min_length=1, max_length=255)
    price: int | None = None
    description: str | None = Field(default=None, max_length=2000)
    is_active: bool | None = None
    sort_order: int | None = Field(default=None, ge=0, le=32000)


class ServiceSuggestion(ApiModel):
    """Название из прошлых сделок, которого нет в справочнике."""

    name: str
    uses: int
    typical_price: int | None = None
    last_used_at: datetime
