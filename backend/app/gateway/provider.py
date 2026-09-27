"""Абстракция поставщика Telegram.

Один и тот же контракт используют демо-провайдер (эта итерация) и боевой
MTProto-провайдер (следующий этап). Цикл шлюза (`runner.py`) о разнице между
ними не знает — он вызывает только эти методы.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Protocol

from app.core.config import settings
from app.gateway.send_plan import OutgoingFile
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
    """Ответ на отправку исходящего сообщения.

    Одно сообщение CRM может стать несколькими в Telegram (альбом, длинный
    текст отдельно от файла). `tg_message_id` — последнее из них, остальные —
    в `extra_ids`: по ним подтяжка истории узнаёт своё и не задваивает.
    Пусто — Telegram подтвердил, что это уже было отправлено раньше, а номер
    восстановить не удалось: сообщение у клиента есть, дубля нет.
    """

    tg_message_id: int | None
    extra_ids: list[int] = field(default_factory=list)


class PermanentFailure(RuntimeError):
    """Отправка не получится ни сейчас, ни через минуту — повторять бессмысленно.

    Текст уходит менеджеру под сообщением: «клиент запретил голосовые»,
    «аккаунт клиента удалён» — то, что можно исправить только иначе.
    """


class ForwardImpossible(RuntimeError):
    """Настоящая пересылка Telegram невозможна (исходное сообщение удалено,
    пересылка запрещена) — шлюз отправит копию из файлов CRM."""


class MediaGone(RuntimeError):
    """Файл больше не достать из Telegram: сообщение удалено или недоступно."""


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
        attachments: list[OutgoingFile],
    ) -> SentMessage: ...

    async def forward_messages(
        self,
        account: TelegramAccount,
        to_chat_id: int,
        from_chat_id: int,
        tg_message_ids: list[int],
        drop_author: bool,
        random_id: int,
    ) -> list[int]:
        """Настоящая пересылка Telegram внутри одного аккаунта. Номера новых
        сообщений — в порядке исходных."""
        ...

    async def download_media(
        self, account: TelegramAccount, chat_id: int, tg_message_id: int, path: str
    ) -> int:
        """Докачать файл сообщения на диск. Возвращает размер в байтах."""
        ...

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
        delete_sink: Any = None,
        inbox_read_sink: Any = None,
    ) -> None:
        """Куда отдавать полученное из Telegram: входящие и правки, отметки о
        прочтении (клиентом — наших, нами с телефона — его), удаления, смену
        состояния сессии, а также готовую сессию после входа по QR.
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
