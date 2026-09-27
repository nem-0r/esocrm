"""Отправка в Telegram: какие запросы уходят, с какими атрибутами, что при ошибках.

Клиент Telethon подменён записывающим: он отвечает так же, как Telegram
(UpdateMessageID на каждый random_id), и умеет «падать» нужной ошибкой.
Проверяется ровно то, что уйдёт на сервер Telegram, — без сети.
"""

import asyncio
import os
import tempfile
from datetime import UTC, datetime

import pytest
from PIL import Image
from telethon import functions, types
from telethon.errors import (
    MediaCaptionTooLongError,
    MediaInvalidError,
    PhotoInvalidDimensionsError,
    RandomIdDuplicateError,
)

from app.gateway import mtproto_send
from app.gateway.mtproto_receive import decode_waveform
from app.gateway.send_plan import OutgoingFile, plan

NOW = datetime(2026, 9, 27, tzinfo=UTC)


class FakeClient:
    def __init__(self, fail: dict[type, list[Exception]] | None = None) -> None:
        self.requests: list = []
        self.uploads: list[tuple[str, str | None]] = []
        self._next = 1000
        self._fail = fail or {}

    def _id(self) -> int:
        self._next += 1
        return self._next

    async def upload_file(self, path, file_name=None):  # noqa: ANN001
        self.uploads.append((str(path), file_name))
        return types.InputFile(
            id=len(self.uploads),
            parts=1,
            name=file_name or os.path.basename(str(path)),
            md5_checksum="",
        )

    def _updates(self, random_ids: list[int]) -> types.Updates:
        updates = []
        for rid in random_ids:
            mid = self._id()
            updates.append(types.UpdateMessageID(id=mid, random_id=rid))
            updates.append(
                types.UpdateNewMessage(
                    message=types.Message(id=mid, peer_id=types.PeerUser(1), date=NOW, message=""),
                    pts=1,
                    pts_count=1,
                )
            )
        return types.Updates(updates=updates, users=[], chats=[], date=NOW, seq=0)

    async def __call__(self, request):  # noqa: ANN001
        self.requests.append(request)
        queue = self._fail.get(type(request))
        if queue:
            raise queue.pop(0)
        if isinstance(request, functions.messages.SendMessageRequest):
            return types.UpdateShortSentMessage(
                id=self._id(), pts=1, pts_count=1, date=NOW, out=True
            )
        if isinstance(request, functions.messages.SendMediaRequest):
            return self._updates([request.random_id])
        if isinstance(request, functions.messages.UploadMediaRequest):
            if isinstance(request.media, types.InputMediaUploadedPhoto):
                return types.MessageMediaPhoto(
                    photo=types.Photo(
                        id=self._id(),
                        access_hash=1,
                        file_reference=b"",
                        date=NOW,
                        sizes=[],
                        dc_id=2,
                    )
                )
            return types.MessageMediaDocument(
                document=types.Document(
                    id=self._id(),
                    access_hash=1,
                    file_reference=b"",
                    date=NOW,
                    mime_type=request.media.mime_type,
                    size=1,
                    dc_id=2,
                    attributes=request.media.attributes,
                )
            )
        if isinstance(request, functions.messages.SendMultiMediaRequest):
            return self._updates([item.random_id for item in request.multi_media])
        if isinstance(request, functions.messages.ForwardMessagesRequest):
            return self._updates(list(request.random_id))
        raise AssertionError(f"неожиданный запрос {type(request).__name__}")

    def of(self, cls: type) -> list:
        return [r for r in self.requests if isinstance(r, cls)]


@pytest.fixture
def workdir():
    with tempfile.TemporaryDirectory() as tmp:
        yield tmp


def _image(workdir: str, name: str, size=(800, 600)) -> str:
    path = os.path.join(workdir, name)
    Image.new("RGB", size, (200, 30, 30)).save(path, "JPEG" if name.endswith("jpg") else "PNG")
    return path


def _blob(workdir: str, name: str) -> str:
    path = os.path.join(workdir, name)
    with open(path, "wb") as out:
        out.write(b"x" * 100)
    return path


def run(client, text, files, base=12345, recover=None):  # noqa: ANN001
    return asyncio.run(
        mtproto_send.execute(client, types.InputPeerUser(1, 2), plan(text, files), base, recover)
    )


