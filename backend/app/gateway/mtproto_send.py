"""Исполнение плана отправки на MTProto (Telethon).

Почему не `client.send_file(список)`: альбом Telethon собирает без наших
метаданных — видео уходило бы клиенту квадратиком 1×1 с «0:00» и без
кадра-превью. Здесь каждый файл загружается с правильными атрибутами, а
альбом отправляется одним запросом `messages.sendMultiMedia`.

**Повтор без дублей.** У каждой отправки свой `random_id`, выведенный из
`random_id` сообщения CRM и номера шага. Если ответ Telegram потерялся или
шлюз перезапустился посреди отправки, очередь повторит её — и Telegram
ответит RANDOM_ID_DUPLICATE вместо второго такого же сообщения у клиента.
Уже ушедший шаг просто пропускается.

Используются только публичные запросы Telegram и публичные помощники Telethon —
от внутренностей библиотеки, которые меняются между версиями, код не зависит.
"""

import hashlib
import logging
import os
import shutil
import tempfile
from collections.abc import Awaitable, Callable
from typing import Any

from telethon import TelegramClient, functions, types, utils
from telethon.errors import (
    ImageProcessFailedError,
    MediaCaptionTooLongError,
    MediaInvalidError,
    PhotoExtInvalidError,
    PhotoInvalidDimensionsError,
    PhotoInvalidError,
    PhotoSaveFileInvalidError,
    RandomIdDuplicateError,
)

from app.gateway.send_plan import MediaStep, OutgoingFile, SendPlan, Step, TextStep

log = logging.getLogger("astra.mtproto.send")

# Ошибки, после которых фото стоит отправить документом: Telegram не принял
# картинку как фото (размер, пропорции, формат), но файл-то целый.
PHOTO_REJECTED = (
    PhotoInvalidDimensionsError,
    PhotoSaveFileInvalidError,
    PhotoExtInvalidError,
    ImageProcessFailedError,
    PhotoInvalidError,
)

# Фото больше этого Telegram не принимает: уменьшаем сами, как Telegram Desktop.
PHOTO_MAX_SIDE = 2560
PHOTO_MAX_BYTES = 10 * 1000 * 1000


def local_size(path: str) -> int:
    """Размер скачанного файла; 0 — файла нет."""
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def step_random_id(base: int, step: int, item: int = 0) -> int:
    """Постоянный random_id шага: тот же при каждом повторе того же сообщения.

    `long` в Telegram — знаковое 64-битное, поэтому переводим в этот диапазон.
    """
    digest = hashlib.blake2b(f"{base}:{step}:{item}".encode(), digest_size=8).digest()
    return int.from_bytes(digest, "big", signed=True)


def ids_from_updates(result: Any, random_ids: list[int]) -> list[int | None]:
    """Номера новых сообщений по нашим random_id — в том же порядке.

    Telegram возвращает соответствие `random_id → id` отдельным обновлением
    UpdateMessageID; по нему, а не по порядку, чтобы не перепутать элементы альбома.
    """
    if isinstance(result, types.UpdateShortSentMessage):
        return [result.id] if len(random_ids) == 1 else [None] * len(random_ids)
    mapping: dict[int, int] = {}
    new_ids: list[int] = []
    for update in getattr(result, "updates", None) or []:
        if isinstance(update, types.UpdateMessageID):
            mapping[update.random_id] = update.id
        elif isinstance(update, (types.UpdateNewMessage, types.UpdateNewChannelMessage)):
            new_ids.append(update.message.id)
    ids = [mapping.get(rid) for rid in random_ids]
    # Запасной путь: без UpdateMessageID (так бывает у пересылки) — по порядку
    # появления новых сообщений, их Telegram отдаёт в порядке отправки.
    if any(value is None for value in ids) and len(new_ids) == len(random_ids):
        ids = sorted(new_ids)
    return ids


# ------------------------------------------------------------- подготовка


