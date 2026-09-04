"""Схемы раздела «Аккаунты»: строка списка, сводка, тела запросов входа.

Строка списка не совпадает 1:1 с моделью: `needs_attention` и `managers`
считаются в сервисе одним пакетным запросом, а не на каждую строку отдельно.
"""

from datetime import datetime
from typing import Literal

from app.models.enums import AccountStatus, FunnelStage
from app.schemas.common import ApiModel


class AccountManagerRef(ApiModel):
    """Менеджер в строке аккаунта: онлайн-статус — из Redis, одним пакетным вызовом."""

    id: int
    full_name: str
    avatar_color: str
    online: bool


class AccountRow(ApiModel):
    id: int
    title: str
    phone: str
    funnel_stage: FunnelStage
    status: AccountStatus
    status_reason: str | None = None
    tg_username: str | None = None
    last_activity_at: datetime | None = None
    is_active: bool
    # Сессия прервана либо на аккаунт не назначен ни один менеджер.
    needs_attention: bool
    managers: list[AccountManagerRef]
    conversations_count: int


class AccountSummary(ApiModel):
    """Плитки над списком аккаунтов."""

    total: int
    connected: int
    attention: int
    conversations_total: int


class AccountCreate(ApiModel):
    title: str
    phone: str
    funnel_stage: FunnelStage
    # Необязательны: по умолчанию берутся общие ключи приложения из окружения.
    # На доске руководитель их не вводит — только название, телефон и этап.
    api_id: int | None = None
    api_hash: str | None = None


class AccountUpdate(ApiModel):
    title: str | None = None
    funnel_stage: FunnelStage | None = None
    is_active: bool | None = None


class SendCodeOut(ApiModel):
    """Ответ на «Получить код». Код приходит в приложение Telegram, не по СМС."""

    phone_code_hash: str
    sent_to: Literal["app", "sms"]
    demo: bool
    hint: str | None = None


class ConfirmCodeIn(ApiModel):
    code: str
    phone_code_hash: str
    password: str | None = None


class ConfirmCodeOut(ApiModel):
    """needs_password=true — нужен второй шаг, это не ошибка, ответ 200.

    account заполняется только когда вход завершён.
    """

    needs_password: bool = False
    account: AccountRow | None = None


class ManagersIn(ApiModel):
    user_ids: list[int] = []
    notify: bool = True
