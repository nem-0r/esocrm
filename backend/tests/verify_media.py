"""Медиа-конвейер: загрузка, голосовые, отправка вложений, выдача файлов.

Проверяется то, что увидят менеджер и клиент:

- тип файла определяет сервер, а не браузер; опасные типы не принимаются;
- запись с микрофона (WebM из Chrome) становится голосовым Telegram — Ogg/Opus
  с длительностью и волной;
- сообщение с альбомом и длинным текстом уходит в Telegram несколькими
  сообщениями, и CRM запоминает все их номера;
- файл отдаётся безопасно (nosniff, песочница, опасное — только скачиванием),
  по частям (HTTP Range — без этого Safari не играет медиа), и для Safari есть
  AAC-копия голосового.

Запуск: docker compose exec -T api python -m tests.verify_media
"""

import asyncio
import base64
import io
import os
import shutil
import sys
import tempfile
from datetime import UTC, datetime

import httpx
from PIL import Image
from sqlalchemy import select, text

from app.core import command_bus
from app.core.db import SessionLocal
from app.models import Attachment, Conversation, Message, TelegramAccount
from tests.support import ADMIN, BASE, MANAGER, Checks, login, make_media, probe, until


async def _target_conversation() -> Conversation:
    async with SessionLocal() as db:
        conv = await db.scalar(
            select(Conversation)
            .join(TelegramAccount, TelegramAccount.id == Conversation.account_id)
            .where(
                TelegramAccount.is_active.is_(True),
                TelegramAccount.deleted_at.is_(None),
                Conversation.is_blocked_by_client.is_(False),
                Conversation.closed_at.is_(None),
            )
            .order_by(Conversation.id)
        )
        assert conv is not None, "нет ни одного живого диалога"
        return conv


