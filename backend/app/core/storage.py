"""Объектное хранилище для вложений. Локально — MinIO, в продакшене — S3-совместимое."""

import asyncio
import contextlib
import mimetypes
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from app.core.config import settings

_client: Any = None


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


class ObjectNotFound(RuntimeError):
    """Ключ есть в базе, а файла под ним в хранилище уже нет."""


async def get_object(key: str) -> bytes:
    def _get() -> bytes:
        try:
            return get_client().get_object(Bucket=settings.s3_bucket, Key=key)["Body"].read()
        except ClientError as exc:
            raise ObjectNotFound(key) from exc

    return await asyncio.to_thread(_get)


async def object_exists(key: str) -> bool:
    def _head() -> bool:
        try:
            get_client().head_object(Bucket=settings.s3_bucket, Key=key)
        except ClientError:
            return False
        return True

    return await asyncio.to_thread(_head)
