"""Шифрование секретов, которые обязаны лежать в базе: сессии Telegram и api_hash.

Файл сессии равен полному доступу к аккаунту — это главный секрет системы.
Ключ живёт в переменной окружения, не в базе и не в репозитории.
"""

import base64
import hashlib
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import settings

_NONCE_SIZE = 12


def _key() -> bytes:
    return hashlib.sha256(settings.encryption_key.encode()).digest()


def encrypt(plaintext: str) -> bytes:
    nonce = os.urandom(_NONCE_SIZE)
    return nonce + AESGCM(_key()).encrypt(nonce, plaintext.encode(), None)


def decrypt(blob: bytes | None) -> str | None:
    if not blob:
        return None
    nonce, payload = blob[:_NONCE_SIZE], blob[_NONCE_SIZE:]
    return AESGCM(_key()).decrypt(nonce, payload, None).decode()


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_token() -> str:
    return base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("=")