async def run() -> int:  # noqa: PLR0915 — сценарий читается сверху вниз
    c = Checks("Медиа: загрузка, голосовые, отправка и выдача файлов")
    workdir = tempfile.mkdtemp(prefix="astra-verify-media-")
    files = make_media(workdir)
    admin = await login(ADMIN)
    manager = await login(MANAGER)
    conv = await _target_conversation()

    async def upload(path: str, name: str | None = None, content_type: str = "application/octet-stream", endpoint: str = "upload"):  # noqa: E501
        with open(path, "rb") as source:
            return await admin.post(
                f"{BASE}/files/{endpoint}",
                files={"file": (name or os.path.basename(path), source.read(), content_type)},
            )

    try:
        c.section("Загрузка: тип определяет сервер")
        r = await upload(files["photo"], content_type="image/png")
        photo = r.json() if r.status_code == 201 else {}
        c.check("картинка принята", r.status_code == 201, r.text[:200])
        c.check("вид — фото, размеры узнаны", (photo.get("kind"), photo.get("width"), photo.get("height")) == ("photo", 800, 600), photo)

        r = await upload(files["video"], content_type="video/mp4")
        video = r.json() if r.status_code == 201 else {}
        c.check(
            "видео: размеры и длительность узнаны",
            (video.get("kind"), video.get("width"), video.get("height"), video.get("duration_sec")) == ("video", 640, 360, 3),
            video,
        )
        r = await upload(files["song"], content_type="audio/mpeg")
        song = r.json() if r.status_code == 201 else {}
        c.check("музыка: вид audio и длительность", (song.get("kind"), song.get("duration_sec")) == ("audio", 4), song)

        r = await upload(files["doc"], content_type="text/html")
        doc = r.json() if r.status_code == 201 else {}
        c.check("русское имя документа сохранено", doc.get("file_name") == "Разбор карты.pdf", doc)
        c.check("тип назначен сервером, а не браузером (заявлен text/html)", doc.get("mime_type") == "application/pdf", doc)

        for name in ("page.html", "script.js", "image.svg", "setup.exe"):
            r = await upload(files["html"], name=name, content_type="text/plain")
            c.check(f"«{name}» не принимается", r.status_code == 422, r.status_code)
        empty = os.path.join(workdir, "empty.txt")
        open(empty, "wb").close()
        r = await upload(empty, content_type="text/plain")
        c.check("пустой файл не принимается", r.status_code == 422, r.status_code)

        c.section("Голосовое с микрофона")
        r = await upload(files["voice_webm"], name="blob", content_type="audio/webm", endpoint="voice")
        voice = r.json() if r.status_code == 201 else {}
        c.check("запись принята", r.status_code == 201, r.text[:200])
        c.check("стала голосовым Ogg", (voice.get("kind"), voice.get("mime_type")) == ("voice", "audio/ogg"), voice)
        c.check("длительность 4 с", voice.get("duration_sec") == 4, voice.get("duration_sec"))
        wave = voice.get("waveform") or []
        c.check("волна из 100 столбиков 0..31", len(wave) == 100 and max(wave) == 31 and min(wave) >= 0, wave[:10])
        c.check("громкая первая половина видна на волне", sum(wave[:45]) > sum(wave[55:]) * 2)
        r = await upload(files["voice_short"], name="blob", content_type="audio/webm", endpoint="voice")
        c.check("слишком короткая запись отклонена понятно", r.status_code == 422 and "коротк" in r.text, r.text[:120])
        r = await upload(files["photo"], name="blob", content_type="audio/webm", endpoint="voice")
        c.check("не-звук вместо записи отклонён", r.status_code == 422, r.status_code)

        c.section("Отправка вложений в Telegram")
        r = await admin.post(f"{BASE}/conversations/{conv.id}/messages", json={"uploads": [voice]})
        sent_voice = r.json() if r.status_code == 201 else {}
        c.check("голосовое отправлено", r.status_code == 201, r.text[:200])
        c.check("сообщение — голосовое", sent_voice.get("kind") == "voice", sent_voice.get("kind"))
        attachment = (sent_voice.get("attachments") or [{}])[0]
        c.check(
            "у вложения вид, длительность и волна из хранилища",
            attachment.get("kind") == "voice" and attachment.get("duration_sec") == 4 and len(attachment.get("waveform") or []) == 100,
            attachment,
        )
        done = await until(lambda: _sent_state(sent_voice.get("id", 0)), 20)
        c.check("шлюз отправил голосовое (есть номер в Telegram)", bool(done), done)

        long_text = ("Описание разбора. " * 110).strip()
        album = [photo, (await upload(files["photo_jpg"], content_type="image/jpeg")).json(), video]
        r = await admin.post(
            f"{BASE}/conversations/{conv.id}/messages",
            json={"text": long_text, "uploads": album},
        )
        sent_album = r.json() if r.status_code == 201 else {}
        c.check("альбом с длинным текстом принят", r.status_code == 201, r.text[:200])
        state = await until(lambda: _sent_state(sent_album.get("id", 0)), 20)
        c.check(
            "в Telegram ушло 4 сообщения: текст + 3 файла, все номера запомнены",
            bool(state) and len(state[1] or []) == 3,
            state,
        )

        r = await admin.post(
            f"{BASE}/conversations/{conv.id}/messages",
            json={"uploads": [{"upload_key": "incoming/2026/01/foreign.jpg", "file_name": "x.jpg"}]},
        )
        c.check("чужой ключ хранилища к сообщению не прикрепить", r.status_code == 422, r.status_code)

        c.section("Выдача файлов")
        photo_att = next(a for a in sent_album.get("attachments", []) if a["kind"] == "photo")
        r = await admin.get(f"{BASE}/files/{photo_att['id']}")
        c.check("картинка отдаётся", r.status_code == 200 and r.headers.get("content-type") == "image/png", r.headers.get("content-type"))
        c.check("nosniff", r.headers.get("x-content-type-options") == "nosniff")
        c.check("песочница CSP", "sandbox" in (r.headers.get("content-security-policy") or ""))
        c.check("показ в браузере", (r.headers.get("content-disposition") or "").startswith("inline"))
        size = len(r.content)
        r = await admin.get(f"{BASE}/files/{photo_att['id']}", headers={"Range": "bytes=0-99"})
        c.check(
            "кусок файла: 206 и 100 байт",
            r.status_code == 206 and len(r.content) == 100 and r.headers.get("content-range") == f"bytes 0-99/{size}",
            (r.status_code, r.headers.get("content-range")),
        )
        r = await admin.get(f"{BASE}/files/{photo_att['id']}", headers={"Range": f"bytes={size}-"})
        c.check("кусок за краем файла — 416", r.status_code == 416, r.status_code)
        r = await admin.get(f"{BASE}/files/{photo_att['id']}?download=1")
        c.check("«Скачать» отдаёт файлом", (r.headers.get("content-disposition") or "").startswith("attachment"))
        etag = r.headers.get("etag")
        r = await admin.get(f"{BASE}/files/{photo_att['id']}?download=1", headers={"If-None-Match": etag or ""})
        c.check("повтор из кэша — 304", r.status_code == 304, r.status_code)

        voice_att_id = attachment.get("id")
        r = await admin.get(f"{BASE}/files/{voice_att_id}?variant=m4a")
        c.check("голосовое для Safari — AAC", r.status_code == 200 and r.headers.get("content-type") == "audio/mp4", r.status_code)
        if r.status_code == 200:
            m4a = os.path.join(workdir, "voice.m4a")
            with open(m4a, "wb") as target:
                target.write(r.content)
            codec = probe(m4a)["streams"][0]["codec_name"]
            c.check("внутри действительно AAC", codec == "aac", codec)
        started = datetime.now(UTC)
        r = await admin.get(f"{BASE}/files/{voice_att_id}?variant=m4a")
        c.check("повтор берётся из кэша хранилища", r.status_code == 200 and (datetime.now(UTC) - started).total_seconds() < 2)

        video_att = next(a for a in sent_album.get("attachments", []) if a["kind"] == "video")
        c.check("у видео есть превью", bool(video_att.get("thumb_url")), video_att)
        r = await admin.get(f"{BASE}/files/{video_att['id']}?variant=thumb")
        c.check("превью — JPEG", r.status_code == 200 and r.headers.get("content-type") == "image/jpeg", r.status_code)

        # Опасный файл от клиента: HTML с JavaScript.
        async with SessionLocal() as db:
            account = await db.get(TelegramAccount, conv.account_id)
            tg_user_id = conv.tg_chat_id
            account_id = account.id
        await command_bus.call(
            account_id,
            "demo_incoming",
            {
                "tg_user_id": tg_user_id,
                "tg_message_id": 880_000_000 + int(datetime.now(UTC).timestamp()) % 1_000_000,
                "text": None,
                "media_kind": "document",
                "attachments": [
                    {
                        "kind": "document",
                        "file_name": "invoice.html",
                        "mime_type": "text/html",
                        "status": "ready",
                        "body_b64": base64.b64encode(b"<script>fetch('/api/v1/auth/me')</script>").decode(),
                    }
                ],
            },
            timeout=20,
        )
        html_att = await until(lambda: _latest_attachment(conv.id, "invoice.html"), 10)
        if html_att:
            r = await admin.get(f"{BASE}/files/{html_att}")
            c.check(
                "HTML клиента не открывается страницей: только скачивание",
                r.headers.get("content-type") == "application/octet-stream"
                and (r.headers.get("content-disposition") or "").startswith("attachment")
                and "sandbox" in (r.headers.get("content-security-policy") or ""),
                (r.headers.get("content-type"), r.headers.get("content-disposition")),
            )
        else:
            c.check("HTML клиента записан", False, "вложение не появилось")

        # Прошлая версия знает не все виды сообщений: музыка и стикер пишутся
        # «документом», точный вид — у вложения, по нему и подпись в списке.
        c.section("Музыка и стикер: вид, понятный прошлой версии")
        r = await admin.post(f"{BASE}/conversations/{conv.id}/messages", json={"uploads": [song]})
        sent_song = r.json() if r.status_code == 201 else {}
        song_att = (sent_song.get("attachments") or [{}])[0]
        c.check("музыка отправлена", r.status_code == 201, r.text[:200])
        c.check(
            "сообщение — «документ», вложение — «аудио»",
            (sent_song.get("kind"), song_att.get("kind")) == ("document", "audio"),
            (sent_song.get("kind"), song_att.get("kind")),
        )
        preview = await _list_preview(admin, conv.id)
        c.check("в списке чатов — «Вы: Аудио»", preview == "Вы: Аудио", preview)

        buffer = io.BytesIO()
        Image.new("RGBA", (128, 128), (255, 200, 0, 255)).save(buffer, "WEBP")
        await command_bus.call(
            account_id,
            "demo_incoming",
            {
                "tg_user_id": tg_user_id,
                "tg_message_id": 881_000_000 + int(datetime.now(UTC).timestamp()) % 1_000_000,
                "text": None,
                "media_kind": "sticker",
                "attachments": [
                    {
                        "kind": "sticker",
                        "file_name": "sticker.webp",
                        "mime_type": "image/webp",
                        "status": "ready",
                        "meta": {"emoji": "🙏"},
                        "body_b64": base64.b64encode(buffer.getvalue()).decode(),
                    }
                ],
            },
            timeout=20,
        )
        sticker_att = await until(lambda: _latest_attachment(conv.id, "sticker.webp"), 10)
        kinds = await _kinds_of(sticker_att) if sticker_att else None
        c.check("стикер клиента: сообщение — «документ», вложение — «стикер»", kinds == ("document", "sticker"), kinds)
        preview = await _list_preview(admin, conv.id)
        c.check("в списке чатов — «Стикер»", preview == "Стикер", preview)

        async with SessionLocal() as db:
            hidden = (
                await db.execute(
                    text(
                        """
                        select a.id from attachments a
                        join conversations c on c.id = a.conversation_id
                        where a.deleted_at is null and not exists (
                            select 1 from account_managers am
                            join users u on u.id = am.user_id
                            where u.email = :email and am.account_id = c.account_id
                              and (c.responsible_id is null or c.responsible_id = u.id)
                        )
                        order by a.id limit 1
                        """
                    ),
                    {"email": MANAGER["email"]},
                )
            ).scalar()
        if hidden:
            r = await manager.get(f"{BASE}/files/{hidden}")
            c.check("чужой файл для менеджера не существует (404)", r.status_code == 404, r.status_code)
    finally:
        await admin.aclose()
        await manager.aclose()
        shutil.rmtree(workdir, ignore_errors=True)
    return c.finish()


async def _sent_state(message_id: int):  # noqa: ANN202
    async with SessionLocal() as db:
        message = await db.get(Message, message_id)
        if message is None or message.tg_message_id is None or message.status.value not in ("sent", "read"):
            return None
        return message.tg_message_id, message.tg_extra_ids


async def _kinds_of(attachment_id: int) -> tuple[str, str | None]:
    async with SessionLocal() as db:
        attachment = await db.get(Attachment, attachment_id)
        message = await db.get(Message, attachment.message_id)
        return message.kind.value, attachment.kind


async def _list_preview(client: httpx.AsyncClient, conversation_id: int) -> str | None:
    r = await client.get(f"{BASE}/conversations", params={"filter": "all", "limit": 100})
    return next(
        (row["last_message_preview"] for row in r.json()["items"] if row["id"] == conversation_id),
        None,
    )


async def _latest_attachment(conversation_id: int, file_name: str) -> int | None:
    async with SessionLocal() as db:
        return await db.scalar(
            select(Attachment.id)
            .where(Attachment.conversation_id == conversation_id, Attachment.file_name == file_name)
            .order_by(Attachment.id.desc())
        )


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
