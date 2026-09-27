"""Сообщение Telethon → то, что понимает CRM (`InboundMessage`).

Правило одно: **ни одного пустого пузыря.** Что бы ни прислал клиент — голосовое,
кружочек, стикер, контакт, геопозицию, опрос или то, чего CRM показывать не
умеет, — менеджер видит, что пришло, а не белое пятно.

Файлы:

- до 20 МБ скачиваются сразу — сообщение появляется уже с файлом;
- крупнее — сообщение появляется сразу с пометкой «загружается», файл
  докачивает фоновая задача шлюза (`app/gateway/backfill.py`);
- больше лимита CRM — «слишком большой, откройте в Telegram»: не молча
  отброшенный, а видимый.
"""

import logging
from datetime import UTC
from typing import Any

from telethon import TelegramClient, utils
from telethon.tl.types import (
    DocumentAttributeAudio,
    DocumentAttributeFilename,
    DocumentAttributeSticker,
    DocumentAttributeVideo,
    MessageMediaContact,
    MessageMediaDice,
    MessageMediaDocument,
    MessageMediaGeo,
    MessageMediaGeoLive,
    MessageMediaPhoto,
    MessageMediaPoll,
    MessageMediaVenue,
    MessageMediaWebPage,
)
from telethon.tl.types import (
    User as TgUser,
)

from app.core.config import settings
from app.services.inbound_service import InboundMessage, PeerData

log = logging.getLogger("astra.mtproto.receive")

# Сколько качаем сразу, не задерживая появление сообщения надолго.
SYNC_DOWNLOAD_BYTES = 20 * 1024 * 1024

# Подписи для типов, которые CRM не показывает, — чтобы менеджер понимал,
# что пришло, и знал, где это открыть.
_UNSUPPORTED_LABELS = {
    "MessageMediaGame": "Игра",
    "MessageMediaInvoice": "Счёт Telegram",
    "MessageMediaStory": "История",
    "MessageMediaGiveaway": "Розыгрыш",
    "MessageMediaGiveawayResults": "Итоги розыгрыша",
    "MessageMediaPaidMedia": "Платный контент",
    "MessageMediaToDo": "Список задач",
    "MessageMediaUnsupported": "Сообщение новой версии Telegram",
}


def decode_waveform(packed: bytes) -> bytes:
    """Волна голосового из формата Telegram: значения по 5 бит подряд.

    Своя распаковка, а не `telethon.utils.decode_waveform`: у той последний
    столбик читается не с того места (сдвиг считается от номера значения, а не
    от номера бита), и в конце волны вместо звука появлялся «провал».
    """
    count = len(packed) * 8 // 5
    padded = bytes(packed) + b"\x00"
    values = bytearray(count)
    for index in range(count):
        byte_index, shift = divmod(index * 5, 8)
        pair = padded[byte_index] | (padded[byte_index + 1] << 8)
        values[index] = (pair >> shift) & 0b11111
    return bytes(values)


def max_download_bytes() -> int:
    return max(1, settings.telegram_max_download_mb) * 1024 * 1024


def _text(value: Any) -> str:
    """В новых версиях Telegram у опросов текст с разметкой (TextWithEntities)."""
    if value is None:
        return ""
    return str(getattr(value, "text", value) or "")


def _display_name(entity: Any) -> str | None:
    if entity is None:
        return None
    name = utils.get_display_name(entity)
    return name or None


def forwarded_from(message: Any) -> dict[str, Any] | None:
    """Откуда переслано: имя человека или канала, если Telegram его показывает."""
    header = getattr(message, "fwd_from", None)
    if header is None:
        return None
    forward = getattr(message, "forward", None)
    name = (
        getattr(header, "from_name", None)
        or _display_name(getattr(forward, "sender", None))
        or _display_name(getattr(forward, "chat", None))
        or getattr(header, "post_author", None)
    )
    date = getattr(header, "date", None)
    return {"name": name, "date": date.astimezone(UTC).isoformat() if date else None}


