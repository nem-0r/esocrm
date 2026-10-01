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
import contextlib
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from cryptography.exceptions import InvalidTag
from telethon import TelegramClient, events, functions
from telethon.errors import (
    ApiIdInvalidError,
    AuthKeyUnregisteredError,
    ChatForwardsRestrictedError,
    FloodWaitError,
    MessageEditTimeExpiredError,
    MessageIdInvalidError,
    MessageIdsEmptyError,
    MessageNotModifiedError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    PhoneNumberBannedError,
    PhoneNumberFloodError,
    PhoneNumberInvalidError,
    RPCError,
    SendCodeUnavailableError,
    SessionPasswordNeededError,
    SessionRevokedError,
    UserDeactivatedBanError,
    UserIsBlockedError,
)
from telethon.sessions import StringSession
from telethon.tl.types.auth import SentCodeTypeApp

from app.core import crypto
from app.core import qr as qr_image
from app.core.config import settings
from app.gateway import mtproto_receive, mtproto_send
from app.gateway.provider import (
    CodeRequest,
    ForwardImpossible,
    MediaGone,
    PermanentFailure,
    QrCode,
    QrState,
    SentMessage,
    SessionResult,
)
from app.gateway.send_plan import OutgoingFile
from app.gateway.send_plan import plan as make_plan
from app.models import TelegramAccount
from app.services.inbound_service import InboundMessage

log = logging.getLogger("astra.mtproto")

Sink = Callable[[int, InboundMessage], Awaitable[None]]
ReadSink = Callable[[int, int, int], Awaitable[None]]
StatusSink = Callable[[int, str, str | None], Awaitable[None]]
SessionSink = Callable[[int, "SessionResult"], Awaitable[None]]
DeleteSink = Callable[[int, int | None, list[int]], Awaitable[None]]

# Ошибки Telegram, которые повтор не исправит, — и что сказать менеджеру.
# Коды — из текста ошибки RPC: часть из них у Telethon без отдельного класса.
PERMANENT_ERRORS: dict[str, str] = {
    "VOICE_MESSAGES_FORBIDDEN": (
        "Клиент запретил получать голосовые сообщения — отправьте текстом или файлом"
    ),
    "PRIVACY_PREMIUM_REQUIRED": (
        "Клиент принимает сообщения только от контактов или от Telegram Premium"
    ),
    "YOU_BLOCKED_USER": (
        "Этот аккаунт сам заблокировал клиента в Telegram — разблокируйте его в приложении"
    ),
    "INPUT_USER_DEACTIVATED": "Аккаунт клиента удалён в Telegram",
    "USER_DEACTIVATED": "Аккаунт клиента удалён в Telegram",
    "PEER_ID_INVALID": "Telegram не находит этого клиента — возможно, его аккаунт удалён",
    "CHAT_WRITE_FORBIDDEN": "Telegram запрещает писать в этот чат",
    "USER_PRIVACY_RESTRICTED": "Настройки приватности клиента не позволяют это отправить",
    "MESSAGE_EMPTY": "Сообщение пустое — отправлять нечего",
    "MEDIA_EMPTY": "Файл пустой или повреждён",
}


def permanent_reason(exc: RPCError) -> str | None:
    return PERMANENT_ERRORS.get(str(getattr(exc, "message", "") or "").upper())

# Сколько всего ждём сканирования, прежде чем закрыть попытку. Сам токен живёт
# около полуминуты и обновляется на месте — это предел на весь вход целиком,
# чтобы забытое открытым окно не держало соединение с Telegram вечно.
QR_TOTAL_SECONDS = 300.0


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


def _qr_window(qr: Any) -> float:
    """Сколько ждать текущий токен, прежде чем выпустить новый.

    Берём срок жизни, который назвал сам Telegram, но не доверяем ему вслепую:
    ноль или отрицательное значение (рассинхрон часов) превратили бы ожидание
    в busy-loop, а слишком долгое — заставило бы показывать мёртвую картинку.
    """
    left = (qr.expires - datetime.now(UTC)).total_seconds()
    return max(5.0, min(left, 60.0))