def test_text_is_sent_verbatim_without_markdown(workdir):
    client = FakeClient()
    ids = run(client, "Оплата **4 500 ₽** по [ссылке](x)", [])
    [request] = client.of(functions.messages.SendMessageRequest)
    assert request.message == "Оплата **4 500 ₽** по [ссылке](x)"
    assert not request.entities
    assert request.random_id == mtproto_send.step_random_id(12345, 0)
    assert len(ids) == 1


def test_voice_note_has_voice_flag_duration_and_waveform(workdir):
    client = FakeClient()
    voice = OutgoingFile(
        path=_blob(workdir, "v.ogg"),
        file_name="Голосовое.ogg",
        mime_type="audio/ogg",
        kind="voice",
        duration_sec=7,
        waveform=bytes([0, 5, 31, 12] * 25),
    )
    ids = run(client, None, [voice])
    [request] = client.of(functions.messages.SendMediaRequest)
    media = request.media
    assert isinstance(media, types.InputMediaUploadedDocument)
    assert media.mime_type == "audio/ogg"
    audio = next(a for a in media.attributes if isinstance(a, types.DocumentAttributeAudio))
    assert audio.voice is True and audio.duration == 7
    # Своя распаковка: у Telethon последний столбик читается неверно.
    assert list(decode_waveform(audio.waveform))[:100] == [0, 5, 31, 12] * 25
    assert len(ids) == 1


def test_album_of_photos_one_request_caption_on_first(workdir):
    client = FakeClient()
    files = [
        OutgoingFile(
            path=_image(workdir, f"{i}.jpg"),
            file_name=f"{i}.jpg",
            mime_type="image/jpeg",
            kind="photo",
        )
        for i in range(3)
    ]
    ids = run(client, "Ваши карты", files)
    [album] = client.of(functions.messages.SendMultiMediaRequest)
    assert len(album.multi_media) == 3
    assert [m.message for m in album.multi_media] == ["Ваши карты", "", ""]
    assert all(isinstance(m.media, types.InputMediaPhoto) for m in album.multi_media)
    assert len(client.of(functions.messages.UploadMediaRequest)) == 3
    assert len(ids) == 3 and ids == sorted(ids)


def test_video_keeps_size_duration_and_thumbnail(workdir):
    client = FakeClient()
    thumb = _image(workdir, "thumb.jpg", (320, 180))
    video = OutgoingFile(
        path=_blob(workdir, "clip.mp4"),
        file_name="clip.mp4",
        mime_type="video/mp4",
        kind="video",
        width=1280,
        height=720,
        duration_sec=42,
        thumb_path=thumb,
    )
    run(client, None, [video])
    [request] = client.of(functions.messages.SendMediaRequest)
    media = request.media
    attr = next(a for a in media.attributes if isinstance(a, types.DocumentAttributeVideo))
    assert (attr.w, attr.h, attr.duration) == (1280, 720, 42)
    assert media.thumb is not None
    assert media.nosound_video is True
    # и превью, и сам файл ушли в Telegram
    assert any(path == thumb for path, _ in client.uploads)


def test_document_is_forced_file_with_original_name(workdir):
    client = FakeClient()
    doc = OutgoingFile(
        path=_blob(workdir, "file.pdf"),
        file_name="Разбор натальной карты.pdf",
        mime_type="application/pdf",
        kind="document",
    )
    run(client, "Готово", [doc])
    [request] = client.of(functions.messages.SendMediaRequest)
    assert request.media.force_file is True
    names = [
        a.file_name
        for a in request.media.attributes
        if isinstance(a, types.DocumentAttributeFilename)
    ]
    assert names == ["Разбор натальной карты.pdf"]
    assert request.message == "Готово"


def test_rejected_photo_is_resent_as_document(workdir):
    rejected = PhotoInvalidDimensionsError(request=None)
    client = FakeClient(fail={functions.messages.SendMediaRequest: [rejected]})
    photo = OutgoingFile(
        path=_image(workdir, "wide.jpg"), file_name="wide.jpg", mime_type="image/jpeg", kind="photo"
    )
    ids = run(client, None, [photo])
    first, second = client.of(functions.messages.SendMediaRequest)
    assert isinstance(first.media, types.InputMediaUploadedPhoto)
    assert isinstance(second.media, types.InputMediaUploadedDocument)
    assert second.media.force_file is True
    assert len(ids) == 1


