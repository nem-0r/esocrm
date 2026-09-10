"""Файлы: загрузка вложений и их выдача.

Загрузка **не создаёт** запись в базе — только кладёт файл в объектное
хранилище и возвращает ключ. Строка `attachments` появляется позже, когда
известны сообщение, диалог и клиент — при отправке (см. `attach_uploads`,
её вызывает сервис переписки).

Выдача файла проверяет право видеть диалог вложения — как и везде, чужой
файл не существует: 404, а не 403.
"""

import mimetypes
import uuid
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any

from fastapi import UploadFile
from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import storage
from app.core.deps import conversation_scope_orm, visible_account_ids
from app.core.errors import Invalid, NotFound
from app.models import Attachment, Conversation, Message, User
from app.schemas.common import ApiModel

MAX_UPLOAD_BYTES = 50 * 1024 * 1024
_READ_CHUNK = 1024 * 1024

# Расширение и MIME-тип проверяются отдельными списками: должно совпасть и то,
# и другое, иначе поддельное расширение с чужим содержимым прошло бы проверку.
ALLOWED_EXTENSIONS = {
    "pdf", "doc", "docx", "xls", "xlsx",
    "jpg", "jpeg", "png", "webp", "gif", "heic", "heif", "bmp", "tiff", "tif",
    "mp4", "mov", "ogg", "oga", "mp3", "m4a",
    "txt",
}

ALLOWED_MIME_TYPES = {
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/gif",
    # Скриншоты и фото с айфона по умолчанию — HEIC/HEIF; чек оплаты чаще
    # всего именно скриншот или фото с телефона, не только PNG.
    "image/heic",
    "image/heif",
    "image/bmp",
    "image/tiff",
    "video/mp4",
    "video/quicktime",
    "audio/ogg",
    "video/ogg",
    "application/ogg",
    "audio/mpeg",
    "audio/mp3",
    "audio/mp4",
    "audio/x-m4a",
    "audio/m4a",
    "text/plain",
}


class UploadResult(ApiModel):
    """Ответ загрузки. В базу ничего не пишется — ключ живёт до отправки сообщения."""

    upload_key: str
    file_name: str
    size_bytes: int
    mime_type: str


def _validate(filename: str, content_type: str | None) -> tuple[str, str]:
    name = PurePosixPath(filename or "").name
    ext = PurePosixPath(name).suffix.lower().lstrip(".")
    if not name or ext not in ALLOWED_EXTENSIONS:
        raise Invalid("Недопустимый тип файла", allowed_extensions=sorted(ALLOWED_EXTENSIONS))

    mime = (content_type or mimetypes.guess_type(name)[0] or "").lower()
    if mime not in ALLOWED_MIME_TYPES:
        raise Invalid("Недопустимый тип файла", allowed_extensions=sorted(ALLOWED_EXTENSIONS))
    return name, mime


async def _read_limited(file: UploadFile) -> bytes:
    """Читаем кусками и обрываем сразу по превышении лимита — не грузим
    в память лишнее, если файл заведомо больше 50 МБ."""
    chunks: list[bytes] = []
    total = 0
    while chunk := await file.read(_READ_CHUNK):
        total += len(chunk)
        if total > MAX_UPLOAD_BYTES:
            raise Invalid("Файл больше 50 МБ")
        chunks.append(chunk)
    if total == 0:
        raise Invalid("Файл пустой")
    return b"".join(chunks)


async def upload(file: UploadFile) -> UploadResult:
    """Проверить, сохранить в хранилище и вернуть ключ для последующей отправки."""
    file_name, mime_type = _validate(file.filename or "", file.content_type)
    body = await _read_limited(file)

    now = datetime.now(UTC)
    ext = PurePosixPath(file_name).suffix.lower()
    key = f"uploads/{now:%Y}/{now:%m}/{uuid.uuid4()}{ext}"
    await storage.put_object(key, body, filename=file_name)

    return UploadResult(
        upload_key=key, file_name=file_name, size_bytes=len(body), mime_type=mime_type
    )


async def download(db: AsyncSession, user: User, attachment_id: int) -> tuple[Attachment, bytes]:
    """Файл вложения вместе с его метаданными. Видимость — как у диалога."""
    attachment = await db.scalar(
        select(Attachment).where(Attachment.id == attachment_id, Attachment.deleted_at.is_(None))
    )
    if attachment is None:
        raise NotFound("Файл не найден")

    account_ids = await visible_account_ids(db, user)
    if account_ids is not None:
        visible = await db.scalar(
            select(
                exists().where(
                    Conversation.id == attachment.conversation_id,
                    *conversation_scope_orm(user, account_ids, Conversation),
                )
            )
        )
        if not visible:
            raise NotFound("Файл не найден")

    try:
        body = await storage.get_object(attachment.storage_key)
    except storage.ObjectNotFound as exc:
        raise NotFound("Файл не найден") from exc
    return attachment, body


async def attach_uploads(
    db: AsyncSession,
    *,
    message: Message,
    conversation: Conversation,
    client_id: int,
    uploads: list[dict[str, Any]],
) -> list[Attachment]:
    """Создать строки `attachments` по уже загруженным файлам.

    Вызывается при отправке сообщения, когда известны диалог и клиент.
    Транзакцию не закрывает — коммитит вызывающий, как и `send_outgoing`:
    сообщение и его вложения обязаны попасть в базу вместе или не попасть вовсе.
    """
    attachments: list[Attachment] = []
    for item in uploads:
        storage_key = item.get("upload_key")
        file_name = item.get("file_name")
        if not storage_key or not file_name:
            raise Invalid("Не хватает данных о загруженном файле")
        attachment = Attachment(
            message_id=message.id,
            conversation_id=conversation.id,
            client_id=client_id,
            file_name=file_name,
            mime_type=item.get("mime_type"),
            size_bytes=int(item.get("size_bytes") or 0),
            storage_key=storage_key,
        )
        db.add(attachment)
        attachments.append(attachment)

    if attachments:
        await db.flush()
    return attachments