def _resized_photo(path: str, workdir: str) -> str:
    """Фото, которое Telegram примет: ≤ 2560 px и ≤ 10 МБ, JPEG.

    Без этого фото с современной камеры (4000+ px) или скриншот 5K отклонялись
    Telegram, сообщение трижды падало и помечалось ошибкой.
    """
    try:
        from PIL import Image, ImageOps
    except ImportError:  # pragma: no cover
        return path
    try:
        size = os.path.getsize(path)
        with Image.open(path) as image:
            # Поворот из EXIF применяем сами: иначе фото с телефона может
            # прийти клиенту «лёжа».
            orientation = image.getexif().get(0x0112)
            if (
                image.width <= PHOTO_MAX_SIDE
                and image.height <= PHOTO_MAX_SIDE
                and size <= PHOTO_MAX_BYTES
                and image.mode == "RGB"
                and (image.format or "").upper() in ("JPEG", "PNG")
                and orientation in (None, 1)
            ):
                return path
            image = ImageOps.exif_transpose(image) or image
            image.thumbnail((PHOTO_MAX_SIDE, PHOTO_MAX_SIDE))
            if image.mode != "RGB":
                background = Image.new("RGB", image.size, (255, 255, 255))
                alpha = image.convert("RGBA").getchannel("A")
                background.paste(image.convert("RGB"), mask=alpha)
                image = background
            target = os.path.join(workdir, "photo.jpg")
            image.save(target, "JPEG", quality=87, progressive=True)
            return target
    except Exception:  # noqa: BLE001 — не смогли уменьшить: пусть решает Telegram
        log.info("Не удалось подготовить фото %s, отправляю как есть", path)
        return path


def _attributes(item: OutgoingFile, *, voice: bool, as_document: bool) -> list[Any]:
    attributes: list[Any] = [types.DocumentAttributeFilename(item.file_name)]
    if voice:
        attributes.append(
            types.DocumentAttributeAudio(
                duration=int(item.duration_sec or 0),
                voice=True,
                waveform=utils.encode_waveform(bytes(item.waveform)) if item.waveform else None,
            )
        )
    elif item.kind == "audio":
        attributes.append(
            types.DocumentAttributeAudio(
                duration=int(item.duration_sec or 0),
                title=item.title,
                performer=item.performer,
            )
        )
    elif item.as_video and not as_document:
        attributes.append(
            types.DocumentAttributeVideo(
                duration=float(item.duration_sec or 0),
                w=int(item.width or 1),
                h=int(item.height or 1),
                round_message=item.kind == "video_note",
                supports_streaming=True,
            )
        )
    return attributes


async def _input_media(
    client: TelegramClient,
    item: OutgoingFile,
    *,
    voice: bool,
    as_document: bool,
    workdir: str,
) -> Any:
    """Загрузить файл в Telegram и собрать для него InputMedia."""
    if item.as_photo and not as_document:
        uploaded = await client.upload_file(_resized_photo(item.path, workdir))
        return types.InputMediaUploadedPhoto(file=uploaded)

    uploaded = await client.upload_file(item.path, file_name=item.file_name)
    thumb = None
    if item.thumb_path and item.as_video and not as_document:
        thumb = await client.upload_file(item.thumb_path)
    mime = item.mime_type or "application/octet-stream"
    if voice:
        mime = "audio/ogg"
    return types.InputMediaUploadedDocument(
        file=uploaded,
        mime_type=mime,
        attributes=_attributes(item, voice=voice, as_document=as_document),
        thumb=thumb,
        # Документ «как есть»: иначе Telegram может показать картинку-документ
        # превью-фотографией или видео — плеером, вопреки нашему решению.
        force_file=as_document or None,
        nosound_video=True if item.as_video and not as_document else None,
    )


# ---------------------------------------------------------------- отправка


SendResult = list[int | None]


async def _send_text(client: TelegramClient, peer: Any, text: str, random_id: int) -> SendResult:
    # Без разметки: в CRM текст показывается как написан, и клиент должен
    # увидеть то же самое, а не «**жирный**», превращённый в жирный шрифт.
    result = await client(
        functions.messages.SendMessageRequest(peer=peer, message=text, random_id=random_id)
    )
    return ids_from_updates(result, [random_id])


async def _send_single(
    client: TelegramClient,
    peer: Any,
    item: OutgoingFile,
    caption: str | None,
    random_id: int,
    *,
    voice: bool,
    workdir: str,
) -> SendResult:
    as_document = item.group == "document" or item.kind in ("sticker",)
    try:
        media = await _input_media(
            client, item, voice=voice, as_document=as_document, workdir=workdir
        )
        result = await client(
            functions.messages.SendMediaRequest(
                peer=peer, media=media, message=caption or "", random_id=random_id
            )
        )
    except PHOTO_REJECTED:
        if not item.as_photo:
            raise
        log.info("Telegram не принял %s как фото — отправляю документом", item.file_name)
        media = await _input_media(client, item, voice=False, as_document=True, workdir=workdir)
        result = await client(
            functions.messages.SendMediaRequest(
                peer=peer, media=media, message=caption or "", random_id=random_id
            )
        )
    return ids_from_updates(result, [random_id])


