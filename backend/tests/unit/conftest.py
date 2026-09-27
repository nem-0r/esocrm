"""Модульные тесты: без базы, без Redis, без Telegram — только логика.

Запуск: `pytest tests/unit` (в контейнере api или в любом окружении с
зависимостями бэкенда). Проверки, которым нужен ffmpeg, пропускаются, если его
нет в системе, — в образе бэкенда он есть всегда.
"""

import shutil

import pytest

needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="нужен ffmpeg",
)
