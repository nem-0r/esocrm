"""Схемы аутентификации: вход, выход, приглашения, смена пароля."""

from pydantic import field_validator

from app.schemas.common import ApiModel
from app.schemas.user import MeOut, validate_email_format, validate_password_strength


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


class MeUpdateIn(ApiModel):
    accepting_leads: bool | None = None
    current_password: str | None = None
    new_password: str | None = None

    @field_validator("new_password")
    @classmethod
    def _new_password(cls, value: str | None) -> str | None:
        return validate_password_strength(value) if value is not None else None
