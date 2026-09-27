"""Общее для сквозных проверок: учёт результатов, вход, ожидание, медиафайлы.

Проверки бьют по работающему стенду (api на localhost:8000 внутри контейнера,
шлюз в демо-режиме), как и остальные verify_*.
"""

import asyncio
import os
import subprocess
import tempfile
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

BASE = "http://localhost:8000/api/v1"
ADMIN = {"email": "elena@astra.ru", "password": "demo1234"}
MANAGER = {"email": "marina@astra.ru", "password": "demo1234"}


class Checks:
    def __init__(self, title: str) -> None:
        self.ok: list[str] = []
        self.bad: list[str] = []
        print(f"{title}\n")

    def section(self, title: str) -> None:
        print(f"\n{title}")

    def check(self, title: str, condition: bool, detail: Any = "") -> bool:
        (self.ok if condition else self.bad).append(title if condition else f"{title} — {detail}")
        suffix = f" — {detail}" if detail not in ("", None) and not condition else ""
        print(f"  {'✓' if condition else '✗'} {title}{suffix}", flush=True)
        return condition

    def finish(self) -> int:
        print(f"\nИтог: {len(self.ok)} выполнено, {len(self.bad)} не выполнено")
        for line in self.bad:
            print(f"  — {line}")
        return 1 if self.bad else 0


async def login(creds: dict[str, str]) -> httpx.AsyncClient:
    client = httpx.AsyncClient(timeout=60)
    response = await client.post(f"{BASE}/auth/login", json=creds)
    response.raise_for_status()
    return client


async def until(
    probe: Callable[[], Awaitable[Any]], seconds: float = 15.0, step: float = 0.3
) -> Any:
    """Ждать, пока `probe()` вернёт истинное значение. Возвращает последнее."""
    deadline = time.monotonic() + seconds
    value = await probe()
    while not value and time.monotonic() < deadline:
        await asyncio.sleep(step)
        value = await probe()
    return value


# ------------------------------------------------------------ медиафайлы


def ffmpeg(*args: str) -> None:
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args],
        check=True,
    )


def make_media(workdir: str | None = None) -> dict[str, str]:
    """Набор настоящих файлов: голос из браузера, видео, музыка, картинки."""
    from PIL import Image

    root = workdir or tempfile.mkdtemp(prefix="astra-verify-")
    paths = {
        "voice_webm": os.path.join(root, "recording.webm"),
        "voice_short": os.path.join(root, "short.webm"),
        "video": os.path.join(root, "clip.mp4"),
        "song": os.path.join(root, "song.mp3"),
        "photo": os.path.join(root, "photo.png"),
        "photo_jpg": os.path.join(root, "photo.jpg"),
        "doc": os.path.join(root, "Разбор карты.pdf"),
        "html": os.path.join(root, "page.html"),
    }
    # Так пишет Chrome: WebM с Opus. Первая половина громче второй — видно на волне.
    ffmpeg(
        "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
        "-filter_complex", "[0]volume=1.0[a];[1]volume=0.1[b];[a][b]concat=n=2:v=0:a=1",
        "-c:a", "libopus", "-f", "webm", paths["voice_webm"],
    )
    ffmpeg("-f", "lavfi", "-i", "sine=frequency=440:duration=0.3", "-c:a", "libopus", "-f", "webm",
           paths["voice_short"])
    ffmpeg(
        "-f", "lavfi", "-i", "testsrc=size=640x360:rate=25:duration=3",
        "-f", "lavfi", "-i", "sine=frequency=330:duration=3",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", paths["video"],
    )
    ffmpeg("-f", "lavfi", "-i", "sine=frequency=500:duration=4", "-metadata", "title=Луна",
           "-metadata", "artist=Хор", paths["song"])
    Image.new("RGB", (800, 600), (30, 90, 200)).save(paths["photo"], "PNG")
    Image.new("RGB", (640, 480), (200, 90, 30)).save(paths["photo_jpg"], "JPEG")
    with open(paths["doc"], "wb") as target:
        target.write(b"%PDF-1.4\n% verify\n" + os.urandom(4096))
    with open(paths["html"], "wb") as target:
        target.write(b"<html><script>alert(1)</script></html>")
    return paths


def probe(path: str) -> dict[str, Any]:
    import json

    out = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path],
        check=True,
        capture_output=True,
    ).stdout
    return json.loads(out)