def describe_media(message: Any) -> tuple[str | None, dict[str, Any]]:
    """Нефайловое содержимое: контакт, геопозиция, опрос, кубик, неизвестное.

    Возвращает текст для ленты и списка чатов (попадёт и в поиск) и то, что
    нужно интерфейсу для карточки.
    """
    media = getattr(message, "media", None)
    if isinstance(media, MessageMediaContact):
        name = " ".join(part for part in (media.first_name, media.last_name) if part).strip()
        phone = media.phone_number or ""
        text = "📇 Контакт: " + ", ".join(part for part in (name, phone) if part)
        return text, {
            "contact": {
                "first_name": media.first_name or None,
                "last_name": media.last_name or None,
                "phone": phone or None,
                "tg_user_id": int(media.user_id) if media.user_id else None,
            }
        }
    if isinstance(media, MessageMediaVenue):
        geo = media.geo
        title = ", ".join(part for part in (media.title, media.address) if part)
        return f"📍 {title or 'Место'}", {
            "location": {
                "lat": float(getattr(geo, "lat", 0.0)),
                "lon": float(getattr(geo, "long", 0.0)),
                "title": media.title or None,
                "address": media.address or None,
            }
        }
    if isinstance(media, (MessageMediaGeo, MessageMediaGeoLive)):
        geo = media.geo
        lat, lon = float(getattr(geo, "lat", 0.0)), float(getattr(geo, "long", 0.0))
        label = "Трансляция геопозиции" if isinstance(media, MessageMediaGeoLive) else "Геопозиция"
        return f"📍 {label}: {lat:.5f}, {lon:.5f}", {
            "location": {"lat": lat, "lon": lon, "title": None, "address": None}
        }
    if isinstance(media, MessageMediaPoll):
        question = _text(media.poll.question)
        options = [_text(answer.text) for answer in (media.poll.answers or [])]
        return f"📊 Опрос: {question}", {"poll": {"question": question, "options": options}}
    if isinstance(media, MessageMediaDice):
        return f"{media.emoticon} {media.value}", {}
    if media is not None and not isinstance(
        media, (MessageMediaPhoto, MessageMediaDocument, MessageMediaWebPage)
    ):
        label = _UNSUPPORTED_LABELS.get(type(media).__name__, "Сообщение")
        return f"{label} — CRM такое не показывает, откройте в Telegram", {"unsupported": label}
    return None, {}


def _attribute(document: Any, cls: type) -> Any:
    for attribute in getattr(document, "attributes", None) or []:
        if isinstance(attribute, cls):
            return attribute
    return None


def media_kind(message: Any) -> str | None:
    """Вид вложения по признакам Telegram, от частного к общему."""
    if not isinstance(getattr(message, "media", None), (MessageMediaPhoto, MessageMediaDocument)):
        return None
    if message.photo:
        return "photo"
    if message.voice:
        return "voice"
    if message.video_note:
        return "video_note"
    if message.sticker:
        return "sticker"
    if message.gif:
        return "animation"
    if message.video:
        return "video"
    if message.audio:
        return "audio"
    return "document"


def _file_meta(message: Any, kind: str) -> dict[str, Any]:
    """Всё о файле, кроме самого файла: имя, тип, размеры, длительность, волна."""
    file = getattr(message, "file", None)
    document = getattr(message, "document", None)
    ext = getattr(file, "ext", None) or ""
    item: dict[str, Any] = {
        "kind": kind,
        "file_name": getattr(file, "name", None) or f"{kind}-{message.id}{ext}",
        "mime_type": getattr(file, "mime_type", None),
        "size": int(getattr(file, "size", 0) or 0),
    }
    filename_attr = _attribute(document, DocumentAttributeFilename)
    if filename_attr is not None and filename_attr.file_name:
        item["file_name"] = filename_attr.file_name
    video = _attribute(document, DocumentAttributeVideo)
    if video is not None:
        item["duration_sec"] = int(round(float(video.duration or 0)))
        item["width"], item["height"] = video.w, video.h
    audio = _attribute(document, DocumentAttributeAudio)
    if audio is not None:
        item["duration_sec"] = int(audio.duration or 0)
        if audio.voice and audio.waveform:
            item["waveform"] = decode_waveform(audio.waveform)
        if audio.title:
            item["title"] = audio.title
        if audio.performer:
            item["performer"] = audio.performer
    sticker = _attribute(document, DocumentAttributeSticker)
    if sticker is not None and sticker.alt:
        item["emoji"] = sticker.alt
    if message.photo:
        item["width"] = getattr(file, "width", None)
        item["height"] = getattr(file, "height", None)
        item["mime_type"] = item["mime_type"] or "image/jpeg"
    return item


