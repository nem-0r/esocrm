"""Понимание и преобразование медиафайлов: ffprobe и ffmpeg.

Зачем это CRM, если файлы просто пересылаются:

- **Голосовое.** Telegram показывает голосовое голосовым (с волной и кнопкой
  «слушать»), только если это Ogg/Opus. Браузер записывает что умеет: Chrome —
  WebM, Firefox — Ogg, Safari — MP4/AAC. Всё приводим к формату Telegram.
- **Видео.** Без ширины, высоты и длительности Telegram рисует видео клиенту
  квадратиком 1×1 с «0:00», без кадра-превью — серым прямоугольником.
- **Safari.** Не умеет Ogg/Opus вообще — голосовые клиентов на iPhone молчат.
  Для него голосовое перекодируется в AAC.

Каждый вызов — отдельный процесс с таймаутом и общим лимитом параллельности:
перекодирование не должно ни подвесить запрос навсегда, ни занять все ядра
сервера, на котором рядом работают база и шлюз Telegram.
"""

import asyncio
import contextlib
import json
import logging
import os
import shutil
from array import array
from dataclasses import dataclass, field
from pathlib import PurePosixPath

log = logging.getLogger("astra.media")

# Сколько конвертаций разом на процесс api. Больше двух тяжёлых задач на процесс
# превращают перекодирование в торможение всего, а процессов api бывает несколько
# (их число растёт с ядрами сервера, core/sizing.py), поэтому предел на процесс
# уменьшается, когда ядер на процесс остаётся мало: на 4 ядрах и 2 процессах — по 2
# (всего 4, как и было), на 4 ядрах и 4 процессах — по одной.
_MAX_PARALLEL_CAP = 2
_semaphore: asyncio.Semaphore | None = None

# Конвертация — фон: менеджерам и базе процессор нужнее, поэтому ffmpeg идёт с
# пониженным приоритетом (nice +10) и при нехватке уступает.
_NICE_LEVEL = "10"


