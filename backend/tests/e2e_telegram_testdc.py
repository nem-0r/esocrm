"""Сквозная проверка шлюза на ТЕСТОВЫХ серверах Telegram.

Запуск (нужны TELEGRAM_API_ID и TELEGRAM_API_HASH и выход в интернет):

    python -m tests.e2e_telegram_testdc

Что происходит:

- на тестовом сервере Telegram (не боевом!) заводятся тестовые номера вида
  99966XYYYY — код входа у них всегда XXXXX, SIM-карта не нужна;
- «аккаунт компании» работает через боевой провайдер CRM (`MTProtoProvider`) —
  ровно тот код, что стоит на продакшене; «клиент» — обычный Telegram-клиент;
- «телефон менеджера» — вторая сессия номера компании: так проверяется
  прочтение с телефона;
- компания шлёт клиенту всё, что умеет CRM, и клиент проверяет, что увидел;
  клиент шлёт компании всё подряд, и проверяется, что разобрал шлюз CRM.

Базы данных и Redis проверка не требует: провайдеру подставляются приёмники
событий, которые складывают разобранное в память.

Тестовые номера общие для всех разработчиков мира и периодически стираются
Telegram, поэтому номера случайные, а сессии кешируются в файл
(`TESTDC_SESSIONS`, по умолчанию во временном каталоге).
"""

import asyncio
import json
import os
import random
import secrets
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from typing import Any

from PIL import Image
from telethon import TelegramClient, functions, types
from telethon.errors import FloodWaitError
from telethon.sessions import StringSession

from app.core import crypto
from app.core.config import settings
from app.gateway import mtproto_provider as mp
from app.gateway.mtproto_receive import decode_waveform
from app.gateway.provider import MediaGone
from app.gateway.send_plan import OutgoingFile
from app.models import TelegramAccount
from app.services import media
from app.services.inbound_service import InboundMessage

TEST_DC = (2, "149.154.167.40", 443)
SESSIONS = os.environ.get("TESTDC_SESSIONS") or os.path.join(
    tempfile.gettempdir(), "astra-testdc-sessions.json"
)
# test — тестовые серверы Telegram и номера 99966XYYYY (код подставляется сам).
# prod — настоящий Telegram и ЗАПАСНЫЕ номера из E2E_COMPANY_PHONE и
# E2E_CLIENT_PHONE: код входа спрашивается в консоли один раз, дальше сессии
# берутся из файла. Боевые номера компании сюда не подставлять никогда.
#
# На 27.09.2026 вход тестовыми номерами Telegram отклоняет любым кодом
# (PHONE_CODE_INVALID на всех тестовых DC; та же жалоба в трекерах Telethon,
# GramJS и TDLib с 2024 года) — поэтому и нужен режим prod.
MODE = os.environ.get("E2E_MODE", "test")

ok: list[str] = []
bad: list[str] = []


def check(title: str, condition: bool, detail: Any = "") -> None:
    (ok if condition else bad).append(title)
    suffix = f" — {detail}" if detail not in ("", None) else ""
    print(f"  {'✓' if condition else '✗'} {title}{suffix}", flush=True)


# ------------------------------------------------------------ сессии


def _load_sessions() -> dict[str, dict[str, str]]:
    try:
        with open(SESSIONS, encoding="utf-8") as source:
            return json.load(source)
    except (OSError, ValueError):
        return {}


def _save_sessions(data: dict[str, dict[str, str]]) -> None:
    with open(SESSIONS, "w", encoding="utf-8") as target:
        json.dump(data, target)
    os.chmod(SESSIONS, 0o600)


def _new_phone() -> str:
    return f"99966{TEST_DC[0]}{random.randint(1000, 9999)}"  # noqa: S311 — тестовый номер


async def _client(session: str | None) -> TelegramClient:
    client = TelegramClient(
        StringSession(session), settings.telegram_api_id, settings.telegram_api_hash
    )
    if not session and MODE == "test":
        client.session.set_dc(*TEST_DC)
    await client.connect()
    return client


def _phone_for(role: str) -> str | None:
    if MODE != "prod":
        return None
    variable = {"company": "E2E_COMPANY_PHONE", "client": "E2E_CLIENT_PHONE"}.get(role)
    return os.environ.get(variable) if variable else None


