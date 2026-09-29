"""Схемы переписки.

Статуса «доставлено» здесь нет и не будет: MTProto его не отдаёт.
Часы — в очереди, галочка — отправлено, две — прочитано, крестик — ошибка.
"""

from datetime import datetime

from pydantic import BaseModel, Field

from app.models.enums import AuthorKind, Direction, MessageKind, MessageStatus
from app.schemas.common import ApiModel

MAX_TEXT_LENGTH = 4096
# Столько сообщений Telegram разрешает переслать одним запросом (100) — берём
# с запасом меньше: пересылка сотни сообщений разом из CRM — скорее ошибка.
MAX_FORWARD_MESSAGES = 50


def attachment_url(attachment_id: int) -> str:
    """Файл отдаётся через API, а не прямой ссылкой на хранилище: там права."""
    return f"/api/v1/files/{attachment_id}"


class AttachmentOut(ApiModel):
    id: int
    file_name: str
    mime_type: str | None = None
    size_bytes: int = 0
    url: str
    width: int | None = None
    height: int | None = None
    duration_sec: int | None = None
    # photo · video · video_note · animation · voice · audio · sticker · document
    kind: str = "document"
    # ready — файл в CRM; pending — докачивается; failed/too_large — только в Telegram
    status: str = "ready"
    # Волна голосового: значения 0..31, как рисует Telegram.
    waveform: list[int] | None = None
    thumb_url: str | None = None
    title: str | None = None
    performer: str | None = None
    emoji: str | None = None
    error: str | None = None


class MessageAuthor(ApiModel):
    """Автор-сотрудник. Цвет аватара хранится у пользователя, чтобы совпадать везде."""

    id: int
    full_name: str
    avatar_color: str


class ForwardedFrom(ApiModel):
    """Сообщение пришло пересланным (клиент переслал чужое или менеджер — с телефона)."""

    name: str | None = None
    date: datetime | None = None


class ForwardedInCrm(ApiModel):
    """Менеджер переслал это сообщение из другого чата CRM. Видно только в CRM."""

    conversation_id: int
    message_id: int
    client_name: str | None = None
    # Реальный исход, а не флаг из запроса: копию клиент никогда не видит с
    # подписью «Переслано от …», поэтому тут true и при выключенном переключателе,
    # если пересылка ушла копией.
    hide_sender: bool = True
    # true — настоящая пересылка Telegram (messages.forwardMessages): такое
    # сообщение Telegram не даёт редактировать, поэтому в CRM для него нет
    # «Изменить» (см. MessageBubble.canEditMessage на фронтенде). false —
    # копия: обычное отправленное сообщение, редактируется как любое другое.
    # Старые записи без этого поля в meta — считаем не настоящей пересылкой,
    # чтобы не отбирать «Изменить» задним числом там, где раньше оно работало.
    native: bool = False


class ContactMeta(ApiModel):
    first_name: str | None = None
    last_name: str | None = None
    phone: str | None = None
    tg_user_id: int | None = None


class LocationMeta(ApiModel):
    lat: float
    lon: float
    title: str | None = None
    address: str | None = None


class PollMeta(ApiModel):
    question: str
    options: list[str] = []


class MessageMeta(ApiModel):
    """Всё нетекстовое о сообщении. Хранится JSON-ом в `messages.meta`."""

    forwarded_from: ForwardedFrom | None = None
    forwarded: ForwardedInCrm | None = None
    contact: ContactMeta | None = None
    location: LocationMeta | None = None
    poll: PollMeta | None = None
    # Клиент (или менеджер с телефона) удалил сообщение в Telegram. В CRM оно
    # остаётся: переписка — история работы с клиентом.
    deleted_in_telegram_at: datetime | None = None
    # Тип сообщения Telegram, который CRM не показывает (игра, счёт и т. п.).
    unsupported: str | None = None


class MessageOut(ApiModel):
    id: int
    conversation_id: int
    direction: Direction
    author_kind: AuthorKind
    author: MessageAuthor | None = None
    kind: MessageKind
    text: str | None = None
    is_internal: bool = False
    status: MessageStatus
    error_text: str | None = None
    created_at: datetime
    sent_at: datetime | None = None
    read_at: datetime | None = None
    edited_at: datetime | None = None
    reply_to_tg_id: int | None = None
    attachments: list[AttachmentOut] = []
    meta: MessageMeta | None = None


class UploadRef(BaseModel):
    """То, что вернул `POST /files/upload` или `POST /files/voice`.

    Файл уже лежит в хранилище, но строки в базе ещё нет: она создаётся
    вместе с сообщением, иначе в базе копились бы вложения без владельца.
    Размеры, длительность и вид берутся из хранилища, а не из этого запроса.
    """

    upload_key: str
    file_name: str
    size_bytes: int = 0
    mime_type: str | None = None


class MessageCreate(BaseModel):
    """Отправка из чата: текст, вложения или и то, и другое."""

    text: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)
    # Служебное сообщение видно только внутри CRM и в Telegram не уходит.
    is_internal: bool = False
    uploads: list[UploadRef] | None = None
    # Для случаев, когда вложение уже существует в базе (повтор отправки).
    attachment_ids: list[int] | None = None


class MessageEdit(BaseModel):
    """Правка текста уже отправленного сообщения."""

    text: str = Field(min_length=1, max_length=MAX_TEXT_LENGTH)


class ForwardRequest(BaseModel):
    """Переслать сообщения из одного чата CRM в другой."""

    source_conversation_id: int = Field(gt=0)
    message_ids: list[int] = Field(min_length=1, max_length=MAX_FORWARD_MESSAGES)
    # Клиент Б не должен видеть имя клиента А — поэтому по умолчанию скрываем.
    hide_sender: bool = True
    # Необязательный текст — уходит отдельным сообщением перед пересланными.
    comment: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)


class ForwardResult(BaseModel):
    conversation_id: int
    messages: list[MessageOut]
    # native — настоящая пересылка Telegram; copy — отправка копией
    mode: str