def test_huge_photo_is_downscaled_before_upload(workdir):
    client = FakeClient()
    photo = OutgoingFile(
        path=_image(workdir, "big.png", (4000, 3000)),
        file_name="big.png",
        mime_type="image/png",
        kind="photo",
    )
    run(client, None, [photo])
    uploaded_path = client.uploads[0][0]
    assert uploaded_path.endswith("photo.jpg")


def test_duplicate_random_id_is_not_resent_and_ids_recovered(workdir):
    duplicate = RandomIdDuplicateError(request=None)
    client = FakeClient(fail={functions.messages.SendMessageRequest: [duplicate]})
    asked: list[int] = []

    async def recover(count, already):  # noqa: ANN001
        asked.append(count)
        return [777]

    ids = run(client, "уже отправлено", [], recover=recover)
    assert ids == [777]
    assert asked == [1]
    # повторной отправки не было — только одна попытка
    assert len(client.of(functions.messages.SendMessageRequest)) == 1


def test_random_ids_are_stable_between_retries(workdir):
    files = [
        OutgoingFile(
            path=_image(workdir, f"{i}.jpg"),
            file_name=f"{i}.jpg",
            mime_type="image/jpeg",
            kind="photo",
        )
        for i in range(2)
    ]
    first, second = FakeClient(), FakeClient()
    run(first, "текст", files, base=555)
    run(second, "текст", files, base=555)
    ids_first = [
        m.random_id for m in first.of(functions.messages.SendMultiMediaRequest)[0].multi_media
    ]
    ids_second = [
        m.random_id for m in second.of(functions.messages.SendMultiMediaRequest)[0].multi_media
    ]
    assert ids_first == ids_second
    assert len(set(ids_first)) == 2


def test_caption_too_long_for_account_goes_as_text(workdir):
    too_long = MediaCaptionTooLongError(request=None)
    client = FakeClient(fail={functions.messages.SendMediaRequest: [too_long]})
    doc = OutgoingFile(
        path=_blob(workdir, "a.pdf"),
        file_name="a.pdf",
        mime_type="application/pdf",
        kind="document",
    )
    ids = run(client, "подпись", [doc])
    [text] = client.of(functions.messages.SendMessageRequest)
    assert text.message == "подпись"
    retried = client.of(functions.messages.SendMediaRequest)[-1]
    assert retried.message == ""
    assert len(ids) == 2


def test_broken_album_falls_back_to_single_files(workdir):
    client = FakeClient(
        fail={functions.messages.SendMultiMediaRequest: [MediaInvalidError(request=None)]}
    )
    files = [
        OutgoingFile(
            path=_image(workdir, f"{i}.jpg"),
            file_name=f"{i}.jpg",
            mime_type="image/jpeg",
            kind="photo",
        )
        for i in range(2)
    ]
    ids = run(client, "два фото", files)
    singles = client.of(functions.messages.SendMediaRequest)
    assert len(singles) == 2
    assert [s.message for s in singles] == ["два фото", ""]
    assert len(ids) == 2


def test_forward_hides_author_and_maps_ids():
    client = FakeClient()
    ids = asyncio.run(
        mtproto_send.forward(
            client,
            types.InputPeerUser(1, 2),
            types.InputPeerUser(3, 4),
            [10, 11, 12],
            drop_author=True,
            base_random_id=99,
        )
    )
    [request] = client.of(functions.messages.ForwardMessagesRequest)
    assert request.drop_author is True
    assert request.id == [10, 11, 12]
    assert len(request.random_id) == 3
    assert len(ids) == 3


def test_ids_from_updates_uses_random_id_mapping():
    updates = types.Updates(
        updates=[
            types.UpdateMessageID(id=21, random_id=2),
            types.UpdateMessageID(id=20, random_id=1),
        ],
        users=[],
        chats=[],
        date=NOW,
        seq=0,
    )
    assert mtproto_send.ids_from_updates(updates, [1, 2]) == [20, 21]
