"""Боевой поставщик Telegram на MTProto (Telethon).

Это не бот. Менеджеры пишут с личных аккаунтов, поэтому CRM держит **вторую
сессию** того же аккаунта: телефон владельца продолжает работать как раньше,
а мы получаем те же сообщения параллельно. Bot API так не умеет — он видит
только то, что написали боту.

Что здесь важно и почему:

- **Сессия равна аккаунту.** Строка сессии — это полный доступ к чужому Telegram.
  Она никогда не пишется в лог и хранится только зашифрованной (`session_enc`).
- **Клиент живёт в процессе шлюза.** Вход в аккаунт нельзя разорвать между
  процессами: код подтверждения принадлежит соединению, которое его запросило.
  Поэтому API просит шлюз через `command_bus`, а не ходит в Telegram сам.
- **`access_hash` — пропуск, а не свойство человека.** Он выдаётся конкретному
  аккаунту, поэтому хранится в `telegram_peers` парой (аккаунт, собеседник).
  Без него после перезапуска нельзя ответить тому, кого нет в свежем списке диалогов.
- **FloodWait — это не ошибка, а расписание.** Telegram говорит, через сколько
  секунд можно повторить; повтор раньше срока удлиняет запрет. Пробрасываем
  наверх `RetryAfter`, и очередь ждёт ровно столько, сколько велено.
"""

import asyncio
import io
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime

from cryptography.exceptions import InvalidTag
from telethon import TelegramClient, events, functions
from telethon.errors import (
    ApiIdInvalidError,
    AuthKeyUnregisteredError,
    FloodWaitError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    PhoneNumberBannedError,
    PhoneNumberFloodError,
    PhoneNumberInvalidError,
    SendCodeUnavailableError,
    SessionPasswordNeededError,
    SessionRevokedError,
    UserDeactivatedBanError,
    UserIsBlockedError,
)
from telethon.sessions import StringSession
from telethon.tl.types import (
    DocumentAttributeAudio,
    DocumentAttributeFilename,
    DocumentAttributeVideo,
)
from telethon.tl.types.auth import SentCodeTypeApp
from telethon.tl.types import (
    User as TgUser,
)

from app.core import crypto
from app.core.config import settings
from app.gateway.provider import CodeRequest, SentMessage, SessionResult
from app.models import TelegramAccount
from app.services.inbound_service import InboundMessage, PeerData

log = logging.getLogger("astra.mtproto")

# Сколько файла соглашаемся тянуть в память при приёме. Больше — не влезет в письмо
# менеджеру и почти наверняка не нужно в переписке о консультации.
MAX_INCOMING_BYTES = 25 * 1024 * 1024

Sink = Callable[[int, InboundMessage], Awaitable[None]]
ReadSink = Callable[[int, int, int], Awaitable[None]]
StatusSink = Callable[[int, str, str | None], Awaitable[None]]


class RetryAfter(RuntimeError):
    """Telegram просит подождать. `seconds` — сколько именно."""

    def __init__(self, seconds: int) -> None:
        super().__init__(f"Telegram просит подождать {seconds} с")
        self.seconds = seconds


class SessionLost(RuntimeError):
    """Сессия больше не действует: разлогинили, забанили или отозвали."""


class ClientBlocked(RuntimeError):
    """Собеседник заблокировал этот номер — сообщение доставить нельзя."""


class LoginRefused(RuntimeError):
    """Telegram отказал во входе по причине, которую нельзя обойти повтором.

    Текст такой ошибки уходит прямо руководителю в интерфейс, поэтому здесь
    живёт человеческая формулировка, а не англоязычная строка из Telethon.
    """


