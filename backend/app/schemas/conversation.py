"""Схемы диалогов: строка списка чатов и карточка чата.

Строка списка собирается на сервере целиком — фронтенд ничего не досчитывает.
Время ожидания приходит и меткой (`awaiting_reply_since`), и уже посчитанными
минутами: секундная точность в списке чатов не нужна, а вычитать даты в разметке
на каждой строке — лишняя работа.
"""

from datetime import date, datetime, time

from pydantic import BaseModel, Field

from app.models.enums import FunnelStage
from app.schemas.common import ApiModel


class ClientBrief(ApiModel):
    """Клиент в строке чата. `name` и `data_complete` — свойства модели."""

    id: int
    name: str
    phone: str | None = None
    tg_username: str | None = None
    data_complete: bool


class ClientInChat(ClientBrief):
    """Шапка чата: к имени добавляются данные рождения и дата карточки."""

    birth_date: date | None = None
    birth_time: time | None = None
    birth_city: str | None = None
    zodiac_sign: str | None = None
    first_contact_at: datetime


class AccountBrief(ApiModel):
    id: int
    title: str
    funnel_stage: FunnelStage


class UserBrief(ApiModel):
    id: int
    full_name: str


class ConversationRow(ApiModel):
    """Одна строка списка чатов."""

    id: int
    client: ClientBrief
    account: AccountBrief
    last_message_at: datetime | None = None
    last_message_preview: str | None = None
    unread_count: int = 0
    awaiting_reply_since: datetime | None = None
    # Пусто, когда ответ уже дан: нечего отсчитывать.
    awaiting_seen: bool = False
    awaiting_minutes: int | None = None
    responsible: UserBrief | None = None
    has_awaiting_deal: bool = False
    awaiting_deal_amount: int | None = None
    is_blocked_by_client: bool = False
    updated_at: datetime


class ConversationDetail(ConversationRow):
    """Карточка чата: та же строка плюс данные рождения и итоги по оплатам.

    Это строка «Оплачено 6 900 ₽ · 2 сделки» в шапке. Сумма в копейках.
    """

    client: ClientInChat
    client_paid_amount: int = 0
    client_paid_count: int = 0


class Counters(BaseModel):
    """Счётчики над списком чатов. `over_threshold` — красный баннер на доске."""

    total: int = 0
    awaiting: int = 0
    awaiting_payment: int = 0
    over_threshold: int = 0
    # Порог из настроек едет рядом с числом: подпись баннера обязана
    # называть тот порог, по которому это число посчитано.
    over_threshold_minutes: int = 0


class TransferIn(BaseModel):
    user_id: int = Field(gt=0)