@dataclass
class _QrSession:
    """Незавершённый вход по QR: соединение, токен и то, что уже про него известно.

    Живёт в памяти шлюза от «показали код» до «вошли». Соединение здесь не
    случайно: `QRLogin.wait()` обязан выполняться именно в тот момент, когда
    пользователь наводит камеру, — иначе вход не завершится.
    """

    client: TelegramClient
    qr: Any
    status: str = "waiting"
    image: str | None = None
    expires_at: datetime | None = None
    message: str | None = None
    task: asyncio.Task | None = None
    # Чей это аккаунт в Telegram по нашим записям. Пусто — аккаунт подключают впервые.
    expected_user_id: int | None = None

    def refresh(self) -> None:
        """Перечитать токен после создания или обновления."""
        self.image = qr_image.login_qr_data_uri(self.qr.url)
        self.expires_at = self.qr.expires


class MTProtoProvider:
    """Держит по клиенту на арендованный аккаунт."""

    def __init__(self) -> None:
        self._clients: dict[int, TelegramClient] = {}
        self._logins: dict[int, TelegramClient] = {}
        self._qr: dict[int, _QrSession] = {}
        self._sink: Sink | None = None
        self._read_sink: ReadSink | None = None
        self._status_sink: StatusSink | None = None
        self._session_sink: SessionSink | None = None
        self._delete_sink: DeleteSink | None = None
        self._inbox_read_sink: ReadSink | None = None
        # Лок на аккаунт, не один на весь процесс: иначе подключение одного
        # (медленная сеть, Telegram не спешит отвечать) держит взаперти
        # send_message/mark_read/подтяжку истории для ВСЕХ остальных
        # аккаунтов этого шлюза, хотя их сессии друг от друга не зависят.
        self._connect_locks: dict[int, asyncio.Lock] = {}

    # ------------------------------------------------------------------ приём

    def set_sinks(
        self,
        sink: Sink,
        read_sink: ReadSink | None = None,
        status_sink: StatusSink | None = None,
        session_sink: SessionSink | None = None,
        delete_sink: DeleteSink | None = None,
        inbox_read_sink: ReadSink | None = None,
    ) -> None:
        """Куда отдавать полученное. Записью в базу занимается шлюз, а не провайдер:
        здесь только Telegram, чтобы эту часть можно было проверять отдельно."""
        self._sink = sink
        self._read_sink = read_sink
        self._status_sink = status_sink
        self._session_sink = session_sink
        self._delete_sink = delete_sink
        self._inbox_read_sink = inbox_read_sink

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
        lock = self._connect_locks.setdefault(account.id, asyncio.Lock())
        async with lock:
            # Пока ждали лок, аккаунт мог подключить другой одновременный
            # вызов — тогда просто отдаём готовый клиент, а не подключаемся
            # заново поверх него.
            client = self._clients.get(account.id)
            if client is not None and client.is_connected():
                return client
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
            await self._forward(account_id, client, event.message, live=True, is_edit=True)

        @client.on(events.MessageRead(inbox=False))
        async def _on_read(event) -> None:  # noqa: ANN001
            # Клиент прочитал наши сообщения — две галочки в CRM.
            if self._read_sink is None:
                return
            chat_id = getattr(event, "chat_id", None)
            if chat_id is None:
                return
            await self._read_sink(account_id, int(chat_id), int(event.max_id))

        @client.on(events.MessageRead(inbox=True))
        async def _on_read_inbox(event) -> None:  # noqa: ANN001
            # Сообщения клиента прочитали на телефоне — в CRM они тоже прочитаны.
            if self._inbox_read_sink is None:
                return
            chat_id = getattr(event, "chat_id", None)
            if chat_id is None:
                return
            await self._inbox_read_sink(account_id, int(chat_id), int(event.max_id))

        @client.on(events.MessageDeleted())
        async def _on_deleted(event) -> None:  # noqa: ANN001
            # В личных чатах Telegram не говорит, из какого чата удалено: номера
            # сообщений уникальны в пределах аккаунта, этого достаточно.
            if self._delete_sink is None:
                return
            ids = [int(value) for value in (getattr(event, "deleted_ids", None) or [])]
            if not ids:
                return
            chat_id = getattr(event, "chat_id", None)
            await self._delete_sink(account_id, int(chat_id) if chat_id else None, ids)

    async def _forward(
        self,
        account_id: int,
        client: TelegramClient,
        message,  # noqa: ANN001
        live: bool,
        is_edit: bool = False,
    ) -> None:
        if self._sink is None:
            return
        try:
            inbound = await mtproto_receive.to_inbound(
                client, message, account_id, live=live, is_edit=is_edit
            )
        except Exception:
            log.exception(
                "Не разобрал сообщение %s аккаунта %s",
                getattr(message, "id", "?"),
                account_id,
            )
            return
        if inbound is not None:
            await self._sink(account_id, inbound)

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

    # -------------------------------------------------------------- вход по QR

    async def qr_start(self, account: TelegramAccount) -> QrCode:
        """Начать вход по QR: создать токен и сесть ждать сканирования.

        Ожидание уходит в фоновую задачу не для скорости, а по требованию
        протокола: Telegram сообщает об успешном сканировании входящим
        обновлением, и поймать его может только соединение, которое в этот
        момент слушает. Дождаться в рамках одного запроса нельзя — человеку
        нужны минуты, чтобы взять телефон.
        """
        await self._qr_drop(account.id)
        client = self._build(account, None)
        await client.connect()
        try:
            qr = await client.qr_login()
        except Exception:
            await client.disconnect()
            raise
        session = _QrSession(client=client, qr=qr, expected_user_id=account.tg_user_id)
        session.refresh()
        self._qr[account.id] = session
        session.task = asyncio.create_task(self._qr_wait(account.id, session))
        return QrCode(image=session.image or "", expires_at=qr.expires)

    def qr_state(self, account: TelegramAccount) -> QrState:
        """Что сейчас со входом. Интерфейс спрашивает это раз в пару секунд."""
        session = self._qr.get(account.id)
        if session is None:
            return QrState(status="error", message="Вход по QR не начат — откройте его заново")
        return QrState(
            status=session.status,  # type: ignore[arg-type]
            image=session.image,
            expires_at=session.expires_at,
            message=session.message,
        )

    async def qr_password(self, account: TelegramAccount, password: str) -> SessionResult:
        """Второй шаг, когда на аккаунте включён облачный пароль."""
        session = self._qr.get(account.id)
        if session is None or not session.client.is_connected():
            raise SessionLost("Вход по QR устарел — покажите код заново")
        await session.client.sign_in(password=password)
        return await self._qr_finish(account.id, session)

    async def _qr_wait(self, account_id: int, session: _QrSession) -> None:
        """Ждать сканирования, обновляя протухший токен на месте."""
        deadline = asyncio.get_running_loop().time() + QR_TOTAL_SECONDS
        try:
            while asyncio.get_running_loop().time() < deadline:
                try:
                    await session.qr.wait(timeout=_qr_window(session.qr))
                except TimeoutError:
                    # Токен истёк, а человек ещё не отсканировал: берём новый и
                    # продолжаем ждать. Для интерфейса это просто новая картинка.
                    await session.qr.recreate()
                    session.refresh()
                    continue
                except SessionPasswordNeededError:
                    session.status = "password"
                    return
                await self._qr_finish(account_id, session)
                return
            session.status = "error"
            session.message = "Время на сканирование вышло — покажите код заново"
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — причина уходит руководителю на экран
            log.exception("Вход по QR для аккаунта %s не удался", account_id)
            session.status = "error"
            session.message = str(exc)
        finally:
            # Брошенная попытка не должна держать соединение с Telegram: при
            # успехе оно уже стало рабочим клиентом аккаунта, при ожидании
            # пароля ещё понадобится, а в остальных случаях лишнее.
            if session.status == "error" and session.client.is_connected():
                with contextlib.suppress(Exception):
                    await session.client.disconnect()

    async def _qr_finish(self, account_id: int, session: _QrSession) -> SessionResult:
        """Вход состоялся: соединение становится рабочим клиентом аккаунта."""
        client = session.client
        me = await client.get_me()
        await self._ensure_same_user(session.expected_user_id, client, me)
        result = SessionResult(
            session_string=client.session.save(),
            tg_user_id=int(me.id),
            tg_username=me.username,
            needs_password=False,
        )
        self._register_handlers(account_id, client)
        old_client = self._clients.get(account_id)
        if old_client is not None and old_client is not client and old_client.is_connected():
            await old_client.disconnect()
        self._clients[account_id] = client
        session.status = "done"
        session.message = None
        # Сохранение не ждёт, пока интерфейс переспросит состояние: человек
        # может закрыть окно сразу после сканирования, а сессия уже выдана —
        # потерять её значит оставить в Telegram висящий чужой сеанс.
        if self._session_sink is not None:
            await self._session_sink(account_id, result)
        return result

    async def _qr_drop(self, account_id: int) -> None:
        """Убрать прошлую незавершённую попытку входа по QR."""
        session = self._qr.pop(account_id, None)
        if session is None:
            return
        if session.task is not None and not session.task.done():
            session.task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await session.task
        # Успешный вход уже отдал соединение в рабочие клиенты — рвать нельзя.
        if session.status != "done" and session.client.is_connected():
            await session.client.disconnect()

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
        try:
            await self._ensure_same_user(account.tg_user_id, client, me)
        except ValueError:
            self._logins.pop(account.id, None)
            raise
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

    async def _ensure_same_user(
        self, expected: int | None, client: TelegramClient, me: Any
    ) -> None:
        """Переподключение — это вход в ТОТ ЖЕ аккаунт Telegram, что был.

        Если ввели код или отсканировали QR из другого аккаунта, чаты и клиенты в CRM
        остались бы привязаны к чужому человеку, а ответы менеджеров уходили бы от его
        имени (и не доходили бы: ключи доступа к собеседникам у каждого аккаунта свои).
        Поэтому вход отклоняется, а только что выданную сессию мы сразу отзываем в
        Telegram — чужого сеанса «висеть» не должно.
        """
        if expected is None or int(me.id) == int(expected):
            return
        with contextlib.suppress(Exception):
            await client.log_out()
        with contextlib.suppress(Exception):
            await client.disconnect()
        who = f"@{me.username}" if getattr(me, "username", None) else f"id {me.id}"
        raise ValueError(
            f"Вы вошли в другой аккаунт Telegram ({who}), а нужен тот же, что был подключён. "
            "Войдите именно в него. Если нужен другой аккаунт — подключите его отдельно, "
            "новой записью. Вход отменён, ничего не изменилось."
        )

    # --------------------------------------------------------------- отправка

    async def send_message(
        self,
        account: TelegramAccount,
        chat_id: int,
        text: str | None,
        random_id: int,
        attachments: list[OutgoingFile],
    ) -> SentMessage:
        """Отправить сообщение CRM: текст, голосовое, альбомы, документы.

        Как разбить на отправки — `send_plan.plan`, как отправить —
        `mtproto_send.execute`. Здесь — соединение и перевод ошибок Telegram
        в понятные очереди исходящих."""
        client = await self._guarded(account)
        entity = await self._entity(client, account, chat_id)
        send_plan = make_plan(text, attachments)
        try:
            ids = await mtproto_send.execute(
                client,
                entity,
                send_plan,
                random_id,
                recover=self._recoverer(client, account, chat_id, entity),
            )
        except BaseException as exc:
            raise self._translate(exc) from exc
        if not ids:
            return SentMessage(tg_message_id=None, extra_ids=[])
        return SentMessage(tg_message_id=ids[-1], extra_ids=ids[:-1])

    async def forward_messages(
        self,
        account: TelegramAccount,
        to_chat_id: int,
        from_chat_id: int,
        tg_message_ids: list[int],
        drop_author: bool,
        random_id: int,
    ) -> list[int]:
        """Настоящая пересылка Telegram внутри одного аккаунта."""
        client = await self._guarded(account)
        to_peer = await self._entity(client, account, to_chat_id)
        from_peer = await self._entity(client, account, from_chat_id)
        try:
            return await mtproto_send.forward(
                client,
                to_peer,
                from_peer,
                tg_message_ids,
                drop_author=drop_author,
                base_random_id=random_id,
                recover=self._recoverer(client, account, to_chat_id, to_peer),
            )
        except (ChatForwardsRestrictedError, MessageIdInvalidError, MessageIdsEmptyError) as exc:
            raise ForwardImpossible(str(exc)) from exc
        except BaseException as exc:
            raise self._translate(exc) from exc

    async def download_media(
        self, account: TelegramAccount, chat_id: int, tg_message_id: int, path: str
    ) -> int:
        """Докачать файл сообщения на диск — для больших файлов и повторов."""
        client = await self._guarded(account)
        entity = await self._entity(client, account, chat_id)
        try:
            message = await client.get_messages(entity, ids=tg_message_id)
        except FloodWaitError as exc:
            raise RetryAfter(int(exc.seconds)) from exc
        if message is None or getattr(message, "media", None) is None:
            raise MediaGone("Сообщение удалено в Telegram — файла больше нет")
        try:
            result = await client.download_media(message, file=path)
        except FloodWaitError as exc:
            raise RetryAfter(int(exc.seconds)) from exc
        size = mtproto_send.local_size(path)
        if not result or size == 0:
            raise MediaGone("Telegram не отдал файл")
        return size

    def _translate(self, exc: BaseException) -> BaseException:
        """Ошибка Telegram → то, что понимает очередь исходящих."""
        if isinstance(exc, FloodWaitError):
            return RetryAfter(int(exc.seconds))
        if isinstance(
            exc, (AuthKeyUnregisteredError, SessionRevokedError, UserDeactivatedBanError)
        ):
            return SessionLost(str(exc))
        if isinstance(exc, UserIsBlockedError):
            return ClientBlocked("Клиент заблокировал этот номер")
        if isinstance(exc, RPCError) and (reason := permanent_reason(exc)):
            return PermanentFailure(reason)
        return exc

    def _recoverer(
        self, client: TelegramClient, account: TelegramAccount, chat_id: int, entity: Any
    ) -> mtproto_send.RecoverIds:
        """Как узнать номера уже отправленного раньше шага (RANDOM_ID_DUPLICATE):
        последние исходящие в диалоге, которых ещё не знает CRM."""

        async def recover(count: int, already: list[int]) -> list[int]:
            try:
                recent = await client.get_messages(entity, limit=max(20, count * 3))
            except Exception:  # noqa: BLE001 — не узнали, значит не узнали: дубля всё равно нет
                return []
            known = await _known_tg_ids(account.id, chat_id)
            fresh = sorted(
                int(item.id)
                for item in recent
                if getattr(item, "out", False)
                and int(item.id) not in known
                and int(item.id) not in already
            )
            return fresh[-count:] if count > 0 else []

        return recover

    async def mark_read(self, account: TelegramAccount, chat_id: int, max_id: int) -> None:
        """Менеджер прочитал в CRM — гасим непрочитанное и в самом Telegram,
        иначе владелец аккаунта видит на телефоне вечный счётчик."""
        client = await self._guarded(account)
        entity = await self._entity(client, account, chat_id)
        try:
            await client.send_read_acknowledge(entity, max_id=max_id)
        except FloodWaitError as exc:
            raise RetryAfter(int(exc.seconds)) from exc

    async def edit_message(
        self, account: TelegramAccount, chat_id: int, tg_message_id: int, text: str
    ) -> None:
        """Изменить текст уже отправленного сообщения в самом Telegram."""
        client = await self._guarded(account)
        entity = await self._entity(client, account, chat_id)
        try:
            # Без разметки — как и при отправке: клиент видит ровно тот текст,
            # что в CRM.
            await client.edit_message(entity, tg_message_id, text, parse_mode=None)
        except MessageNotModifiedError:
            # Текст не поменялся с точки зрения Telegram — не ошибка.
            return
        except MessageEditTimeExpiredError as exc:
            raise ValueError(
                "Telegram больше не разрешает редактировать это сообщение — прошло больше 48 часов"
            ) from exc
        except MessageIdInvalidError as exc:
            raise ValueError("Сообщение не найдено в Telegram — возможно, его удалили") from exc
        except FloodWaitError as exc:
            raise RetryAfter(int(exc.seconds)) from exc
        except (AuthKeyUnregisteredError, SessionRevokedError, UserDeactivatedBanError) as exc:
            raise SessionLost(str(exc)) from exc

    async def fetch_birthday(
        self, account: TelegramAccount, tg_user_id: int
    ) -> tuple[int, int, int | None] | None:
        """Один запрос полного профиля — день рождения есть только там, не в
        лёгком User из потока сообщений. Лучшее из возможного: любая осечка
        (приватность закрыта, аккаунт сейчас не поднят, флуд-контроль) —
        просто «не узнали», а не повод останавливать приём сообщений."""
        client = self.client_for(account.id)
        if client is None:
            return None
        try:
            entity = await self._entity(client, account, tg_user_id)
            full = await client(functions.users.GetFullUserRequest(entity))
        except Exception:
            log.info(
                "Дата рождения аккаунта %s недоступна для клиента %s", account.id, tg_user_id
            )
            return None
        birthday = getattr(full.full_user, "birthday", None)
        if birthday is None:
            return None
        return birthday.day, birthday.month, birthday.year

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
            try:
                async for message in client.iter_messages(dialog.entity, limit=per_dialog_limit):
                    if message.date.astimezone(UTC) < since:
                        break
                    try:
                        inbound = await mtproto_receive.to_inbound(
                            client, message, account.id, live=False
                        )
                    except (AuthKeyUnregisteredError, SessionRevokedError, UserDeactivatedBanError):
                        # Сессия отвалилась совсем — дальше нечем ходить ни по
                        # этому, ни по остальным диалогам. Пробрасываем вместо
                        # того, чтобы широкий except ниже принял разрыв связи
                        # за «не разобрал одно сообщение» и молча пошёл дальше.
                        raise
                    except Exception:
                        # Один непонятый тип сообщения не должен стоить всей
                        # оставшейся истории: без этой защиты подтяжка обрывалась
                        # целиком, и все диалоги, что шли в очереди дальше, вообще
                        # не открывались — ровно то, что уже произошло на живом
                        # аккаунте (`AttributeError` в `_read_media`).
                        log.exception(
                            "Не разобрал сообщение %s аккаунта %s при подтяжке истории",
                            getattr(message, "id", "?"),
                            account.id,
                        )
                        continue
                    if inbound is not None:
                        yield inbound
            except (AuthKeyUnregisteredError, SessionRevokedError, UserDeactivatedBanError):
                raise
            except FloodWaitError as exc:
                # Тот же принцип, что и для одного сообщения, но уровнем выше:
                # один диалог, за который Telegram попросил подождать дольше,
                # чем Telethon готов терпеть молча (`flood_sleep_threshold`),
                # не должен останавливать подтяжку по всем диалогам, что идут
                # в очереди дальше — просто дойдём до него следующим прогоном.
                log.warning(
                    "Диалог %s аккаунта %s пропущен: Telegram просит подождать %s с",
                    getattr(dialog, "id", "?"),
                    account.id,
                    exc.seconds,
                )
                continue
            except Exception:
                log.exception(
                    "Диалог %s аккаунта %s пропущен при подтяжке истории",
                    getattr(dialog, "id", "?"),
                    account.id,
                )
                continue
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
        # Незавершённый вход по QR снимаем первым: он держит и фоновую задачу,
        # и отдельное соединение с Telegram, о которых `_clients` ничего не знает.
        await self._qr_drop(account.id)
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


async def _known_tg_ids(account_id: int, chat_id: int) -> set[int]:
    """Номера Telegram, которые CRM уже знает в этом диалоге (последние 300)."""
    from sqlalchemy import select

    from app.core.db import SessionLocal
    from app.models import Conversation, Message

    async with SessionLocal() as db:
        rows = await db.execute(
            select(Message.tg_message_id, Message.tg_extra_ids)
            .join(Conversation, Conversation.id == Message.conversation_id)
            .where(Conversation.account_id == account_id, Conversation.tg_chat_id == chat_id)
            .order_by(Message.created_at.desc())
            .limit(300)
        )
        known: set[int] = set()
        for tg_id, extra in rows.all():
            if tg_id:
                known.add(int(tg_id))
            known.update(int(value) for value in (extra or []))
    return known


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