class MTProtoProvider:
    """Держит по клиенту на арендованный аккаунт."""

    def __init__(self) -> None:
        self._clients: dict[int, TelegramClient] = {}
        self._logins: dict[int, TelegramClient] = {}
        self._sink: Sink | None = None
        self._read_sink: ReadSink | None = None
        self._status_sink: StatusSink | None = None
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ приём

    def set_sinks(
        self, sink: Sink, read_sink: ReadSink | None = None, status_sink: StatusSink | None = None
    ) -> None:
        """Куда отдавать полученное. Записью в базу занимается шлюз, а не провайдер:
        здесь только Telegram, чтобы эту часть можно было проверять отдельно."""
        self._sink = sink
        self._read_sink = read_sink
        self._status_sink = status_sink

    # --------------------------------------------------------------- клиенты

    def _build(self, account: TelegramAccount, session: str | None) -> TelegramClient:
        api_hash = crypto.decrypt(account.api_hash_enc)
        return TelegramClient(
            StringSession(session or None),
            account.api_id,
            api_hash,
            # Нейтральный, а не самопальный фингерпринт устройства: явное имя
            # вроде "Astra CRM" на входе с серверного IP — лишний повод для
            # антиспам-фильтра Telegram счесть вход автоматическим (docs/12,
            # раздел «Про прокси — не перестраховка»).
            device_model="Desktop",
            system_version="Windows 10",
            app_version="5.5.4",
            # Прокси один на все аккаунты (или прямое подключение с адреса
            # сервера, если не задан) — решение D-21 сознательно не делается:
            # отдельные прокси на номер не покупаются.
            proxy=_proxy_for(account),
            connection_retries=None,  # переподключаться бесконечно, а не падать
            request_retries=3,
            auto_reconnect=True,
        )

    async def _connected(self, account: TelegramAccount) -> TelegramClient:
        client = self._clients.get(account.id)
        if client is not None and client.is_connected():
            return client
        if account.session_enc is None:
            raise SessionLost("Аккаунт не подключён: нет сохранённой сессии")
        async with self._lock:
            try:
                client = self._build(account, crypto.decrypt(account.session_enc))
            except InvalidTag as exc:
                # Без этого сбой тихо пробрасывается наверх как есть: `start()`
                # ловит только SessionLost, а сырую cryptography-ошибку никто
                # не превращает в понятную причину на экране аккаунта.
                raise SessionLost(
                    "Не удалось расшифровать данные аккаунта — на сервере сменился "
                    "ключ шифрования. Подключите аккаунт заново."
                ) from exc
            await client.connect()
            if not await client.is_user_authorized():
                await client.disconnect()
                raise SessionLost("Сессия недействительна — нужен повторный вход")
            self._register_handlers(account.id, client)
            self._clients[account.id] = client
        return client

    def _register_handlers(self, account_id: int, client: TelegramClient) -> None:
        @client.on(events.NewMessage())
        async def _on_new(event) -> None:  # noqa: ANN001 — тип события внутри Telethon
            await self._forward(account_id, client, event.message, live=True)

        @client.on(events.MessageEdited())
        async def _on_edited(event) -> None:  # noqa: ANN001
            await self._forward(account_id, client, event.message, live=True)

        @client.on(events.MessageRead(inbox=False))
        async def _on_read(event) -> None:  # noqa: ANN001
            if self._read_sink is None:
                return
            chat_id = getattr(event, "chat_id", None)
            if chat_id is None:
                return
            await self._read_sink(account_id, int(chat_id), int(event.max_id))

    async def _forward(
        self, account_id: int, client: TelegramClient, message, live: bool
    ) -> None:  # noqa: ANN001
        if self._sink is None:
            return
        try:
            inbound = await self._to_inbound(client, message, live=live)
        except Exception:
            log.exception(
                "Не разобрал сообщение %s аккаунта %s",
                getattr(message, "id", "?"),
                account_id,
            )
            return
        if inbound is not None:
            await self._sink(account_id, inbound)

    async def _to_inbound(
        self, client: TelegramClient, message, live: bool
    ) -> InboundMessage | None:  # noqa: ANN001
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
        media_kind, attachments = await _read_media(client, message)
        return InboundMessage(
            peer=peer,
            tg_message_id=int(message.id),
            date=message.date.astimezone(UTC),
            text=message.message or None,
            outgoing=bool(message.out),
            random_id=None,
            reply_to_tg_id=int(message.reply_to_msg_id) if message.reply_to_msg_id else None,
            media_kind=media_kind,
            live=live,
            attachments=attachments,
        )

    # ------------------------------------------------------------------- вход

    async def send_code(self, account: TelegramAccount) -> CodeRequest:
        # «Отправить код заново» на уже открытом логин-соединении — это
        # настоящий повтор: Telethon помнит phone_code_hash на самом клиенте
        # и посылает auth.ResendCodeRequest, а не новый auth.SendCodeRequest.
        # Если вместо этого каждый раз собирать клиента заново (новый ключ
        # авторизации), Telegram видит не повтор, а ещё один "новый прибор",
        # заходящий на тот же номер, — с сервера это выглядит как перебор.
        stale_login = self._logins.get(account.id)
        if stale_login is not None and stale_login.is_connected():
            client = stale_login
        else:
            self._logins.pop(account.id, None)
            client = self._build(account, None)
            await client.connect()
        try:
            sent = await client.send_code_request(account.phone)
        except FloodWaitError as exc:
            await client.disconnect()
            self._logins.pop(account.id, None)
            raise RetryAfter(int(exc.seconds)) from exc
        except SendCodeUnavailableError as exc:
            # Повтор уже нечем доставить: Telegram исчерпал каналы для номера.
            # Соединение не рвём — прежний код мог остаться действующим.
            raise LoginRefused(
                "Telegram больше не может отправить код на этот номер: все способы "
                "доставки уже использованы. Код из предыдущего запроса, скорее всего, "
                "ещё действует — найдите его в приложении Telegram на телефоне с этим "
                "номером, в служебном чате «Telegram». Новый код тот же номер сможет "
                "получить через несколько часов."
            ) from exc
        except PhoneNumberBannedError as exc:
            await client.disconnect()
            self._logins.pop(account.id, None)
            raise LoginRefused(
                "Telegram заблокировал этот номер — подключить его нельзя. "
                "Нужен другой номер."
            ) from exc
        except PhoneNumberFloodError as exc:
            await client.disconnect()
            self._logins.pop(account.id, None)
            raise LoginRefused(
                "С этого номера сегодня запрашивали код слишком много раз. "
                "Telegram временно закрыл вход — попробуйте через сутки."
            ) from exc
        except PhoneNumberInvalidError as exc:
            await client.disconnect()
            self._logins.pop(account.id, None)
            raise LoginRefused(
                f"Telegram не знает номер {account.phone}. Проверьте, тот ли это номер "
                "и зарегистрирован ли на нём Telegram."
            ) from exc
        except ApiIdInvalidError as exc:
            await client.disconnect()
            self._logins.pop(account.id, None)
            raise LoginRefused(
                "Telegram отклонил ключи приложения (TELEGRAM_API_ID/TELEGRAM_API_HASH) — "
                "подключение невозможно, пока их не исправят в настройках сервера."
            ) from exc
        # Клиент остаётся жить до подтверждения: код принадлежит этому соединению.
        self._logins[account.id] = client
        # Куда Telegram отправил код и чем готов повторить. Без этого в логе
        # разбор «код не пришёл» упирается в догадки: next_type=None означает,
        # что запасного канала (SMS, звонок) для номера не предложено вовсе.
        log.info(
            "Аккаунт %s: код отправлен через %s, запасной канал %s, таймаут %s",
            account.id,
            type(sent.type).__name__,
            type(sent.next_type).__name__ if sent.next_type else "нет",
            sent.timeout,
        )
        # `sent.type` — один из нескольких классов Telethon (SentCodeTypeApp,
        # SentCodeTypeSms, SentCodeTypeCall, ...); у всех есть CONSTRUCTOR_ID,
        # так что проверять его наличие бессмысленно — нужен именно класс типа.
        return CodeRequest(
            phone_code_hash=sent.phone_code_hash,
            sent_to="app" if isinstance(sent.type, SentCodeTypeApp) else "sms",
        )

    async def confirm_code(
        self,
        account: TelegramAccount,
        code: str,
        phone_code_hash: str,
        password: str | None = None,
    ) -> SessionResult:
        client = self._logins.get(account.id)
        if client is None or not client.is_connected():
            raise SessionLost("Код устарел — запросите новый")
        try:
            if password:
                await client.sign_in(password=password)
            else:
                await client.sign_in(
                    phone=account.phone, code=code, phone_code_hash=phone_code_hash
                )
        except SessionPasswordNeededError:
            # Второй шаг: облачный пароль. Клиент не отпускаем.
            return SessionResult(
                session_string=None, tg_user_id=None, tg_username=None, needs_password=True
            )
        except PhoneCodeInvalidError as exc:
            raise ValueError("Неверный код подтверждения") from exc
        except PhoneCodeExpiredError as exc:
            raise ValueError("Код устарел — запросите новый") from exc
        except FloodWaitError as exc:
            raise RetryAfter(int(exc.seconds)) from exc

        me = await client.get_me()
        session_string = client.session.save()
        self._logins.pop(account.id, None)
        self._register_handlers(account.id, client)
        # Переподключение уже живого аккаунта («Переподключить» на уже
        # CONNECTED): старое соединение нужно закрыть явно, иначе оно просто
        # повисает без ссылок, а Telegram продолжает видеть два одновременных
        # сеанса с одного аккаунта — ровно то, что резко повышает риск
        # блокировки (docs/12-telegram-connect.md, разд. 5).
        old_client = self._clients.get(account.id)
        if old_client is not None and old_client is not client and old_client.is_connected():
            await old_client.disconnect()
        self._clients[account.id] = client
        return SessionResult(
            session_string=session_string,
            tg_user_id=int(me.id),
            tg_username=me.username,
            needs_password=False,
        )

    # --------------------------------------------------------------- отправка

    async def send_message(
        self,
        account: TelegramAccount,
        chat_id: int,
        text: str | None,
        random_id: int,
        attachments: list[dict],
    ) -> SentMessage:
        client = await self._guarded(account)
        entity = await self._entity(client, account, chat_id)
        try:
            files = _as_files(attachments)
            if files:
                sent = await client.send_file(entity, file=files, caption=text or "")
                sent = sent[-1] if isinstance(sent, list) else sent
            else:
                sent = await client.send_message(entity, text or "")
        except FloodWaitError as exc:
            raise RetryAfter(int(exc.seconds)) from exc
        except (AuthKeyUnregisteredError, SessionRevokedError, UserDeactivatedBanError) as exc:
            raise SessionLost(str(exc)) from exc
        except UserIsBlockedError as exc:
            raise ClientBlocked("Клиент заблокировал этот номер") from exc
        return SentMessage(tg_message_id=int(sent.id))

    async def mark_read(self, account: TelegramAccount, chat_id: int, max_id: int) -> None:
        """Менеджер прочитал в CRM — гасим непрочитанное и в самом Telegram,
        иначе владелец аккаунта видит на телефоне вечный счётчик."""
        client = await self._guarded(account)
        entity = await self._entity(client, account, chat_id)
        try:
            await client.send_read_acknowledge(entity, max_id=max_id)
        except FloodWaitError as exc:
            raise RetryAfter(int(exc.seconds)) from exc

    # --------------------------------------------------------------- история

    async def iter_history(
        self, account: TelegramAccount, since: datetime, per_dialog_limit: int = 500
    ) -> AsyncIterator[InboundMessage]:
        """Пройти диалоги аккаунта и отдать переписку не старше `since`.

        Идём от новых к старым и обрываем диалог, как только упёрлись в границу:
        Telegram отдаёт историю страницами, и тянуть всё подряд — это часы
        ожидания и почти гарантированный FloodWait.
        """
        client = await self._guarded(account)
        async for dialog in client.iter_dialogs():
            if not dialog.is_user or dialog.entity.bot:
                continue
            async for message in client.iter_messages(dialog.entity, limit=per_dialog_limit):
                if message.date.astimezone(UTC) < since:
                    break
                inbound = await self._to_inbound(client, message, live=False)
                if inbound is not None:
                    yield inbound
            # Пауза между диалогами: ровный темп дешевле, чем запрет на час.
            await asyncio.sleep(0.4)

    # ------------------------------------------------------------ соединение

    async def start(self, account: TelegramAccount) -> None:
        if account.session_enc is None:
            return
        try:
            await self._connected(account)
            log.info("Аккаунт %s подключён к Telegram", account.id)
        except SessionLost as exc:
            await self._report(account.id, "error", str(exc))
            raise

    def client_for(self, account_id: int) -> TelegramClient | None:
        """Живой клиент аккаунта, если он поднят этим процессом."""
        return self._clients.get(account_id)

    async def stop(self, account: TelegramAccount) -> None:
        client = self._clients.pop(account.id, None)
        if client is not None and client.is_connected():
            await client.disconnect()
        login = self._logins.pop(account.id, None)
        if login is not None and login.is_connected():
            await login.disconnect()

    async def _guarded(self, account: TelegramAccount) -> TelegramClient:
        try:
            return await self._connected(account)
        except (AuthKeyUnregisteredError, SessionRevokedError, UserDeactivatedBanError) as exc:
            await self._report(account.id, "error", str(exc))
            raise SessionLost(str(exc)) from exc

    async def _report(self, account_id: int, status: str, reason: str | None) -> None:
        if self._status_sink is not None:
            await self._status_sink(account_id, status, reason)

    async def _entity(self, client: TelegramClient, account: TelegramAccount, chat_id: int):  # noqa: ANN202
        """Собеседник по сохранённому пропуску. Если хэша нет — просим Telegram,
        но это дороже и работает не всегда, поэтому хэш и хранится в базе."""
        from sqlalchemy import select
        from telethon.tl.types import InputPeerUser

        from app.core.db import SessionLocal
        from app.models import TelegramPeer

        async with SessionLocal() as db:
            peer = await db.scalar(
                select(TelegramPeer).where(
                    TelegramPeer.account_id == account.id, TelegramPeer.tg_user_id == chat_id
                )
            )
        if peer is not None and peer.access_hash is not None:
            return InputPeerUser(user_id=chat_id, access_hash=peer.access_hash)
        return await client.get_input_entity(chat_id)


