"""Разбор входящих Telegram: ни одного пустого пузыря.

Сообщения собираются из настоящих типов Telethon — те же объекты, что приходят
от Telegram, только без сети.
"""

import asyncio
from datetime import UTC, datetime

from telethon import types, utils

from app.gateway import mtproto_receive

NOW = datetime(2026, 9, 27, 10, 0, tzinfo=UTC)


class _EntityCache:
    def get(self, _key):  # noqa: ANN001
        return None


class _StubClient:
    """То немногое, что Telethon спрашивает у клиента, собирая сообщение."""

    _self_id = 1
    _mb_entity_cache = _EntityCache()


def _message(media=None, text: str = "", fwd_from=None, out: bool = False, msg_id: int = 10):  # noqa: ANN001
    message = types.Message(
        id=msg_id,
        peer_id=types.PeerUser(555),
        date=NOW,
        message=text,
        media=media,
        fwd_from=fwd_from,
        out=out,
    )
    message._finish_init(_StubClient(), {}, None)
    return message


def _document(mime: str, attributes: list, size: int = 1000):  # noqa: ANN001
    return types.MessageMediaDocument(
        document=types.Document(
            id=1,
            access_hash=2,
            file_reference=b"",
            date=NOW,
            mime_type=mime,
            size=size,
            dc_id=2,
            attributes=attributes,
        )
    )


def test_voice_with_waveform_and_duration():
    wave = [3, 9, 31, 0, 17] * 20
    media = _document(
        "audio/ogg",
        [
            types.DocumentAttributeAudio(
                duration=9, voice=True, waveform=utils.encode_waveform(bytes(wave))
            )
        ],
    )
    message = _message(media)
    assert mtproto_receive.media_kind(message) == "voice"
    item = mtproto_receive._file_meta(message, "voice")
    assert item["duration_sec"] == 9
    assert list(item["waveform"])[:100] == wave


def test_video_note_gif_sticker_audio_are_told_apart():
    round_video = _document(
        "video/mp4", [types.DocumentAttributeVideo(duration=5, w=384, h=384, round_message=True)]
    )
    gif = _document(
        "video/mp4",
        [types.DocumentAttributeVideo(duration=2, w=320, h=240), types.DocumentAttributeAnimated()],
    )
    sticker = _document(
        "image/webp",
        [
            types.DocumentAttributeSticker(alt="😂", stickerset=types.InputStickerSetEmpty()),
            types.DocumentAttributeImageSize(w=512, h=512),
        ],
    )
    song = _document(
        "audio/mpeg",
        [
            types.DocumentAttributeAudio(duration=180, title="Луна", performer="Хор"),
            types.DocumentAttributeFilename("luna.mp3"),
        ],
    )
    assert mtproto_receive.media_kind(_message(round_video)) == "video_note"
    assert mtproto_receive.media_kind(_message(gif)) == "animation"
    assert mtproto_receive.media_kind(_message(sticker)) == "sticker"
    assert mtproto_receive.media_kind(_message(song)) == "audio"
    sticker_meta = mtproto_receive._file_meta(_message(sticker), "sticker")
    assert sticker_meta["emoji"] == "😂"
    song_meta = mtproto_receive._file_meta(_message(song), "audio")
    assert (song_meta["title"], song_meta["performer"], song_meta["file_name"]) == (
        "Луна",
        "Хор",
        "luna.mp3",
    )


def test_contact_becomes_readable_text_and_card():
    media = types.MessageMediaContact(
        phone_number="+79990001122", first_name="Анна", last_name="Ли", vcard="", user_id=42
    )
    text, meta = mtproto_receive.describe_media(_message(media))
    assert text == "📇 Контакт: Анна Ли, +79990001122"
    assert meta["contact"]["tg_user_id"] == 42


def test_location_and_venue():
    geo = types.GeoPoint(long=37.6173, lat=55.7558, access_hash=0)
    text, meta = mtproto_receive.describe_media(_message(types.MessageMediaGeo(geo=geo)))
    assert text.startswith("📍 Геопозиция: 55.75580, 37.61730")
    assert meta["location"] == {"lat": 55.7558, "lon": 37.6173, "title": None, "address": None}
    venue = types.MessageMediaVenue(
        geo=geo, title="Кафе", address="Тверская, 1", provider="", venue_id="", venue_type=""
    )
    text, meta = mtproto_receive.describe_media(_message(venue))
    assert text == "📍 Кафе, Тверская, 1"


