"""Схемы аутентификации: вход, выход, приглашения, смена пароля."""

from datetime import datetime

from pydantic import field_validator

from app.schemas.common import ApiModel
from app.schemas.user import MeOut, validate_email_format, validate_password_strength
from app.schemas.validators import optional_phone


class LoginIn(ApiModel):
    email: str
    password: str

    @field_validator("email")
    @classmethod
    def _email(cls, value: str) -> str:
        return validate_email_format(value)


class LoginOut(MeOut):
    """Ответ на вход и на приём приглашения.

    Сессия ставится в cookie, но токен дублируется в теле ответа — им может
    воспользоваться клиент без cookie (например, мобильное приложение).
    """

    token: str


class AcceptInviteIn(ApiModel):
    token: str
    password: str

    @field_validator("password")
    @classmethod
    def _password(cls, value: str) -> str:
        return validate_password_strength(value)


class ResetPasswordIn(ApiModel):
    """Новый пароль по одноразовой ссылке от руководителя (D-38).

    Политика пароля та же, что у приглашений и смены пароля в профиле.
    """

    token: str
    password: str

    @field_validator("password")
    @classmethod
    def _password(cls, value: str) -> str:
        return validate_password_strength(value)


class ResetPasswordPreviewOut(ApiModel):
    """Что видит открывший ссылку: только имя для приветствия и срок. Больше ничего."""

    full_name: str
    expires_at: datetime


class MeUpdateIn(ApiModel):
    accepting_leads: bool | None = None
    full_name: str | None = None
    phone: str | None = None
    current_password: str | None = None
    new_password: str | None = None

    @field_validator("full_name")
    @classmethod
    def _full_name(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("Укажите имя")
        return value.strip() if value is not None else value

    @field_validator("phone")
    @classmethod
    def _phone(cls, value: str | None) -> str | None:
        return optional_phone(value)

    @field_validator("new_password")
    @classmethod
    def _new_password(cls, value: str | None) -> str | None:
        return validate_password_strength(value) if value is not None else None
