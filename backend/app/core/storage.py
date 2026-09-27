"""Объектное хранилище для вложений. Локально — MinIO, в продакшене — S3-совместимое.

Большие файлы (видео до 200 МБ) ходят потоком: загрузка из временного файла
частями, выдача — только запрошенный кусок (HTTP Range). Целиком в память
процесса они не попадают ни на приёме, ни на отдаче.
"""

import asyncio
import contextlib
import mimetypes
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import boto3
from boto3.s3.transfer import TransferConfig
from botocore.config import Config
from botocore.exceptions import ClientError

from app.core.config import settings

_client: Any = None

# Части по 8 МБ: файл до 200 МБ уходит в хранилище за 25 запросов, а память
# процесса при этом не растёт больше чем на пару частей.
_TRANSFER = TransferConfig(
    multipart_threshold=16 * 1024 * 1024, multipart_chunksize=8 * 1024 * 1024
)
_STREAM_CHUNK = 256 * 1024


def get_client() -> Any:
    global _client
    if _client is None:
        _client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            region_name=settings.s3_region,
            config=Config(signature_version="s3v4"),
        )
    return _client


def ensure_bucket() -> None:
    client = get_client()
    try:
        client.head_bucket(Bucket=settings.s3_bucket)
    except ClientError:
        # Корзина могла появиться параллельно другим процессом — это не ошибка.
        with contextlib.suppress(ClientError):
            client.create_bucket(Bucket=settings.s3_bucket)


async def put_object(key: str, body: bytes, filename: str | None = None) -> None:
    content_type = mimetypes.guess_type(filename or key)[0] or "application/octet-stream"

    def _put() -> None:
        get_client().put_object(
            Bucket=settings.s3_bucket, Key=key, Body=body, ContentType=content_type
        )

    await asyncio.to_thread(_put)


async def put_file(
    key: str,
    path: str,
    content_type: str,
    metadata: dict[str, str] | None = None,
) -> None:
    """Положить файл с диска частями. `metadata` — то, что сервер узнал о файле
    (вид, размеры, длительность): при отправке он читает это отсюда, а не
    доверяет тому, что пришлёт браузер."""

    def _upload() -> None:
        get_client().upload_file(
            path,
            settings.s3_bucket,
            key,
            ExtraArgs={"ContentType": content_type, "Metadata": metadata or {}},
            Config=_TRANSFER,
        )

    await asyncio.to_thread(_upload)


class ObjectNotFound(RuntimeError):
    """Ключ есть в базе, а файла под ним в хранилище уже нет."""


async def get_object(key: str) -> bytes:
    def _get() -> bytes:
        try:
            return get_client().get_object(Bucket=settings.s3_bucket, Key=key)["Body"].read()
        except ClientError as exc:
            raise ObjectNotFound(key) from exc

    return await asyncio.to_thread(_get)


@dataclass(slots=True)
class ObjectHead:
    size: int
    content_type: str | None
    metadata: dict[str, str]


async def head(key: str) -> ObjectHead | None:
    """Размер и метаданные объекта без скачивания. None — объекта нет."""

    def _head() -> ObjectHead | None:
        try:
            answer = get_client().head_object(Bucket=settings.s3_bucket, Key=key)
        except ClientError:
            return None
        return ObjectHead(
            size=int(answer.get("ContentLength") or 0),
            content_type=answer.get("ContentType"),
            metadata={str(k).lower(): str(v) for k, v in (answer.get("Metadata") or {}).items()},
        )

    return await asyncio.to_thread(_head)


async def object_exists(key: str) -> bool:
    return await head(key) is not None


async def download_to_file(key: str, path: str) -> None:
    """Скачать объект на диск частями — для отправки большого файла в Telegram."""

    def _download() -> None:
        try:
            get_client().download_file(settings.s3_bucket, key, path, Config=_TRANSFER)
        except ClientError as exc:
            raise ObjectNotFound(key) from exc

    await asyncio.to_thread(_download)


async def open_stream(
    key: str, start: int | None = None, end: int | None = None
) -> AsyncIterator[bytes]:
    """Открыть объект (или его кусок `start..end` включительно) для отдачи потоком.

    Кусок запрашивается у хранилища, а не вырезается из скачанного целиком:
    Safari просит видео маленькими диапазонами много раз подряд. Объект
    открывается сразу, до начала ответа: пропавший файл должен дать честный
    404, а не оборванный на середине ответ с кодом 200.
    """
    kwargs: dict[str, Any] = {"Bucket": settings.s3_bucket, "Key": key}
    if start is not None:
        kwargs["Range"] = f"bytes={start}-{'' if end is None else end}"

    def _open() -> Any:
        try:
            return get_client().get_object(**kwargs)["Body"]
        except ClientError as exc:
            raise ObjectNotFound(key) from exc

    body = await asyncio.to_thread(_open)

    async def _chunks() -> AsyncIterator[bytes]:
        try:
            while True:
                chunk = await asyncio.to_thread(body.read, _STREAM_CHUNK)
                if not chunk:
                    break
                yield chunk
        finally:
            with contextlib.suppress(Exception):
                body.close()

    return _chunks()