def _as_files(attachments: list[dict]) -> list[io.BytesIO]:
    """Тело вложения → файлоподобный объект для Telethon.

    `.name` — не декорация: по нему Telethon определяет расширение и решает,
    отправлять как фото/видео или как обычный документ.
    """
    files: list[io.BytesIO] = []
    for item in attachments:
        body = item.get("body")
        if not body:
            continue
        buf = io.BytesIO(body)
        buf.name = item.get("file_name") or "file"
        files.append(buf)
    return files


def _proxy_for(account: TelegramAccount) -> tuple | None:
    """Прокси на аккаунт. Пока задаётся одним значением на установку —
    поаккаунтные адреса появятся вместе с закупкой мобильных прокси (D-21)."""
    raw = settings.telegram_proxy
    if not raw:
        return None
    # Формат: socks5://user:pass@host:port
    from urllib.parse import urlparse

    import python_socks  # noqa: F401  — проверяем, что зависимость на месте

    parsed = urlparse(raw)
    kind = {"socks5": 2, "socks4": 1, "http": 3}.get(parsed.scheme)
    if kind is None or parsed.hostname is None or parsed.port is None:
        log.warning("Прокси задан непонятной строкой, работаю напрямую: %s", parsed.scheme)
        return None
    return (kind, parsed.hostname, parsed.port, True, parsed.username, parsed.password)


