"""Схемы переписки.

Статуса «доставлено» здесь нет и не будет: MTProto его не отдаёт.
Часы — в очереди, галочка — отправлено, две — прочитано, крестик — ошибка.
"""

from datetime import datetime

from pydantic import BaseModel, Field

from app.models.enums import AuthorKind, Direction, MessageKind, MessageStatus
from app.schemas.common import ApiModel

MAX_TEXT_LENGTH = 4096


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


class MessageAuthor(ApiModel):
    """Автор-сотрудник. Цвет аватара хранится у пользователя, чтобы совпадать везде."""

    id: int
    full_name: str
    avatar_color: str


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


class UploadRef(BaseModel):
    """То, что вернул `POST /files/upload`.

    Файл уже лежит в хранилище, но строки в базе ещё нет: она создаётся
    вместе с сообщением, иначе в базе копились бы вложения без владельца.
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
    # Для случаев, когда вложение уже существует в базе (пересылка, повтор отправки).
    attachment_ids: list[int] | None = None
