"""Абстракция поставщика Telegram.

Один и тот же контракт используют демо-провайдер (эта итерация) и боевой
MTProto-провайдер (следующий этап). Цикл шлюза (`runner.py`) о разнице между
ними не знает — он вызывает только эти методы.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Protocol

from app.core.config import settings
from app.models import TelegramAccount


@dataclass(slots=True)
class CodeRequest:
    """Ответ на запрос кода подтверждения входа."""

    phone_code_hash: str
    sent_to: Literal["app", "sms"]


@dataclass(slots=True)
class QrCode:
    """Картинка QR и момент, после которого она перестаёт действовать.

    Токен входа живёт около полуминуты, поэтому шлюз обновляет его сам, а
    интерфейс переспрашивает состояние и перерисовывает картинку.
    """

    image: str
    expires_at: datetime


@dataclass(slots=True)
class QrState:
    """Что сейчас происходит со входом по QR.

    `waiting`  — ждём сканирования, картинку можно показывать;
    `password` — код отсканировали, но на аккаунте стоит облачный пароль;
    `done`     — вошли, сессия уже зашифрована и сохранена шлюзом;
    `error`    — вход не состоялся, причина в `message`.
    """

    status: Literal["waiting", "password", "done", "error"]
    image: str | None = None
    expires_at: datetime | None = None
    message: str | None = None


@dataclass(slots=True)
class SessionResult:
    """Ответ на подтверждение кода.

    needs_password=True — нужен второй шаг (облачный пароль), поле session_string
    и остальные при этом пустые: сессия ещё не создана.
    """

    session_string: str | None
    tg_user_id: int | None
    tg_username: str | None
    needs_password: bool = False


@dataclass(slots=True)
class SentMessage:
    """Ответ на отправку исходящего сообщения."""

    tg_message_id: int


class TelegramProvider(Protocol):
    """Контракт поставщика: вход в аккаунт и отправка сообщений."""

    async def send_code(self, account: TelegramAccount) -> CodeRequest: ...

    async def confirm_code(
        self,
        account: TelegramAccount,
        code: str,
        phone_code_hash: str,
        password: str | None = None,
    ) -> SessionResult: ...

    async def send_message(
        self,
        account: TelegramAccount,
        chat_id: int,
        text: str | None,
        random_id: int,
        attachments: list[dict[str, Any]],
    ) -> SentMessage: ...

    async def start(self, account: TelegramAccount) -> None:
        """Поднять сессию: для MTProto — подключить клиента. Вызывается при получении аренды."""
        ...

    async def stop(self, account: TelegramAccount) -> None:
        """Остановить сессию: вызывается при освобождении аренды и на остановке процесса."""
        ...

    def set_sinks(
        self,
        sink: Any,
        read_sink: Any = None,
        status_sink: Any = None,
        session_sink: Any = None,
    ) -> None:
        """Куда отдавать полученное из Telegram: входящие, отметки о прочтении,
        смену состояния сессии, а также готовую сессию после входа по QR.
        Записью в базу занимается шлюз, не провайдер."""
        ...

    async def mark_read(self, account: TelegramAccount, chat_id: int, max_id: int) -> None:
        """Погасить непрочитанное в самом Telegram, когда менеджер прочитал в CRM."""
        ...

    async def edit_message(
        self, account: TelegramAccount, chat_id: int, tg_message_id: int, text: str
    ) -> None:
        """Изменить текст уже отправленного сообщения в самом Telegram."""
        ...

    def iter_history(
        self, account: TelegramAccount, since: Any, per_dialog_limit: int = 500
    ) -> Any:
        """Асинхронный обход переписки не старше `since` — для первой подтяжки."""
        ...

    async def fetch_birthday(
        self, account: TelegramAccount, tg_user_id: int
    ) -> tuple[int, int, int | None] | None:
        """День, месяц и (если открыт) год рождения из полного профиля собеседника.

        Лучшее из возможного: `None`, если скрыто приватностью, аккаунт не
        подключён или запрос не удался — вызывающий не должен из-за этого
        останавливать приём сообщений."""
        ...


_provider: TelegramProvider | None = None


def get_provider() -> TelegramProvider:
    """В демо-режиме — рабочая имитация. Иначе — честный отказ, а не тихая заглушка:
    подключение к настоящему Telegram появится на следующем этапе."""
    global _provider
    if _provider is not None:
        return _provider

    if settings.demo_mode:
        from app.gateway.demo_provider import DemoProvider

        _provider = DemoProvider()
        return _provider

    from app.gateway.mtproto_provider import MTProtoProvider

    _provider = MTProtoProvider()
    return _provider