def _max_parallel() -> int:
    from app.core.hostinfo import detected_cores

    try:
        workers = max(1, int(os.environ.get("API_WORKERS_RESOLVED", "") or 2))
    except ValueError:
        workers = 2
    return max(1, min(_MAX_PARALLEL_CAP, detected_cores() // workers))

PROBE_TIMEOUT = 30.0
TRANSCODE_TIMEOUT = 180.0

# Столько столбиков рисует Telegram в волне голосового, и столько же уровней.
WAVEFORM_POINTS = 100
WAVEFORM_MAX = 31
# Для огибающей громкости хватает 2 кГц: нужны пики, а не звук. Меньше
# выборок — быстрее расчёт на длинной записи.
_WAVEFORM_RATE = 2000

# Кадр-превью видео для Telegram: JPEG не больше 320 px по длинной стороне.
THUMB_SIDE = 320


class MediaError(RuntimeError):
    """Файл не удалось разобрать или преобразовать. Текст — для человека."""


def available() -> bool:
    """Есть ли ffmpeg в системе. В образе бэкенда он есть всегда; проверка
    нужна, чтобы локальный запуск без Docker падал понятной ошибкой."""
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def _sem() -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(_max_parallel())
    return _semaphore


async def _run(args: list[str], limit_seconds: float, *, stdout: bool = False) -> bytes:
    """Запустить ffmpeg/ffprobe и дождаться. Истёк срок — процесс убивается."""
    async with _sem():
        niced = ["nice", "-n", _NICE_LEVEL, *args] if shutil.which("nice") else args
        proc = await asyncio.create_subprocess_exec(
            *niced,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE if stdout else asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=limit_seconds)
        except TimeoutError as exc:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            await proc.wait()
            raise MediaError("Файл обрабатывается слишком долго") from exc
    if proc.returncode != 0:
        detail = (err or b"").decode(errors="replace").strip().splitlines()
        log.info("%s завершился с кодом %s: %s", args[0], proc.returncode, detail[-3:])
        raise MediaError("Не удалось обработать файл — возможно, он повреждён")
    return out or b""


# --------------------------------------------------------------------- probe


@dataclass(slots=True)
class ProbeResult:
    """Что ffprobe узнал о файле. Всё необязательно: у документа нет ничего."""

    format_name: str = ""
    has_video: bool = False
    has_audio: bool = False
    video_codec: str | None = None
    audio_codec: str | None = None
    width: int | None = None
    height: int | None = None
    duration: float | None = None
    title: str | None = None
    performer: str | None = None
    # Кадров больше одного — это видео; один кадр (обложка mp3, png) — картинка.
    frames_hint: int | None = None
    tags: dict[str, str] = field(default_factory=dict)

    @property
    def duration_sec(self) -> int | None:
        if self.duration is None:
            return None
        return max(0, int(round(self.duration)))


def _rotation(stream: dict) -> int:
    """Поворот кадра: у видео с iPhone ширина и высота записаны «лёжа», а
    показываются «стоя». Telegram нужны размеры такими, какими их видят."""
    for item in stream.get("side_data_list") or []:
        if "rotation" in item:
            with contextlib.suppress(TypeError, ValueError):
                return int(float(item["rotation"])) % 360
    tags = stream.get("tags") or {}
    with contextlib.suppress(TypeError, ValueError):
        return int(float(tags.get("rotate", 0))) % 360
    return 0


def _float(value: object) -> float | None:
    with contextlib.suppress(TypeError, ValueError):
        number = float(value)  # type: ignore[arg-type]
        if number >= 0 and number != float("inf"):
            return number
    return None


def parse_probe(raw: dict) -> ProbeResult:
    """Разбор JSON ffprobe отдельно от запуска — чтобы проверять без файлов."""
    result = ProbeResult(format_name=str((raw.get("format") or {}).get("format_name") or ""))
    fmt = raw.get("format") or {}
    result.duration = _float(fmt.get("duration"))
    tags = {str(k).lower(): str(v) for k, v in (fmt.get("tags") or {}).items()}
    result.tags = tags
    result.title = tags.get("title") or None
    result.performer = tags.get("artist") or tags.get("author") or tags.get("album_artist")

    for stream in raw.get("streams") or []:
        codec_type = stream.get("codec_type")
        disposition = stream.get("disposition") or {}
        if codec_type == "video":
            # Обложка альбома в mp3 — это «видеопоток» из одной картинки.
            if disposition.get("attached_pic"):
                continue
            if result.has_video:
                continue
            result.has_video = True
            result.video_codec = stream.get("codec_name")
            width, height = stream.get("width"), stream.get("height")
            if isinstance(width, int) and isinstance(height, int) and width > 0 and height > 0:
                if _rotation(stream) in (90, 270):
                    width, height = height, width
                result.width, result.height = width, height
            frames = stream.get("nb_frames")
            with contextlib.suppress(TypeError, ValueError):
                result.frames_hint = int(frames)
            if result.duration is None:
                result.duration = _float(stream.get("duration"))
        elif codec_type == "audio" and not result.has_audio:
            result.has_audio = True
            result.audio_codec = stream.get("codec_name")
            if result.duration is None:
                result.duration = _float(stream.get("duration"))
    return result


async def probe(path: str) -> ProbeResult | None:
    """Разобрать файл. None — ffprobe файла не понял (обычный документ)."""
    try:
        out = await _run(
            [
                "ffprobe",
                "-v",
                "error",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                path,
            ],
            PROBE_TIMEOUT,
            stdout=True,
        )
    except MediaError:
        return None
    try:
        return parse_probe(json.loads(out or b"{}"))
    except (ValueError, TypeError):
        return None


# ------------------------------------------------------------------- голосовое


async def transcode_voice(src: str, dst: str) -> None:
    """Голосовое в формат Telegram: Ogg/Opus, моно, 48 кГц, 32 кбит/с.

    Метаданные вычищаются: Telegram отличает голосовое от музыки в том числе
    по тегам — Ogg с «названием трека» он может показать как аудиофайл.
    """
    await _run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-y",
            "-i",
            src,
            "-map",
            "0:a:0",
            "-vn",
            "-ac",
            "1",
            "-ar",
            "48000",
            "-c:a",
            "libopus",
            "-b:a",
            "32k",
            "-vbr",
            "on",
            "-application",
            "voip",
            "-map_metadata",
            "-1",
            "-f",
            "ogg",
            dst,
        ],
        TRANSCODE_TIMEOUT,
    )


