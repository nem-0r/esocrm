"""Файлы: загрузка вложений и их выдача.

Загрузка **не создаёт** запись в базе — только кладёт файл в объектное
хранилище и возвращает ключ. Строка `attachments` появляется позже, когда
известны сообщение, диалог и клиент — при отправке (см. `attach_uploads`,
её вызывает сервис переписки).

Что изменилось 27.09.2026 (docs/14-release-plan-2026-09-27.md, §2):

- Тип файла определяет **сервер** — по расширению и по содержимому (ffprobe),
  а не по тому, что заявил браузер. Всё, что сервер узнал (вид, размеры,
  длительность, волна, превью), лежит в метаданных объекта хранилища и при
  отправке читается оттуда же.
- Голосовое с микрофона перекодируется в формат голосовых Telegram.
- Файлы до 200 МБ ходят потоком через временный файл, а не через память.

Выдача файлов браузеру — `app/services/file_serving.py`.
"""

import asyncio
import base64
import contextlib
import os
import re
import shutil
import tempfile
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any, BinaryIO
from urllib.parse import quote, unquote

from fastapi import UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import storage
from app.core.errors import Invalid
from app.models import Attachment, AttachmentKind, AttachmentStatus, Conversation, Message
from app.schemas.common import ApiModel
from app.services import media
from app.services.file_types import (
    ALLOWED_EXTENSIONS,
    AUDIO_EXTENSIONS,
    EXTENSION_MIME,
    IMAGE_EXTENSIONS,
    VIDEO_EXTENSIONS,
)

MAX_UPLOAD_BYTES = 200 * 1024 * 1024
# Запись с микрофона до перекодирования. Час разговора в WebM — около 30 МБ;
# голосовое такой длины уже не голосовое, а лекция.
MAX_VOICE_BYTES = 40 * 1024 * 1024
MIN_VOICE_SECONDS = 1
_COPY_CHUNK = 1024 * 1024


class UploadResult(ApiModel):
    """Ответ загрузки. В базу ничего не пишется — ключ живёт до отправки сообщения.

    Всё, кроме ключа и имени, — для превью в поле ввода. При отправке сервер
    берёт эти сведения не из запроса, а из метаданных объекта в хранилище.
    """

    upload_key: str
    file_name: str
    size_bytes: int
    mime_type: str
    kind: str = AttachmentKind.DOCUMENT.value
    width: int | None = None
    height: int | None = None
    duration_sec: int | None = None
    waveform: list[int] | None = None


# ----------------------------------------------------------------- загрузка


def _safe_name(filename: str | None) -> str:
    """Имя без пути и управляющих символов, не длиннее колонки в базе."""
    name = PurePosixPath((filename or "").replace("\\", "/")).name
    name = "".join(ch for ch in name if ch.isprintable()).strip()
    if len(name) > 200:
        stem, dot, ext = name.rpartition(".")
        name = (stem[: 200 - len(ext) - 1] + dot + ext) if dot else name[:200]
    return name


def _copy_limited(source: BinaryIO, target_path: str, limit: int) -> int:
    """Переписать загрузку во временный файл, обрывая по превышении лимита."""
    total = 0
    with open(target_path, "wb") as target:
        while chunk := source.read(_COPY_CHUNK):
            total += len(chunk)
            if total > limit:
                raise Invalid(f"Файл больше {limit // (1024 * 1024)} МБ")
            target.write(chunk)
    if total == 0:
        raise Invalid("Файл пустой")
    return total


async def _spool(file: UploadFile, target_path: str, limit: int) -> int:
    await file.seek(0)
    return await asyncio.to_thread(_copy_limited, file.file, target_path, limit)


@dataclass(slots=True)
class _Analysis:
    kind: str
    mime_type: str
    width: int | None = None
    height: int | None = None
    duration_sec: int | None = None
    title: str | None = None
    performer: str | None = None
    thumb_path: str | None = None