async def _send_album(
    client: TelegramClient,
    peer: Any,
    step: MediaStep,
    random_ids: list[int],
    *,
    workdir: str,
) -> SendResult:
    as_document = step.group == "document"
    multi: list[Any] = []
    for index, item in enumerate(step.files):
        media = await _input_media(
            client, item, voice=False, as_document=as_document, workdir=workdir
        )
        # Альбом принимает только уже загруженные в Telegram медиа: «сырой»
        # InputMediaUploaded* превращаем в ссылку на готовый файл.
        stored = await client(functions.messages.UploadMediaRequest(peer=peer, media=media))
        if isinstance(stored, types.MessageMediaPhoto):
            ready = utils.get_input_media(stored.photo)
        else:
            ready = utils.get_input_media(stored.document)
        multi.append(
            types.InputSingleMedia(
                media=ready,
                random_id=random_ids[index],
                message=(step.caption or "") if index == 0 else "",
            )
        )
    result = await client(functions.messages.SendMultiMediaRequest(peer=peer, multi_media=multi))
    return ids_from_updates(result, random_ids)


async def _run_step(
    client: TelegramClient,
    peer: Any,
    step: Step,
    step_index: int,
    base_random_id: int,
    workdir: str,
) -> SendResult:
    if isinstance(step, TextStep):
        return await _send_text(client, peer, step.text, step_random_id(base_random_id, step_index))
    random_ids = [step_random_id(base_random_id, step_index, i) for i in range(len(step.files))]
    if len(step.files) == 1:
        return await _send_single(
            client,
            peer,
            step.files[0],
            step.caption,
            random_ids[0],
            voice=step.voice,
            workdir=workdir,
        )
    try:
        return await _send_album(client, peer, step, random_ids, workdir=workdir)
    except (MediaInvalidError, *PHOTO_REJECTED):
        # Один «плохой» элемент ломает весь альбом. Лучше дойти до клиента
        # по одному файлу, чем не дойти вовсе.
        log.info("Альбом не принят Telegram — отправляю файлы по одному")
        ids: SendResult = []
        for index, item in enumerate(step.files):
            ids += await _send_single(
                client,
                peer,
                item,
                step.caption if index == 0 else None,
                random_ids[index],
                voice=False,
                workdir=workdir,
            )
        return ids


# (сколько номеров нужно, какие уже получены в этой отправке) → номера
RecoverIds = Callable[[int, list[int]], Awaitable[list[int]]]


async def execute(
    client: TelegramClient,
    peer: Any,
    plan: SendPlan,
    base_random_id: int,
    recover: RecoverIds | None = None,
) -> list[int]:
    """Выполнить план. Возвращает номера всех получившихся сообщений по порядку.

    `recover(n)` — как узнать номера шага, который уже был отправлен раньше
    (Telegram ответил RANDOM_ID_DUPLICATE): обычно это последние n исходящих
    в диалоге, ещё не известных CRM.
    """
    ids: list[int] = []
    workdir = tempfile.mkdtemp(prefix="astra-send-")
    try:
        for index, step in enumerate(plan.steps):
            expected = 1 if isinstance(step, TextStep) else len(step.files)
            try:
                got = await _run_step(client, peer, step, index, base_random_id, workdir)
            except RandomIdDuplicateError:
                log.info("Шаг %s уже был отправлен раньше — пропускаю без повтора", index)
                got = list(await recover(expected, ids)) if recover else []
            except MediaCaptionTooLongError:
                # Лимит подписи у этого аккаунта меньше ожидаемого — текст
                # отдельным сообщением, файлы без подписи.
                if not isinstance(step, MediaStep) or not step.caption:
                    raise
                caption, step.caption = step.caption, None
                got = await _send_text(
                    client, peer, caption, step_random_id(base_random_id, index, 99)
                )
                got += await _run_step(client, peer, step, index, base_random_id, workdir)
            ids.extend(value for value in got if value is not None)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    return ids


async def forward(
    client: TelegramClient,
    to_peer: Any,
    from_peer: Any,
    tg_message_ids: list[int],
    *,
    drop_author: bool,
    base_random_id: int,
    recover: RecoverIds | None = None,
) -> list[int]:
    """Настоящая пересылка Telegram. `drop_author` — «скрыть отправителя»:
    клиент увидит сообщение без подписи «Переслано от …».

    Альбомы, пересланные одним запросом, остаются альбомами.
    """
    random_ids = [step_random_id(base_random_id, 0, i) for i in range(len(tg_message_ids))]
    try:
        result = await client(
            functions.messages.ForwardMessagesRequest(
                from_peer=from_peer,
                id=tg_message_ids,
                to_peer=to_peer,
                drop_author=drop_author or None,
                random_id=random_ids,
            )
        )
    except RandomIdDuplicateError:
        log.info("Пересылка уже была выполнена раньше — повтора не будет")
        return list(await recover(len(tg_message_ids), [])) if recover else []
    return [value for value in ids_from_updates(result, random_ids) if value is not None]