def waveform_from_pcm(pcm: bytes, points: int = WAVEFORM_POINTS) -> list[int]:
    """Пики громкости по корзинам → значения 0..31, как рисует Telegram.

    Нормируем на самый громкий пик записи: тихое голосовое не должно
    выглядеть ровной линией, а громкое — сплошной стеной.
    """
    samples = array("h")
    samples.frombytes(pcm[: len(pcm) - len(pcm) % 2])
    if not samples:
        return [0] * points
    total = len(samples)
    peaks: list[int] = []
    for index in range(points):
        start = index * total // points
        end = max(start + 1, (index + 1) * total // points)
        chunk = samples[start:end]
        peaks.append(max(max(chunk), -min(chunk)) if chunk else 0)
    loudest = max(peaks) or 1
    return [min(WAVEFORM_MAX, round(peak * WAVEFORM_MAX / loudest)) for peak in peaks]


async def waveform(path: str, points: int = WAVEFORM_POINTS) -> list[int]:
    """Волна для любого аудиофайла."""
    pcm = await _run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-i",
            path,
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(_WAVEFORM_RATE),
            "-f",
            "s16le",
            "-",
        ],
        TRANSCODE_TIMEOUT,
        stdout=True,
    )
    return waveform_from_pcm(pcm, points)


# ------------------------------------------------------------------ видео


async def video_thumbnail(src: str, dst: str, duration: float | None = None) -> bool:
    """Кадр-превью JPEG ≤ 320 px. False — кадр взять не удалось (не беда:
    видео уйдёт и без превью, просто некрасиво)."""
    # Первый кадр часто чёрный (затемнение в начале ролика). Полсекунды —
    # компромисс, но у совсем короткого видео столько может не быть.
    offset = "0.5" if (duration or 0) > 1.0 else "0"
    scale = (
        f"scale='if(gt(iw,ih),{THUMB_SIDE},-2)':'if(gt(iw,ih),-2,{THUMB_SIDE})'"
    )
    try:
        await _run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-nostdin",
                "-y",
                "-ss",
                offset,
                "-i",
                src,
                "-frames:v",
                "1",
                "-vf",
                scale,
                "-q:v",
                "5",
                "-f",
                "image2",
                dst,
            ],
            PROBE_TIMEOUT,
        )
    except MediaError:
        return False
    return file_size(dst) > 0


# ------------------------------------------------------------------ Safari


async def to_m4a(src: str, dst: str) -> None:
    """Аудио в AAC (m4a) — формат, который Safari играет на любой версии."""
    await _run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-y",
            "-i",
            src,
            "-vn",
            "-c:a",
            "aac",
            "-b:a",
            "64k",
            "-movflags",
            "+faststart",
            "-f",
            "ipod",
            dst,
        ],
        TRANSCODE_TIMEOUT,
    )


# ------------------------------------------------------------- картинки


def image_size(path: str) -> tuple[int, int] | None:
    """Размер картинки с учётом поворота из EXIF.

    Фото с телефона хранится «лёжа» с пометкой «повернуть». Браузер при показе
    её учитывает, и если отдать ему размеры без поворота, место под картинку
    в ленте окажется не той формы — лента дёрнется при загрузке.
    """
    try:
        from PIL import Image
    except ImportError:  # pragma: no cover — Pillow есть в зависимостях
        return None
    try:
        # Размер читается из заголовка, картинка целиком не раскодируется —
        # 12-мегапиксельное фото не должно стоить сотни миллисекунд.
        with Image.open(path) as image:
            width, height = int(image.width), int(image.height)
            orientation = image.getexif().get(0x0112)
    except Exception:  # noqa: BLE001 — не картинка или формат без поддержки (HEIC)
        return None
    # Ориентации 5–8 — поворот на 90°: ширина и высота меняются местами.
    if orientation in (5, 6, 7, 8):
        width, height = height, width
    return width, height


def extension(name: str) -> str:
    return PurePosixPath(name or "").suffix.lower().lstrip(".")


def file_size(path: str) -> int:
    """Размер локального файла; 0 — файла нет. Обычный stat — мгновенный."""
    try:
        return os.path.getsize(path)
    except OSError:
        return 0