async def _analyze(path: str, ext: str, workdir: str) -> _Analysis:
    """Что за файл на самом деле: вид, размеры, длительность, превью."""
    mime = EXTENSION_MIME[ext]
    if ext in IMAGE_EXTENSIONS:
        size = media.image_size(path)
        kind = AttachmentKind.ANIMATION if ext == "gif" else AttachmentKind.PHOTO
        return _Analysis(
            kind=kind.value,
            mime_type=mime,
            width=size[0] if size else None,
            height=size[1] if size else None,
        )
    if ext in VIDEO_EXTENSIONS or ext in AUDIO_EXTENSIONS:
        info = await media.probe(path)
        if info is None or not (info.has_video or info.has_audio):
            # Файл с «медийным» расширением, который ffprobe не понял, — уйдёт
            # документом: так клиент хотя бы получит его, а не ошибку Telegram.
            return _Analysis(kind=AttachmentKind.DOCUMENT.value, mime_type=mime)
        if info.has_video and ext in VIDEO_EXTENSIONS:
            thumb = os.path.join(workdir, "thumb.jpg")
            has_thumb = await media.video_thumbnail(path, thumb, info.duration)
            return _Analysis(
                kind=AttachmentKind.VIDEO.value,
                mime_type=mime,
                width=info.width,
                height=info.height,
                duration_sec=info.duration_sec,
                thumb_path=thumb if has_thumb else None,
            )
        # Звуковая дорожка без картинки: webm с голосом, mp3, m4a.
        audio_mime = "audio/webm" if ext == "webm" else ("audio/ogg" if ext == "ogg" else mime)
        return _Analysis(
            kind=AttachmentKind.AUDIO.value,
            mime_type=audio_mime,
            duration_sec=info.duration_sec,
            title=(info.title or None),
            performer=(info.performer or None),
        )
    return _Analysis(kind=AttachmentKind.DOCUMENT.value, mime_type=mime)


def _meta_text(value: str | None) -> str | None:
    """Метаданные хранилища — только ASCII: кириллицу кодируем как в URL."""
    if not value:
        return None
    return quote(value[:120], safe="")


def _object_metadata(
    analysis: _Analysis, thumb_key: str | None, wave: list[int] | None
) -> dict[str, str]:
    meta: dict[str, str] = {"kind": analysis.kind}
    if analysis.width:
        meta["w"] = str(analysis.width)
    if analysis.height:
        meta["h"] = str(analysis.height)
    if analysis.duration_sec is not None:
        meta["dur"] = str(analysis.duration_sec)
    if thumb_key:
        meta["thumb"] = thumb_key
    if wave:
        meta["wave"] = base64.b64encode(bytes(wave)).decode("ascii")
    if title := _meta_text(analysis.title):
        meta["title"] = title
    if performer := _meta_text(analysis.performer):
        meta["performer"] = performer
    return meta


def _new_key(prefix: str, ext: str) -> str:
    now = datetime.now(UTC)
    return f"{prefix}/{now:%Y}/{now:%m}/{uuid.uuid4()}.{ext}"


# Ключи, которые браузер может приложить к сообщению: только то, что загружено
# через /files/upload и /files/voice, — не входящие файлы чужих диалогов. Ключ
# сверяется целиком с тем, что выдаёт _new_key (префикс/год/месяц/uuid.расширение):
# проверка по началу строки пропустила бы «uploads/../incoming/…», и если
# хранилище схлопнет «..» в пути, к сообщению прикрепился бы чужой файл.
_UPLOAD_KEY = re.compile(
    r"(uploads|voice)/\d{4}/\d{2}/"
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.[a-z0-9]{1,16}"
)


def is_upload_key(key: object, prefixes: tuple[str, ...] = ("uploads", "voice")) -> bool:
    """Ключ выдан загрузкой CRM (а не подставлен руками) и лежит под одним из префиксов."""
    match = _UPLOAD_KEY.fullmatch(key) if isinstance(key, str) else None
    return match is not None and match.group(1) in prefixes