async def _read_media(client: TelegramClient, message) -> tuple[str | None, list[dict]]:  # noqa: ANN001
    """Скачать вложение сразу: ссылка Telegram недолговечна, а переписку открывают и через год."""
    if not message.media:
        return None, []

    kind = "document"
    if message.photo:
        kind = "photo"
    elif message.voice:
        kind = "voice"
    elif message.video:
        kind = "video"

    size = getattr(getattr(message, "file", None), "size", 0) or 0
    if size > MAX_INCOMING_BYTES:
        log.info("Файл %s байт слишком велик, сохраняю только упоминание", size)
        return kind, []

    try:
        body = await client.download_media(message, file=bytes)
    except Exception:
        log.exception("Не скачал вложение сообщения %s", message.id)
        return kind, []
    if not body:
        return kind, []

    name = getattr(message.file, "name", None) or f"{kind}-{message.id}{message.file.ext or ''}"
    item: dict = {
        "file_name": name,
        "mime_type": getattr(message.file, "mime_type", None),
        "body": body,
    }
    for attribute in getattr(getattr(message, "document", None), "attributes", []) or []:
        if isinstance(attribute, DocumentAttributeVideo):
            item["duration_sec"] = int(attribute.duration or 0)
            item["width"], item["height"] = attribute.w, attribute.h
        elif isinstance(attribute, DocumentAttributeAudio):
            item["duration_sec"] = int(attribute.duration or 0)
        elif isinstance(attribute, DocumentAttributeFilename):
            item["file_name"] = attribute.file_name
    if message.photo:
        item["width"] = getattr(message.file, "width", None)
        item["height"] = getattr(message.file, "height", None)
    return kind, [item]


async def logout(provider: "MTProtoProvider", account: TelegramAccount) -> None:
    """Полный выход: сессия отзывается на стороне Telegram, а не просто забывается нами.
    Иначе владелец аккаунта увидит в списке устройств чужой сеанс, который никто не гасил."""
    client = provider.client_for(account.id)
    if client is not None:
        try:
            await client(functions.auth.LogOutRequest())
        except Exception as exc:  # noqa: BLE001 — сеть не должна мешать отключению
            log.warning("Выход из аккаунта %s прошёл не полностью: %s", account.id, exc)
    await provider.stop(account)