async def _login(
    role: str, sessions: dict[str, dict[str, str]], phone: str | None = None
) -> tuple[TelegramClient, str]:
    """Войти тестовым номером (или взять сохранённую сессию). Возвращает клиента и номер."""
    saved = sessions.get(role)
    if saved and (phone is None or saved["phone"] == phone):
        client = await _client(saved["session"])
        if await client.is_user_authorized():
            return client, saved["phone"]
        await client.disconnect()
    phone = phone or _phone_for(role) or (saved["phone"] if saved else None)
    if phone is None:
        if MODE == "prod":
            raise SystemExit(f"Для роли {role} нужен номер в E2E_COMPANY_PHONE / E2E_CLIENT_PHONE")
        phone = _new_phone()
    client = await _client(None)
    code = phone[5] * 5 if MODE == "test" else None
    for attempt in range(3):
        try:
            await client.start(
                phone=phone,
                code_callback=(lambda: code) if code else (lambda: input(f"Код для {phone}: ")),
                first_name=f"CRM {role}",
                last_name="Test",
            )
            break
        except FloodWaitError as exc:
            if exc.seconds > 120 or attempt == 2:
                raise
            print(f"  … тестовый сервер просит подождать {exc.seconds} с", flush=True)
            await asyncio.sleep(exc.seconds + 1)
    sessions[role] = {"phone": phone, "session": client.session.save()}
    _save_sessions(sessions)
    return client, phone


# ------------------------------------------------------------ файлы


def _ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], check=True)  # noqa: S603, S607


def _make_files(workdir: str) -> dict[str, str]:
    paths = {
        "tone": os.path.join(workdir, "tone.wav"),
        "video": os.path.join(workdir, "clip.mp4"),
        "round": os.path.join(workdir, "round.mp4"),
        "doc": os.path.join(workdir, "doc.pdf"),
        "big": os.path.join(workdir, "big.png"),
    }
    _ffmpeg("-f", "lavfi", "-i", "sine=frequency=440:duration=3", "-ac", "1", paths["tone"])
    _ffmpeg(
        "-f",
        "lavfi",
        "-i",
        "testsrc=size=640x360:rate=25:duration=3",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=330:duration=3",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-shortest",
        "-movflags",
        "+faststart",
        paths["video"],
    )
    _ffmpeg(
        "-f",
        "lavfi",
        "-i",
        "testsrc=size=240x240:rate=25:duration=2",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=500:duration=2",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-shortest",
        paths["round"],
    )
    with open(paths["doc"], "wb") as target:
        target.write(b"%PDF-1.4\n% test document\n" + os.urandom(2048))
    Image.new("RGB", (4000, 3000), (40, 90, 160)).save(paths["big"], "PNG")
    for index in range(3):
        path = os.path.join(workdir, f"p{index}.jpg")
        Image.new("RGB", (640, 480), (40 * index, 120, 200)).save(path, "JPEG")
        paths[f"p{index}"] = path
    return paths


def _photo(path: str, name: str) -> OutgoingFile:
    return OutgoingFile(path=path, file_name=name, mime_type="image/jpeg", kind="photo")


# ------------------------------------------------------------ ожидание


async def _until(predicate: Callable[[], Any], seconds: float = 20.0) -> Any:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        await asyncio.sleep(0.3)
    return predicate()


def _rid() -> int:
    return secrets.randbits(62)


# ------------------------------------------------------------ сценарий


