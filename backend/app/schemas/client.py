"""Схемы карточки клиента: список, карточка, заметки, материалы.

Суммы сделок — целые копейки (см. `app.services.money`), форматирование — во фронтенде.
"""

from datetime import date, datetime, time
from typing import Literal

from pydantic import Field

from app.models import BirthTimeApprox, FunnelStage
from app.schemas.common import ApiModel


class ClientListRow(ApiModel):
    """Строка списка клиентов. Суммы посчитаны группировкой по клиенту, не по одной на клиента."""

    id: int
    name: str
    phone: str | None = None
    tg_username: str | None = None
    birth_date: date | None = None
    birth_time: time | None = None
    birth_time_approx: BirthTimeApprox | None = None
    birth_city: str | None = None
    zodiac_sign: str | None = None
    data_complete: bool
    first_contact_at: datetime
    created_at: datetime
    last_contact_at: datetime | None = None
    paid_amount: int
    paid_count: int
    awaiting_amount: int
    conversations_count: int


class ClientAccountBrief(ApiModel):
    id: int
    title: str
    funnel_stage: FunnelStage


class ClientResponsibleBrief(ApiModel):
    id: int
    full_name: str


class ClientConversationRow(ApiModel):
    id: int
    account: ClientAccountBrief
    responsible: ClientResponsibleBrief | None = None
    last_message_at: datetime | None = None
    unread_count: int


class ClientCard(ClientListRow):
    """Карточка клиента: строка списка плюс паспортные поля и все диалоги."""

    source_code: str | None = None
    source: str | None = None
    tg_first_name: str | None = None
    tg_last_name: str | None = None
    # Имя, введённое менеджером. Пусто — значит сверху показан @username
    # или публичный id, и поле «Имя» в правке должно быть пустым (ТЗ п. 5.3).
    display_name: str | None = None
    # ТЗ п. 5.2: через какой аккаунт заведена карточка и на скольких аккаунтах
    # клиент вообще присутствует.
    created_via_account: ClientAccountBrief | None = None
    accounts_count: int = 0
    # Согласия приходят из бота воронки; менеджер может снять согласие на
    # рассылку по просьбе клиента, но проставить его за клиента не может.
    pdn_consent_at: datetime | None = None
    pdn_consent_version: str | None = None
    marketing_consent: bool
    marketing_consent_at: datetime | None = None
    conversations: list[ClientConversationRow]


class ClientUpdate(ApiModel):
    """Тело PATCH. Какие поля реально переданы — смотрим через `model_fields_set`,
    иначе не отличить «поле не передали» от «поле явно очистили в null»."""

    display_name: str | None = None
    phone: str | None = None
    birth_date: date | None = None
    birth_time: time | None = None
    birth_time_approx: BirthTimeApprox | None = None
    birth_city: str | None = None
    source: str | None = None
    marketing_consent: bool | None = None


class NoteAuthor(ApiModel):
    id: int
    full_name: str
    avatar_color: str


class NoteRow(ApiModel):
    id: int
    text: str
    author: NoteAuthor
    created_at: datetime


class NoteCreate(ApiModel):
    text: str = Field(min_length=1, max_length=2000)


class MaterialAuthor(ApiModel):
    id: int
    full_name: str


class MaterialRow(ApiModel):
    """Вложение, отправленное клиенту или полученное от него — вкладка «Материалы»."""

    id: int
    file_name: str
    mime_type: str | None = None
    size_bytes: int
    url: str
    created_at: datetime
    direction: Literal["in", "out"]
    author: MaterialAuthor | None = None
