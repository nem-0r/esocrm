"""Выдача файлов браузеру: права, безопасные заголовки, куски (HTTP Range).

Отдельно от загрузки, потому что правила здесь другие и цена ошибки выше:

- **Показ в браузере — только безопасных типов.** HTML или SVG, открытый на
  домене CRM, исполнил бы свой JavaScript от имени вошедшего менеджера — и
  такой файл может прислать любой клиент в Telegram. Всё, кроме картинок,
  аудио, видео и PDF, отдаётся только на скачивание.
- **Куски файла по запросу (Range).** Без ответа 206 Safari не играет ни видео,
  ни аудио. Заодно большое видео не читается в память целиком.
- **Вариант для Safari.** Голосовые Telegram приходят в Ogg/Opus, которого
  Safari не понимает, — для него делается AAC-копия и кешируется.
"""

import asyncio
import mimetypes
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import PurePosixPath

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import storage
from app.core.deps import conversation_scope_orm, visible_account_ids
from app.core.errors import Invalid, NotFound
from app.models import Attachment, AttachmentStatus, Conversation, User
from app.services import media

# Что можно показывать прямо в браузере. Всё остальное отдаётся только на
# скачивание: HTML или SVG, открытый на домене CRM, исполнил бы свой
# JavaScript от имени вошедшего менеджера.
INLINE_MIME_TYPES = frozenset(
    {
        "image/jpeg",
        "image/png",
        "image/webp",
        "image/gif",
        "image/bmp",
        "video/mp4",
        "video/quicktime",
        "video/webm",
        "video/x-m4v",
        "audio/ogg",
        "audio/mpeg",
        "audio/mp4",
        "audio/aac",
        "audio/wav",
        "audio/x-wav",
        "audio/flac",
        "audio/webm",
        "application/pdf",
    }
)


@dataclass(slots=True)
class FileToServe:
    """Что и как отдать браузеру."""

    key: str
    size: int
    mime_type: str
    file_name: str
    inline: bool


async def _visible_attachment(db: AsyncSession, user: User, attachment_id: int) -> Attachment:
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
    return attachment


_derive_locks: dict[int, asyncio.Lock] = {}


async def _ensure_m4a(attachment: Attachment) -> tuple[str, int]:
    """AAC-версия голосового для Safari: делается один раз и кешируется."""
    key = f"derived/m4a/{attachment.id}.m4a"
    existing = await storage.head(key)
    if existing is not None:
        return key, existing.size
    lock = _derive_locks.setdefault(attachment.id, asyncio.Lock())
    async with lock:
        existing = await storage.head(key)
        if existing is not None:
            return key, existing.size
        workdir = tempfile.mkdtemp(prefix="astra-m4a-")
        try:
            source = os.path.join(workdir, "source")
            target = os.path.join(workdir, "voice.m4a")
            await storage.download_to_file(attachment.storage_key or "", source)
            try:
                await media.to_m4a(source, target)
            except media.MediaError as exc:
                raise NotFound("Не удалось подготовить запись для этого браузера") from exc
            await storage.put_file(key, target, "audio/mp4")
            return key, media.file_size(target)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
            _derive_locks.pop(attachment.id, None)


def _is_inline(mime_type: str | None) -> bool:
    return (mime_type or "").lower() in INLINE_MIME_TYPES


async def resolve(
    db: AsyncSession, user: User, attachment_id: int, variant: str | None, download: bool
) -> FileToServe:
    """Что отдать по `/files/{id}`: сам файл, превью видео или AAC для Safari."""
    attachment = await _visible_attachment(db, user, attachment_id)
    if attachment.status != AttachmentStatus.READY.value or not attachment.storage_key:
        if attachment.status == AttachmentStatus.PENDING.value:
            raise NotFound("Файл ещё загружается из Telegram")
        raise NotFound("Файл не сохранён в CRM — откройте его в Telegram")

    if variant == "thumb":
        if not attachment.thumb_key:
            raise NotFound("У файла нет превью")
        head = await storage.head(attachment.thumb_key)
        if head is None:
            raise NotFound("У файла нет превью")
        return FileToServe(attachment.thumb_key, head.size, "image/jpeg", "preview.jpg", True)

    if variant == "m4a":
        if not (attachment.mime_type or "").startswith("audio/"):
            raise Invalid("Этот вариант есть только у аудио")
        key, size = await _ensure_m4a(attachment)
        stem = PurePosixPath(attachment.file_name).stem or "audio"
        return FileToServe(key, size, "audio/mp4", f"{stem}.m4a", True)

    if variant:
        raise Invalid("Неизвестный вариант файла")

    size = attachment.size_bytes
    if not size:
        head = await storage.head(attachment.storage_key)
        if head is None:
            raise NotFound("Файл не найден")
        size = head.size
    mime = attachment.mime_type or mimetypes.guess_type(attachment.file_name)[0] or ""
    inline = _is_inline(mime) and not download
    return FileToServe(
        attachment.storage_key,
        size,
        mime if _is_inline(mime) else "application/octet-stream",
        attachment.file_name,
        inline,
    )


def parse_range(header: str | None, size: int) -> tuple[int, int] | None:
    """`Range: bytes=…` → (начало, конец включительно). None — отдать целиком.

    Несколько диапазонов через запятую браузеры для медиа не просят —
    на такой запрос честно отдаём файл целиком. Неисполнимый диапазон —
    ValueError: ответ 416, как требует HTTP.
    """
    if not header or not header.startswith("bytes=") or size <= 0:
        return None
    spec = header[len("bytes=") :].strip()
    if "," in spec:
        return None
    start_raw, _, end_raw = spec.partition("-")
    try:
        if start_raw == "":
            length = int(end_raw)
            if length <= 0:
                raise ValueError("пустой суффикс")
            return max(0, size - length), size - 1
        start = int(start_raw)
        end = int(end_raw) if end_raw else size - 1
    except ValueError as exc:
        raise ValueError("битый диапазон") from exc
    if start >= size or start < 0 or end < start:
        raise ValueError("диапазон вне файла")
    return start, min(end, size - 1)
