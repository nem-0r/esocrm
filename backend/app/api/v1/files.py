"""Файлы: загрузка вложений и раздача уже загруженных.

Роутер тонкий: построение HTTP-ответа (заголовки, куски файла) — не бизнес-логика,
поэтому оно здесь, как и заголовки CSV-экспорта в `clients.py`. Какой файл и
можно ли его показывать в браузере, решает `app/services/file_serving.py`.
"""

from typing import Annotated

from fastapi import APIRouter, File, Query, Request, UploadFile
from fastapi.responses import Response, StreamingResponse

from app.core import storage
from app.core.deps import CurrentUser, Db
from app.core.errors import NotFound
from app.services import file_service, file_serving
from app.services.export_format import content_disposition
from app.services.file_service import UploadResult

router = APIRouter()

# Для всего, что не картинка/звук/видео/PDF: даже если браузер решит открыть
# ответ как страницу, скрипты в нём не выполнятся и до API не дотянутся.
_SANDBOX_CSP = "sandbox; default-src 'none'; img-src 'self' data:; media-src 'self'"


def _headers(
    target: file_serving.FileToServe, attachment_id: int, variant: str | None
) -> dict[str, str]:
    headers = {
        "Content-Disposition": content_disposition(
            target.file_name, disposition="inline" if target.inline else "attachment"
        ),
        # Содержимое по id и варианту неизменно раз и навсегда: год кэша в
        # браузере — одно скачивание голосового, дальше повтор без сети.
        "Cache-Control": "private, max-age=31536000, immutable",
        "ETag": f'"{attachment_id}-{variant or "file"}"',
        "Accept-Ranges": "bytes",
        # Тип задаёт сервер, браузеру угадывать по содержимому нельзя: иначе
        # «картинка» с HTML внутри открылась бы как страница.
        "X-Content-Type-Options": "nosniff",
    }
    # PDF показывает встроенный просмотрщик браузера — песочница ему мешает,
    # а скрипты PDF и так не получают доступа к странице CRM.
    if target.mime_type != "application/pdf":
        headers["Content-Security-Policy"] = _SANDBOX_CSP
    return headers


@router.post(
    "/upload",
    response_model=UploadResult,
    status_code=201,
    summary="Загрузить файл перед отправкой",
)
async def upload_file(
    user: CurrentUser,
    file: Annotated[UploadFile, File(description="Файл в поле form-data `file`, до 200 МБ")],
) -> UploadResult:
    return await file_service.upload(file)


@router.post(
    "/voice",
    response_model=UploadResult,
    status_code=201,
    summary="Загрузить запись с микрофона — станет голосовым сообщением Telegram",
)
async def upload_voice(
    user: CurrentUser,
    file: Annotated[UploadFile, File(description="Запись MediaRecorder: webm, ogg или mp4")],
) -> UploadResult:
    return await file_service.upload_voice(file)


@router.get("/{attachment_id}", summary="Скачать или показать вложение")
async def download_file(
    request: Request,
    db: Db,
    user: CurrentUser,
    attachment_id: int,
    variant: Annotated[
        str | None,
        Query(
            pattern="^(thumb|m4a)$",
            description="thumb — кадр-превью видео, m4a — звук для Safari",
        ),
    ] = None,
    download: Annotated[bool, Query(description="Скачать, даже если можно показать")] = False,
) -> Response:
    target = await file_serving.resolve(db, user, attachment_id, variant, download)
    headers = _headers(target, attachment_id, variant)

    if request.headers.get("if-none-match") == headers["ETag"]:
        return Response(status_code=304, headers=headers)

    try:
        byte_range = file_serving.parse_range(request.headers.get("range"), target.size)
    except ValueError:
        return Response(
            status_code=416,
            headers={**headers, "Content-Range": f"bytes */{target.size}"},
        )

    try:
        if byte_range is None:
            chunks = await storage.open_stream(target.key)
            headers["Content-Length"] = str(target.size)
            return StreamingResponse(
                chunks, status_code=200, media_type=target.mime_type, headers=headers
            )
        start, end = byte_range
        chunks = await storage.open_stream(target.key, start, end)
    except storage.ObjectNotFound as exc:
        raise NotFound("Файл не найден") from exc

    headers["Content-Range"] = f"bytes {start}-{end}/{target.size}"
    headers["Content-Length"] = str(end - start + 1)
    return StreamingResponse(chunks, status_code=206, media_type=target.mime_type, headers=headers)
