"""Как одно сообщение CRM превращается в отправки Telegram.

Сообщение в CRM — это текст плюс сколько угодно файлов. В Telegram так нельзя:

- голосовое — всегда отдельное сообщение и не бывает в альбоме;
- в альбоме до 10 элементов, и смешивать можно только фото с видео;
  аудио — отдельным альбомом, документы — отдельным;
- подпись к файлу — не длиннее 1024 символов (у обычного аккаунта), текст
  сообщения — не длиннее 4096.

Здесь только решение «что за чем отправлять» — чистая функция без Telegram,
поэтому она проверяется тестами целиком, а демо-шлюз и боевой шлюз разбивают
сообщение одинаково.
"""

from dataclasses import dataclass, field
from typing import Literal

from app.services.media import extension

# Лимиты Telegram считаются в единицах UTF-16: эмодзи — это две единицы.
CAPTION_LIMIT = 1024
TEXT_LIMIT = 4096
ALBUM_LIMIT = 10

# Фото Telegram принимает только в этих форматах; остальные картинки уходят
# документом (HEIC, WebP, TIFF, BMP) — клиент получит файл, а не ошибку.
PHOTO_EXTENSIONS = frozenset({"jpg", "jpeg", "png"})
# Видео, которое клиенты Telegram показывают плеером. Остальное — документ.
VIDEO_EXTENSIONS = frozenset({"mp4", "mov", "m4v"})

Group = Literal["visual", "audio", "document"]


@dataclass(slots=True)
class OutgoingFile:
    """Файл, готовый к отправке: уже скачан из хранилища во временный путь."""

    path: str
    file_name: str
    mime_type: str | None
    kind: str
    width: int | None = None
    height: int | None = None
    duration_sec: int | None = None
    waveform: bytes | None = None
    thumb_path: str | None = None
    title: str | None = None
    performer: str | None = None

    @property
    def ext(self) -> str:
        return extension(self.file_name)

    @property
    def as_photo(self) -> bool:
        return self.kind == "photo" and self.ext in PHOTO_EXTENSIONS

    @property
    def as_video(self) -> bool:
        return self.kind in ("video", "video_note") and self.ext in VIDEO_EXTENSIONS

    @property
    def group(self) -> Group | None:
        """None — файл уходит только поодиночке (голосовое, кружочек, GIF, стикер)."""
        if self.kind in ("voice", "video_note", "animation", "sticker"):
            return None
        if self.as_photo or self.as_video:
            return "visual"
        if self.kind == "audio":
            return "audio"
        return "document"


@dataclass(slots=True)
class TextStep:
    text: str


@dataclass(slots=True)
class MediaStep:
    """Один файл или альбом. У альбома подпись — у первого элемента."""

    files: list[OutgoingFile]
    caption: str | None = None
    group: Group | None = None
    voice: bool = False


Step = TextStep | MediaStep


@dataclass(slots=True)
class SendPlan:
    steps: list[Step] = field(default_factory=list)

    @property
    def message_count(self) -> int:
        """Сколько сообщений Telegram получится (для демо-шлюза и проверок)."""
        total = 0
        for step in self.steps:
            total += 1 if isinstance(step, TextStep) else len(step.files)
        return total


def utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def split_text(text: str, limit: int = TEXT_LIMIT) -> list[str]:
    """Длинный текст — на части не длиннее лимита, по абзацам и пробелам.

    CRM принимает до 4096 символов, но эмодзи в Telegram весят вдвое, и такой
    текст мог оказаться длиннее его лимита — сообщение трижды падало с ошибкой.
    """
    parts: list[str] = []
    rest = text
    while utf16_len(rest) > limit:
        # Самый длинный префикс, который влезает, — затем откатываемся к переносу
        # строки или пробелу, чтобы не резать слово пополам.
        cut, units = 0, 0
        for index, char in enumerate(rest):
            weight = 2 if ord(char) > 0xFFFF else 1
            if units + weight > limit:
                break
            units += weight
            cut = index + 1
        window = rest[:cut]
        for separator in ("\n\n", "\n", " "):
            position = window.rfind(separator)
            if position > cut // 2:
                cut = position + len(separator)
                break
        parts.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip()
    if rest.strip():
        parts.append(rest)
    return parts or [text]


def plan(text: str | None, files: list[OutgoingFile]) -> SendPlan:
    """Разбить сообщение CRM на отправки Telegram в естественном порядке."""
    text = (text or "").strip() or None
    result = SendPlan()
    if not files:
        if text:
            result.steps.extend(TextStep(part) for part in split_text(text))
        return result

    # Подпись к файлу — только если влезает; иначе текст уходит отдельным
    # сообщением перед файлами, как это делает Telegram Desktop.
    caption: str | None = None
    if text is not None:
        if utf16_len(text) <= CAPTION_LIMIT:
            caption = text
        else:
            result.steps.extend(TextStep(part) for part in split_text(text))

    # Группы — в порядке первого появления: что менеджер приложил первым,
    # то клиент и увидит первым.
    order: list[Group | None] = []
    grouped: dict[Group, list[OutgoingFile]] = {}
    singles: list[tuple[int, OutgoingFile]] = []
    for index, item in enumerate(files):
        group = item.group
        if group is None:
            singles.append((index, item))
            order.append(None)
            continue
        if group not in grouped:
            grouped[group] = []
            order.append(group)
        grouped[group].append(item)

    single_iter = iter(singles)
    for group in order:
        if group is None:
            _, item = next(single_iter)
            result.steps.append(MediaStep(files=[item], voice=item.kind == "voice"))
            continue
        items = grouped[group]
        for start in range(0, len(items), ALBUM_LIMIT):
            result.steps.append(MediaStep(files=items[start : start + ALBUM_LIMIT], group=group))

    if caption is not None:
        first_media = next(step for step in result.steps if isinstance(step, MediaStep))
        first_media.caption = caption
    return result