async def run() -> int:  # noqa: C901, PLR0915 — один длинный сценарий читается проще
    if not settings.telegram_api_id or not settings.telegram_api_hash:
        print("Нет TELEGRAM_API_ID / TELEGRAM_API_HASH — проверять не на чем.")
        return 1
    if not media.available():
        print("Нет ffmpeg — не из чего собрать голосовое и видео.")
        return 1

    where = f"тестовый сервер Telegram DC{TEST_DC[0]}" if MODE == "test" else "НАСТОЯЩИЙ Telegram"
    print(f"Режим {MODE}: {where}\n")
    sessions = _load_sessions()
    company_raw, company_phone = await _login("company", sessions)
    company_me = await company_raw.get_me()
    company_session = company_raw.session.save()
    await company_raw.disconnect()
    phone_client, _ = await _login("company_phone", sessions, phone=company_phone)
    client, client_phone = await _login("client", sessions)
    client_me = await client.get_me()
    print(
        f"Компания: {company_phone} (id {company_me.id}), клиент: {client_phone} (id {client_me.id})\n"
    )

    # Боевой провайдер CRM, без базы: пропуск к собеседнику берём у Telethon,
    # «известные» номера сообщений — из памяти.
    known: set[int] = set()

    async def entity(self, tg, account, chat_id):  # noqa: ANN001, ANN202
        return await tg.get_input_entity(chat_id)

    async def known_ids(account_id: int, chat_id: int) -> set[int]:
        return set(known)

    mp.MTProtoProvider._entity = entity  # type: ignore[method-assign]
    mp._known_tg_ids = known_ids  # type: ignore[assignment]

    inbound: list[InboundMessage] = []
    reads: list[tuple[int, int]] = []
    inbox_reads: list[tuple[int, int]] = []
    deletions: list[list[int]] = []

    async def sink(account_id: int, event: InboundMessage) -> None:
        inbound.append(event)

    async def read_sink(account_id: int, chat_id: int, max_id: int) -> None:
        reads.append((chat_id, max_id))

    async def inbox_sink(account_id: int, chat_id: int, max_id: int) -> None:
        inbox_reads.append((chat_id, max_id))

    async def delete_sink(account_id: int, chat_id: int | None, ids: list[int]) -> None:
        deletions.append(ids)

    async def status_sink(account_id: int, status: str, reason: str | None) -> None:
        print(f"  ! статус аккаунта: {status} {reason or ''}")

    provider = mp.MTProtoProvider()
    provider.set_sinks(sink, read_sink, status_sink, None, delete_sink, inbox_sink)
    account = TelegramAccount(
        id=1,
        title="test",
        phone=company_phone,
        api_id=settings.telegram_api_id,
        api_hash_enc=crypto.encrypt(settings.telegram_api_hash),
        session_enc=crypto.encrypt(company_session),
    )
    await provider.start(account)
    workdir = tempfile.mkdtemp(prefix="astra-e2e-")
    files = _make_files(workdir)

    try:
        # ---------------------------------------------------- знакомство
        print("Знакомство: клиент пишет первым")
        imported = await client(
            functions.contacts.ImportContactsRequest(
                [
                    types.InputPhoneContact(
                        client_id=1, phone=company_phone, first_name="CRM", last_name=""
                    )
                ]
            )
        )
        company_peer = imported.users[0] if imported.users else company_me.id
        await client.send_message(company_peer, "Здравствуйте! Хочу разбор")
        # В личных чатах у отправителя и получателя свои номера одного и того
        # же сообщения — сопоставляем по содержимому, а не по номеру.
        got = await _until(lambda: [e for e in inbound if e.text == "Здравствуйте! Хочу разбор"])
        check("входящее дошло до шлюза CRM", bool(got))
        if got:
            check("клиент опознан по id", got[0].peer.tg_user_id == client_me.id)
            check("это входящее, а не исходящее", got[0].outgoing is False)

        async def latest(count: int = 1) -> list[Any]:
            """Последние сообщения глазами клиента, по порядку отправки."""
            items = await client.get_messages(company_peer, limit=count)
            return list(reversed(items))

        async def next_inbound(send: Callable[[], Any]) -> InboundMessage | None:
            """Отправить от клиента и дождаться, что разобрал шлюз CRM."""
            before = len(inbound)
            await send()
            await _until(lambda: len(inbound) > before, 30)
            return inbound[before] if len(inbound) > before else None

        # ---------------------------------------------------- CRM → клиент
        print("\nCRM → клиент")
        raw_text = "Оплата **4 500 ₽** по [ссылке](x) и __курсив__"
        sent = await provider.send_message(account, client_me.id, raw_text, _rid(), [])
        [seen] = await latest()
        check(
            "текст дошёл дословно, без разметки",
            seen is not None and seen.message == raw_text,
            seen.message if seen else None,
        )

        voice_path = os.path.join(workdir, "voice.ogg")
        await media.transcode_voice(files["tone"], voice_path)
        wave = await media.waveform(voice_path)
        voice_probe = await media.probe(voice_path)
        voice = OutgoingFile(
            path=voice_path,
            file_name="Голосовое.ogg",
            mime_type="audio/ogg",
            kind="voice",
            duration_sec=voice_probe.duration_sec if voice_probe else 3,
            waveform=bytes(wave),
        )
        sent = await provider.send_message(account, client_me.id, None, _rid(), [voice])
        [seen] = await latest()
        attr = None
        if seen is not None and seen.voice is not None:
            attr = next(
                a for a in seen.voice.attributes if isinstance(a, types.DocumentAttributeAudio)
            )
        check("клиенту пришло именно голосовое", attr is not None and attr.voice)
        check(
            "у голосового верная длительность",
            attr is not None and attr.duration == 3,
            attr.duration if attr else None,
        )
        check(
            "у голосового есть волна",
            attr is not None and bool(attr.waveform) and max(decode_waveform(attr.waveform)) > 0,
        )

        album = [_photo(files[f"p{i}"], f"карта-{i}.jpg") for i in range(3)]
        sent = await provider.send_message(account, client_me.id, "Ваши карты", _rid(), album)
        album_ids = [*sent.extra_ids, sent.tg_message_id]
        known.update(album_ids)
        seen_album = await latest(3)
        grouped = {m.grouped_id for m in seen_album if m is not None}
        check("альбом из 3 фото: три номера в ответе", len(album_ids) == 3, album_ids)
        check("клиент видит один альбом", len(grouped) == 1 and None not in grouped, grouped)
        check(
            "подпись — у первого фото",
            seen_album[0] is not None and seen_album[0].message == "Ваши карты",
        )

        video_probe = await media.probe(files["video"])
        thumb = os.path.join(workdir, "thumb.jpg")
        await media.video_thumbnail(
            files["video"], thumb, video_probe.duration if video_probe else None
        )
        video = OutgoingFile(
            path=files["video"],
            file_name="Видео.mp4",
            mime_type="video/mp4",
            kind="video",
            width=video_probe.width,
            height=video_probe.height,
            duration_sec=video_probe.duration_sec,
            thumb_path=thumb,
        )
        sent = await provider.send_message(account, client_me.id, None, _rid(), [video])
        [seen] = await latest()
        vattr = None
        if seen is not None and seen.video is not None:
            vattr = next(
                a for a in seen.video.attributes if isinstance(a, types.DocumentAttributeVideo)
            )
        check("видео пришло видео, а не файлом", vattr is not None)
        check(
            "у видео настоящие размеры и длительность",
            vattr is not None and (vattr.w, vattr.h) == (640, 360) and round(vattr.duration) == 3,
            (vattr.w, vattr.h, vattr.duration) if vattr else None,
        )
        check(
            "у видео есть кадр-превью", seen is not None and bool(seen.video and seen.video.thumbs)
        )

        doc = OutgoingFile(
            path=files["doc"],
            file_name="Разбор карты.pdf",
            mime_type="application/pdf",
            kind="document",
        )
        sent = await provider.send_message(account, client_me.id, "Готово ✨", _rid(), [doc])
        [seen] = await latest()
        check(
            "документ сохранил русское имя",
            seen is not None and seen.file and seen.file.name == "Разбор карты.pdf",
            seen.file.name if seen and seen.file else None,
        )
        check("подпись к документу на месте", seen is not None and seen.message == "Готово ✨")

        long_text = "Длинное описание разбора. " * 60
        sent = await provider.send_message(
            account, client_me.id, long_text, _rid(), [_photo(files["p0"], "a.jpg")]
        )
        check("длинный текст с фото — два сообщения", len(sent.extra_ids) == 1, sent)
        if sent.extra_ids:
            text_part, photo_part = await latest(2)
            check(
                "сначала текст целиком",
                text_part is not None and text_part.message == long_text.strip(),
            )
            check(
                "потом фото без подписи",
                photo_part is not None and photo_part.photo is not None and not photo_part.message,
            )

        big = OutgoingFile(
            path=files["big"], file_name="скрин.png", mime_type="image/png", kind="photo"
        )
        sent = await provider.send_message(account, client_me.id, None, _rid(), [big])
        [seen] = await latest()
        check(
            "огромное фото уменьшено и принято как фото",
            seen is not None and seen.photo is not None,
        )

        base = _rid()
        first = await provider.send_message(account, client_me.id, "Повтор без дубля", base, [])
        known.update([first.tg_message_id])
        again = await provider.send_message(account, client_me.id, "Повтор без дубля", base, [])
        recent = await client.get_messages(company_peer, limit=10)
        copies = [m for m in recent if m.message == "Повтор без дубля"]
        check("повтор той же отправки не создал дубль у клиента", len(copies) == 1, len(copies))
        check(
            "повтор не придумал чужой номер",
            again.tg_message_id in (None, first.tg_message_id),
            again,
        )

        # ---------------------------------------------------- пересылка
        print("\nПересылка")
        source = await next_inbound(
            lambda: client.send_message(company_peer, "Перешлите это, пожалуйста")
        )
        source_id = source.tg_message_id if source else 0
        ids = await provider.forward_messages(
            account, client_me.id, client_me.id, [source_id], True, _rid()
        )
        [seen] = await latest()
        check(
            "пересылка со скрытым отправителем дошла",
            seen is not None and seen.message == "Перешлите это, пожалуйста",
        )
        check("подписи «Переслано от» нет", seen is not None and seen.fwd_from is None)
        ids = await provider.forward_messages(
            account, client_me.id, client_me.id, [source_id], False, _rid()
        )
        [seen] = await latest()
        check(
            "без скрытия — подпись «Переслано от» есть",
            seen is not None and seen.fwd_from is not None,
        )
        ids = await provider.forward_messages(
            account, client_me.id, client_me.id, album_ids, True, _rid()
        )
        seen_album = await latest(3)
        grouped = {m.grouped_id for m in seen_album if m is not None}
        check(
            "пересланный альбом остался альбомом",
            len(ids) == 3 and len(grouped) == 1 and None not in grouped,
            ids,
        )
        try:
            await provider.forward_messages(
                account, client_me.id, client_me.id, [999_999_999], True, _rid()
            )
            check("пересылка несуществующего даёт ошибку для перехода на копию", False)
        except mp.ForwardImpossible:
            check("пересылка несуществующего даёт ошибку для перехода на копию", True)

        # ---------------------------------------------------- клиент → CRM
        print("\nКлиент → CRM")
        poll = types.InputMediaPoll(
            poll=types.Poll(
                id=random.randint(1, 10**9),  # noqa: S311
                question=types.TextWithEntities(text="Когда удобно?", entities=[]),
                answers=[
                    types.PollAnswer(
                        text=types.TextWithEntities(text="Утром", entities=[]), option=b"1"
                    ),
                    types.PollAnswer(
                        text=types.TextWithEntities(text="Вечером", entities=[]), option=b"2"
                    ),
                ],
                hash=0,
            )
        )
        repeat_copy = next(m for m in await latest(20) if m.message == "Повтор без дубля")
        e_voice = await next_inbound(
            lambda: client.send_file(company_peer, voice_path, voice_note=True)
        )
        e_round = await next_inbound(
            lambda: client.send_file(company_peer, files["round"], video_note=True)
        )
        e_contact = await next_inbound(
            lambda: client.send_file(
                company_peer,
                types.InputMediaContact(
                    phone_number="+79990001122", first_name="Анна", last_name="Ли", vcard=""
                ),
            )
        )
        e_geo = await next_inbound(
            lambda: client.send_file(
                company_peer,
                types.InputMediaGeoPoint(types.InputGeoPoint(lat=55.7558, long=37.6173)),
            )
        )
        e_poll = await next_inbound(lambda: client.send_message(company_peer, file=poll))
        e_doc = await next_inbound(
            lambda: client.send_file(company_peer, files["doc"], caption="мой документ")
        )
        e_fwd = await next_inbound(
            lambda: client.forward_messages(company_peer, repeat_copy.id, company_peer)
        )

        event = e_voice
        attachment = event.attachments[0] if event and event.attachments else {}
        check("голосовое клиента распознано", event is not None and event.media_kind == "voice")
        check(
            "голосовое скачано сразу",
            attachment.get("status") == "ready" and bool(attachment.get("body")),
        )
        check("у голосового клиента есть волна", bool(attachment.get("waveform")))

        event = e_round
        check(
            "кружочек распознан как кружочек",
            event is not None and event.media_kind == "video_note",
        )

        event = e_contact
        check(
            "контакт — читаемым текстом",
            event is not None and event.text == "📇 Контакт: Анна Ли, +79990001122",
            event.text if event else None,
        )
        event = e_geo
        check(
            "геопозиция — текстом с координатами",
            event is not None and (event.text or "").startswith("📍"),
        )
        check(
            "и координатами для карты",
            event is not None and (event.meta or {}).get("location", {}).get("lat") == 55.7558,
        )
        event = e_poll
        check("опрос — вопросом", event is not None and event.text == "📊 Опрос: Когда удобно?")
        event = e_doc
        check(
            "документ с подписью",
            event is not None and event.media_kind == "document" and event.text == "мой документ",
        )
        event = e_fwd
        check(
            "пересланное клиентом помечено",
            event is not None and bool((event.meta or {}).get("forwarded_from")),
        )

        # ---------------------------------------------------- докачка
        print("\nДокачка файла из Telegram")
        target = os.path.join(workdir, "fetched.pdf")
        size = await provider.download_media(
            account, client_me.id, e_doc.tg_message_id if e_doc else 0, target
        )
        check("файл докачан по номеру сообщения", size == os.path.getsize(files["doc"]), size)
        try:
            await provider.download_media(
                account, client_me.id, 999_999_998, os.path.join(workdir, "x")
            )
            check("удалённое сообщение — понятная ошибка", False)
        except MediaGone:
            check("удалённое сообщение — понятная ошибка", True)

        # ---------------------------------------------------- правки, удаления, прочтение
        print("\nПравки, удаления, прочтение")
        draft_holder: list[Any] = []

        async def send_draft() -> None:
            draft_holder.append(await client.send_message(company_peer, "исправлю"))

        draft_event = await next_inbound(send_draft)
        company_draft_id = draft_event.tg_message_id if draft_event else 0
        draft = draft_holder[0]
        await client.edit_message(company_peer, draft.id, "исправлено")
        edit = await _until(
            lambda: [e for e in inbound if e.tg_message_id == company_draft_id and e.is_edit], 20
        )
        check("правка клиента дошла как правка", bool(edit) and edit[-1].text == "исправлено")

        await client.delete_messages(company_peer, [draft.id], revoke=True)
        gone = await _until(lambda: [ids for ids in deletions if company_draft_id in ids], 20)
        check("удаление клиента дошло до CRM", bool(gone))

        await client.send_read_acknowledge(company_peer)
        read = await _until(lambda: [r for r in reads if r[0] == client_me.id], 20)
        check("клиент прочитал — CRM узнала номер прочитанного", bool(read))

        fresh = await next_inbound(
            lambda: client.send_message(company_peer, "прочтите на телефоне")
        )
        fresh_id = fresh.tg_message_id if fresh else 0
        # «Телефон» — отдельная сессия: собеседника она знает из своих диалогов.
        await phone_client.get_dialogs(limit=20)
        client_entity = await phone_client.get_input_entity(client_me.id)
        await phone_client.send_read_acknowledge(client_entity, max_id=fresh_id)
        seen_on_phone = await _until(lambda: [r for r in inbox_reads if r[0] == client_me.id], 20)
        check(
            "прочтение с телефона дошло до CRM",
            bool(seen_on_phone) and seen_on_phone[-1][1] >= fresh_id,
            seen_on_phone,
        )
    finally:
        await provider.stop(account)
        await client.disconnect()
        await phone_client.disconnect()
        import shutil

        shutil.rmtree(workdir, ignore_errors=True)

    print(f"\nИтого: {len(ok)} прошло, {len(bad)} не прошло")
    for title in bad:
        print(f"  ✗ {title}")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
