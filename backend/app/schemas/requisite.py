"""Схемы справочника счетов получателя.

Читают все — менеджер выбирает счёт при создании оплаты. Меняет только руководитель.
"""

from datetime import datetime

from app.schemas.common import ApiModel


class RequisiteOut(ApiModel):
    id: int
    title: str
    bank_name: str | None = None
    account_masked: str | None = None
    # Страна и тип нужны менеджеру при выборе: клиент из Казахстана не заплатит
    # на российскую карту, и наоборот. Номер целиком наружу не отдаём —
    # в списке хватает маски, а в счёт он попадает из details_text.
    country: str | None = None
    method: str | None = None
    kind: str | None = None
    holder: str | None = None
    # Полный текст реквизитов — именно он уходит клиенту в чат.
    details_text: str
    is_active: bool
    sort_order: int
    created_at: datetime


class RequisiteCreate(ApiModel):
    title: str
    bank_name: str | None = None
    account_masked: str | None = None
    details_text: str
    is_active: bool = True
    sort_order: int = 0


class RequisiteUpdate(ApiModel):
    """Все поля необязательны: применяются только те, что реально пришли."""

    title: str | None = None
    bank_name: str | None = None
    account_masked: str | None = None
    details_text: str | None = None
    is_active: bool | None = None
    sort_order: int | None = None
