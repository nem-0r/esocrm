"""Файлы: загрузка вложений и раздача уже загруженных.

Роутер тонкий: построение HTTP-ответа (заголовки, стрим) — не бизнес-логика,
поэтому оно здесь, как и заголовки CSV-экспорта в `clients.py`.
"""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, File, UploadFile
from fastapi.responses import StreamingResponse

from app.core.deps import CurrentUser, Db
from app.services import file_service
from app.services.export_format import content_disposition
from app.services.file_service import UploadResult

router = APIRouter()

_CHUNK_SIZE = 1024 * 1024


async def _chunks(body: bytes) -> AsyncIterator[bytes]:
    for offset in range(0, len(body), _CHUNK_SIZE):
        yield body[offset : offset + _CHUNK_SIZE]


def _content_disposition(file_name: str) -> str:
    """Заголовок обязан быть latin-1 — для русских имён нужна форма RFC 5987.

    Общий с CSV-выгрузками код: он же вычищает из имени кавычку и перевод
    строки, которыми загруженный файл иначе разломал бы заголовок ответа.
    """
    return content_disposition(file_name, disposition="inline")


@router.post(
    "/upload",
    response_model=UploadResult,
    status_code=201,
    summary="Загрузить файл перед отправкой",
)
async def upload_file(
    user: CurrentUser,
    file: Annotated[UploadFile, File(description="Файл в поле form-data `file`, до 50 МБ")],
) -> UploadResult:
    return await file_service.upload(file)


@router.get("/{attachment_id}", summary="Скачать вложение")
async def download_file(db: Db, user: CurrentUser, attachment_id: int) -> StreamingResponse:
    attachment, body = await file_service.download(db, user, attachment_id)
    return StreamingResponse(
        _chunks(body),
        media_type=attachment.mime_type or "application/octet-stream",
        headers={
            "Content-Disposition": _content_disposition(attachment.file_name),
            # Содержимое вложения неизменяемо: id указывает на конкретный файл
            # раз и навсегда. Год кэша в браузере — это ровно то, что нужно
            # плееру голосовых: одно скачивание, дальше повтор без сети.
            "Cache-Control": "private, max-age=31536000, immutable",
            "ETag": f'"{attachment_id}"',
        },
    )