async def read_media(
    client: TelegramClient, message: Any, account_id: int
) -> tuple[str | None, list[dict[str, Any]]]:
    """Вид сообщения и вложения. Большие файлы — на фоновую докачку."""
    kind = media_kind(message)
    if kind is None:
        return None, []
    item = _file_meta(message, kind)
    source = {
        "account_id": account_id,
        "chat_id": int(message.chat_id) if message.chat_id is not None else None,
        "tg_message_id": int(message.id),
    }
    size = item["size"]
    if size > max_download_bytes():
        log.info("Файл %s байт больше лимита CRM — только упоминание", size)
        item["status"] = "too_large"
        return kind, [item]
    if size > SYNC_DOWNLOAD_BYTES:
        item["status"] = "pending"
        item["source"] = source
        return kind, [item]
    try:
        body = await client.download_media(message, file=bytes)
    except Exception:
        # Не скачалось сейчас (сеть, ссылка устарела) — фоновая докачка
        # попробует ещё, а сообщение уже на месте и видно менеджеру.
        log.exception("Не скачал вложение сообщения %s — поставлю на докачку", message.id)
        item["status"] = "pending"
        item["source"] = source
        return kind, [item]
    if not body:
        item["status"] = "pending"
        item["source"] = source
        return kind, [item]
    item["body"] = body
    item["size"] = len(body)
    item["status"] = "ready"
    return kind, [item]


async def to_inbound(
    client: TelegramClient, message: Any, account_id: int, *, live: bool, is_edit: bool = False
) -> InboundMessage | None:
    """Событие Telethon → то, что понимает CRM.

    Собеседника берём из диалога, а не из отправителя: у исходящего сообщения
    отправитель — мы сами, и по нему завелась бы карточка «клиента» с нашим же
    номером. Диалог указывает на человека одинаково в обе стороны.

    Группы и каналы пропускаем: продукт про личную переписку, а групповой чат
    сломал бы правило «диалог = клиент + аккаунт».
    """
    partner = await message.get_chat()
    if not isinstance(partner, TgUser) or partner.is_self or partner.bot:
        return None
    peer = PeerData(
        tg_user_id=int(partner.id),
        access_hash=int(partner.access_hash) if partner.access_hash else None,
        username=partner.username,
        phone=partner.phone,
        first_name=partner.first_name,
        last_name=partner.last_name,
        is_bot=bool(partner.bot),
    )
    # Правка не несёт новых файлов: скачивать их второй раз незачем.
    kind, attachments = (None, []) if is_edit else await read_media(client, message, account_id)
    text = message.message or None
    described, meta = describe_media(message)
    if described and not text:
        text = described
    forwarded = forwarded_from(message)
    if forwarded:
        meta["forwarded_from"] = forwarded
    edit_date = getattr(message, "edit_date", None)
    return InboundMessage(
        peer=peer,
        tg_message_id=int(message.id),
        date=message.date.astimezone(UTC),
        text=text,
        outgoing=bool(message.out),
        random_id=None,
        reply_to_tg_id=int(message.reply_to_msg_id) if message.reply_to_msg_id else None,
        media_kind=kind,
        live=live,
        attachments=attachments,
        meta=meta or None,
        is_edit=is_edit,
        edit_date=edit_date.astimezone(UTC) if edit_date else None,
    )