def test_poll_text_and_options():
    poll = types.Poll(
        id=1,
        question=types.TextWithEntities(text="Когда удобно?", entities=[]),
        answers=[
            types.PollAnswer(text=types.TextWithEntities(text="Утром", entities=[]), option=b"1"),
            types.PollAnswer(text=types.TextWithEntities(text="Вечером", entities=[]), option=b"2"),
        ],
        hash=0,
    )
    media = types.MessageMediaPoll(poll=poll, results=types.PollResults())
    text, meta = mtproto_receive.describe_media(_message(media))
    assert text == "📊 Опрос: Когда удобно?"
    assert meta["poll"]["options"] == ["Утром", "Вечером"]


def test_dice_and_unsupported_never_empty():
    text, _ = mtproto_receive.describe_media(
        _message(types.MessageMediaDice(value=5, emoticon="🎲"))
    )
    assert text == "🎲 5"
    text, meta = mtproto_receive.describe_media(_message(types.MessageMediaUnsupported()))
    assert "откройте в Telegram" in text
    assert meta["unsupported"]


def test_forwarded_from_hidden_user_uses_name():
    header = types.MessageFwdHeader(date=NOW, from_name="Мария")
    info = mtproto_receive.forwarded_from(_message(text="смотри", fwd_from=header))
    assert info == {"name": "Мария", "date": NOW.isoformat()}


def test_webpage_preview_is_plain_text_not_a_file():
    media = types.MessageMediaWebPage(webpage=types.WebPageEmpty(id=1))
    message = _message(media, text="https://example.com")
    assert mtproto_receive.media_kind(message) is None
    assert mtproto_receive.describe_media(message) == (None, {})


class _DownloadingClient:
    def __init__(self, body: bytes | Exception) -> None:
        self.body = body
        self.calls = 0

    async def download_media(self, message, file=None):  # noqa: ANN001
        self.calls += 1
        if isinstance(self.body, Exception):
            raise self.body
        return self.body


def test_small_file_is_downloaded_right_away():
    photo = types.MessageMediaDocument(
        document=types.Document(
            id=1,
            access_hash=2,
            file_reference=b"",
            date=NOW,
            mime_type="application/pdf",
            size=1000,
            dc_id=2,
            attributes=[types.DocumentAttributeFilename("a.pdf")],
        )
    )
    client = _DownloadingClient(b"%PDF-1.4")
    kind, items = asyncio.run(mtproto_receive.read_media(client, _message(photo), account_id=7))
    assert kind == "document"
    assert items[0]["status"] == "ready" and items[0]["body"] == b"%PDF-1.4"


def test_big_file_is_deferred_and_huge_file_is_only_mentioned():
    big = _document(
        "video/mp4",
        [types.DocumentAttributeVideo(duration=60, w=1920, h=1080)],
        size=mtproto_receive.SYNC_DOWNLOAD_BYTES + 1,
    )
    client = _DownloadingClient(b"")
    kind, items = asyncio.run(mtproto_receive.read_media(client, _message(big), account_id=7))
    assert kind == "video" and items[0]["status"] == "pending"
    assert items[0]["source"] == {"account_id": 7, "chat_id": 555, "tg_message_id": 10}
    assert client.calls == 0

    huge = _document("video/mp4", [], size=mtproto_receive.max_download_bytes() + 1)
    kind, items = asyncio.run(mtproto_receive.read_media(client, _message(huge), account_id=7))
    assert items[0]["status"] == "too_large"
    assert client.calls == 0


def test_failed_download_becomes_pending_not_lost():
    doc = _document("application/pdf", [types.DocumentAttributeFilename("a.pdf")])
    client = _DownloadingClient(RuntimeError("сеть"))
    _, items = asyncio.run(mtproto_receive.read_media(client, _message(doc), account_id=7))
    assert items[0]["status"] == "pending"
    assert "body" not in items[0]


def test_decode_waveform_is_exact_for_every_value():
    for values in ([31] * 100, list(range(32)) * 3, [1, 2, 3]):
        packed = utils.encode_waveform(bytes(values))
        assert list(mtproto_receive.decode_waveform(packed))[: len(values)] == values
