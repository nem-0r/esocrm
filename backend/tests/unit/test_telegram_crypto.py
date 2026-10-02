"""Шифрование Telegram должно идти через cryptg, а не через pyaes на чистом Python.

Без cryptg всё работает, но в 90 раз медленнее (docs/17-resource-autoscaling.md §3.2):
скачивание гигабайта файлов занимает почти четыре минуты чистого процессора шлюза
вместо трёх секунд. Тест ловит тихую потерю зависимости — например, образ без колеса
cryptg для новой версии Python.
"""

import os

from telethon.crypto import aes


def test_cryptg_is_used_for_aes_ige():
    assert getattr(aes, "cryptg", None) is not None, (
        "Telethon не нашёл cryptg и считает AES на чистом Python — "
        "проверьте зависимость cryptg в pyproject.toml"
    )


def test_aes_ige_round_trip_with_cryptg():
    key, iv = os.urandom(32), os.urandom(32)
    data = os.urandom(64 * 1024)
    encrypted = aes.AES.encrypt_ige(data, key, iv)
    assert encrypted != data
    assert aes.AES.decrypt_ige(encrypted, key, iv)[: len(data)] == data