async def upload(file: UploadFile) -> UploadResult:
    """Проверить, изучить, сохранить в хранилище и вернуть ключ для отправки."""
    file_name = _safe_name(file.filename)
    ext = media.extension(file_name)
    if not file_name or ext not in ALLOWED_EXTENSIONS:
        raise Invalid("Недопустимый тип файла", allowed_extensions=sorted(ALLOWED_EXTENSIONS))

    workdir = tempfile.mkdtemp(prefix="astra-upload-")
    try:
        source = os.path.join(workdir, f"source.{ext}")
        size = await _spool(file, source, MAX_UPLOAD_BYTES)
        analysis = await _analyze(source, ext, workdir)
        key = _new_key("uploads", ext)
        thumb_key = None
        if analysis.thumb_path:
            thumb_key = f"{key}.thumb.jpg"
            await storage.put_file(thumb_key, analysis.thumb_path, "image/jpeg")
        await storage.put_file(
            key, source, analysis.mime_type, _object_metadata(analysis, thumb_key, None)
        )
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    return UploadResult(
        upload_key=key,
        file_name=file_name,
        size_bytes=size,
        mime_type=analysis.mime_type,
        kind=analysis.kind,
        width=analysis.width,
        height=analysis.height,
        duration_sec=analysis.duration_sec,
    )


async def upload_voice(file: UploadFile) -> UploadResult:
    """Запись с микрофона → голосовое в формате Telegram (Ogg/Opus) + волна.

    Браузер записывает что умеет — WebM, Ogg или MP4, — поэтому на входе
    не расширение важно, а то, что внутри есть звук.
    """
    workdir = tempfile.mkdtemp(prefix="astra-voice-")
    try:
        source = os.path.join(workdir, "recording")
        await _spool(file, source, MAX_VOICE_BYTES)
        info = await media.probe(source)
        if info is None or not info.has_audio:
            raise Invalid("Запись не распознана — попробуйте записать ещё раз")
        target = os.path.join(workdir, "voice.ogg")
        try:
            await media.transcode_voice(source, target)
        except media.MediaError as exc:
            raise Invalid("Не удалось обработать запись — попробуйте ещё раз") from exc
        encoded = await media.probe(target)
        duration = (encoded.duration_sec if encoded else None) or info.duration_sec or 0
        if duration < MIN_VOICE_SECONDS:
            raise Invalid("Запись слишком короткая")
        wave = await media.waveform(target)
        analysis = _Analysis(
            kind=AttachmentKind.VOICE.value, mime_type="audio/ogg", duration_sec=duration
        )
        key = _new_key("voice", "ogg")
        size = media.file_size(target)
        await storage.put_file(key, target, "audio/ogg", _object_metadata(analysis, None, wave))
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    return UploadResult(
        upload_key=key,
        file_name="Голосовое.ogg",
        size_bytes=size,
        mime_type="audio/ogg",
        kind=AttachmentKind.VOICE.value,
        duration_sec=duration,
        waveform=wave,
    )


# ------------------------------------------------------ загрузка → вложение


def _int(value: str | None) -> int | None:
    with contextlib.suppress(TypeError, ValueError):
        number = int(value)  # type: ignore[arg-type]
        return number if number >= 0 else None
    return None


def attachment_fields_from_head(head: storage.ObjectHead) -> dict[str, Any]:
    """Сведения о файле, которые сервер сам записал при загрузке."""
    meta = head.metadata
    wave = None
    if meta.get("wave"):
        with contextlib.suppress(ValueError):
            wave = base64.b64decode(meta["wave"])
    extra = {
        key: unquote(meta[key]) for key in ("title", "performer") if meta.get(key)
    }
    kind = meta.get("kind")
    known_kinds = {k.value for k in AttachmentKind}
    return {
        "kind": kind if kind in known_kinds else AttachmentKind.DOCUMENT.value,
        "mime_type": head.content_type,
        "size_bytes": head.size,
        "width": _int(meta.get("w")),
        "height": _int(meta.get("h")),
        "duration_sec": _int(meta.get("dur")),
        "waveform": wave,
        "thumb_key": meta.get("thumb") or None,
        "meta": extra or None,
    }


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
        file_name = _safe_name(item.get("file_name"))
        if not file_name or not is_upload_key(storage_key):
            raise Invalid("Не хватает данных о загруженном файле")
        head = await storage.head(storage_key)
        if head is None:
            raise Invalid("Файл не найден в хранилище — загрузите его ещё раз")
        attachment = Attachment(
            message_id=message.id,
            conversation_id=conversation.id,
            client_id=client_id,
            file_name=file_name,
            storage_key=storage_key,
            status=AttachmentStatus.READY.value,
            **attachment_fields_from_head(head),
        )
        db.add(attachment)
        attachments.append(attachment)

    if attachments:
        await db.flush()
    return attachments
