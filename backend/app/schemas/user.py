"""Схемы сотрудников: профиль текущего пользователя и управление персоналом.

`MeOut` — то, что видит о себе сам сотрудник (эндпойнты /auth).
`UserOut` — строка списка сотрудников, которую видит руководитель (эндпойнты /users);
набор полей другой: там есть онлайн-статус, аккаунты и статистика за месяц,
но нет счётчика неудачных входов и прочих личных данных.
"""

import re
from datetime import datetime

from pydantic import Field, field_validator, model_validator

from app.models import FunnelStage, UserRole
from app.schemas.common import ApiModel

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def validate_email_format(value: str) -> str:
    value = value.strip()
    if not EMAIL_RE.match(value):
        raise ValueError("Некорректный адрес почты")
    return value


def validate_password_strength(value: str) -> str:
    if len(value) < 8:
        raise ValueError("Пароль должен быть не короче 8 символов")
    return value


class WorkScheduleOut(ApiModel):
    """График работы сотрудника (ТЗ Б.3): недельный повтор плюс ответ «на смене ли сейчас»."""

    enabled: bool
    days: list[int]
    start: str | None
    end: str | None
    summary: str
    on_shift: bool


class WorkScheduleIn(ApiModel):
    enabled: bool
    # Дни недели по ISO: 1 — понедельник, 7 — воскресенье.
    days: list[int] = []
    start: str | None = None
    end: str | None = None

    @field_validator("days")
    @classmethod
    def _days(cls, value: list[int]) -> list[int]:
        if any(day < 1 or day > 7 for day in value):
            raise ValueError("День недели задаётся числом от 1 (Пн) до 7 (Вс)")
        return sorted(set(value))

    @field_validator("start", "end")
    @classmethod
    def _time(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not TIME_RE.match(value.strip()):
            raise ValueError("Время указывается в формате ЧЧ:ММ")
        return value.strip()

    @model_validator(mode="after")
    def _complete(self) -> "WorkScheduleIn":
        # Включённый график без дней или без часов — это «включено, но ничего не значит».
        # Такой график нельзя ни показать сотруднику, ни сравнить с текущим временем.
        if self.enabled and (not self.days or not self.start or not self.end):
            raise ValueError("Для графика укажите дни недели, начало и конец рабочего дня")
        return self


class MeOut(ApiModel):
    """Профиль текущего пользователя — ответ /auth/me, /auth/login, /auth/accept-invite."""

    id: int
    full_name: str
    email: str
    phone: str | None
    role: UserRole
    is_active: bool
    accepting_leads: bool
    avatar_color: str
    last_seen_at: datetime | None
    is_admin: bool
    # Пусто у руководителя: он видит всё, отдельный признак — sees_all_accounts.
    account_ids: list[int]
    sees_all_accounts: bool
    # ТЗ Б.1: с какого момента человек работает. Берём момент, когда он принял
    # приглашение и впервые вошёл; если приглашение не принято — дату создания.
    works_since: datetime
    # ТЗ Б.2: среднее время ответа рядом с диалогами и продажами.
    month_conversations: int = 0
    month_sales_amount: int = 0
    avg_response_seconds: int | None = None
    # ТЗ Б.5: сколько «моих» аккаунтов требуют внимания — и у менеджера тоже.
    accounts_attention: int = 0
    # ТЗ Б.3: свой график работы. Менеджер его видит, меняет только руководитель.
    schedule: WorkScheduleOut
    # Демо-режим: Telegram не подключён, действия с ним недоступны. Интерфейс
    # обязан знать об этом, чтобы гасить кнопки, а не показывать ошибку по нажатию.
    demo_mode: bool = False
    # Способ оплаты «Ссылка» существует в форме, только если Робокасса реально
    # настроена на этом сервере — иначе это была бы нерабочая кнопка.
    robokassa_enabled: bool = False


class StaffAccountOut(ApiModel):
    id: int
    title: str
    funnel_stage: FunnelStage


class UserOut(ApiModel):
    """Строка списка/карточки сотрудника в разделе руководителя."""

    id: int
    full_name: str
    email: str
    phone: str | None
    role: UserRole
    is_active: bool
    avatar_color: str
    online: bool
    last_seen_at: datetime | None
    invite_pending: bool
    accounts: list[StaffAccountOut]
    month_conversations: int
    month_sales_amount: int
    schedule: WorkScheduleOut


class UserCreateIn(ApiModel):
    full_name: str
    email: str
    phone: str | None = None
    role: UserRole
    account_ids: list[int] | None = None

    @field_validator("email")
    @classmethod
    def _email(cls, value: str) -> str:
        return validate_email_format(value)

    @field_validator("full_name")
    @classmethod
    def _full_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Укажите имя сотрудника")
        return value


class UserCreateOut(UserOut):
    invite_url: str = Field(
        description=(
            "Ссылка-приглашение для сотрудника. Отправка почты пока не реализована — "
            "руководитель копирует эту ссылку и передаёт сотруднику вручную."
        )
    )


class UserUpdateIn(ApiModel):
    full_name: str | None = None
    phone: str | None = None
    role: UserRole | None = None
    is_active: bool | None = None
    account_ids: list[int] | None = None
    schedule: WorkScheduleIn | None = None

    @field_validator("full_name")
    @classmethod
    def _full_name(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("Укажите имя сотрудника")
        return value


class ResendInviteOut(ApiModel):
    invite_url: str = Field(description="Новая ссылка-приглашение взамен истёкшей.")
