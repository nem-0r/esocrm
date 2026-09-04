"""Демо-провайдер: имитирует MTProto без реального Telegram.

Без хранения состояния (процесс шлюза одноразовый — см. docs/07-architecture.md
§1), поэтому `phone_code_hash` не запоминается, а вычисляется детерминированно
из аккаунта: подтверждение с чужим или устаревшим хешем отклоняется так же,
как отклонился бы просроченный код у настоящего Telegram.
"""

import asyncio
import hashlib
import itertools
import secrets
import time
from typing import Any

from app.core.errors import Invalid
from app.gateway.provider import CodeRequest, SentMessage, SessionResult
from app.models import TelegramAccount

_SEND_DELAY_SECONDS = 0.4
_CODE_DELAY_SECONDS = 0.15
# Монотонный счётчик с разным стартом на каждый запуск процесса — id сообщений
# не повторяются в пределах жизни контейнера, коллизии в демо не встречаются.
_tg_message_ids = itertools.count(int(time.time()))


def _phone_code_hash(account: TelegramAccount) -> str:
    raw = f"demo:{account.id}:{account.phone}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _fake_tg_user_id(account: TelegramAccount) -> int:
    return 900_000_000 + account.id


class DemoProvider:
    """Рабочая имитация входа и отправки — для тестирования интерфейса без Telegram.

    Код подтверждения — любые 5 цифр. Номер, оканчивающийся на «9», один раз
    требует облачный пароль (любое непустое значение) — так проверяется
    двухшаговый сценарий входа на макете.
    """

    async def start(self, account: TelegramAccount) -> None:
        # Демо-сессии не держат соединений — поднимать нечего.
        return

    async def stop(self, account: TelegramAccount) -> None:
        return

    async def send_code(self, account: TelegramAccount) -> CodeRequest:
        await asyncio.sleep(_CODE_DELAY_SECONDS)
        return CodeRequest(phone_code_hash=_phone_code_hash(account), sent_to="app")

    async def confirm_code(
        self,
        account: TelegramAccount,
        code: str,
        phone_code_hash: str,
        password: str | None = None,
    ) -> SessionResult:
        await asyncio.sleep(_CODE_DELAY_SECONDS)
        if phone_code_hash != _phone_code_hash(account):
            raise Invalid("Неверный код")
        if not code or not code.isdigit() or len(code) != 5:
            raise Invalid("Неверный код")

        # Тестовый сценарий двухшагового входа: номер оканчивается на «9».
        needs_password = account.phone.endswith("9")
        if needs_password and password is None:
            return SessionResult(
                session_string=None, tg_user_id=None, tg_username=None, needs_password=True
            )
        if needs_password and not password.strip():
            raise Invalid("Неверный облачный пароль")

        return SessionResult(
            session_string=f"demo-session-{account.id}-{secrets.token_hex(8)}",
            tg_user_id=_fake_tg_user_id(account),
            tg_username=f"demo_{account.id}",
            needs_password=False,
        )

    async def send_message(
        self,
        account: TelegramAccount,
        chat_id: int,
        text: str | None,
        random_id: int,
        attachments: list[dict[str, Any]],
    ) -> SentMessage:
        await asyncio.sleep(_SEND_DELAY_SECONDS)
        return SentMessage(tg_message_id=next(_tg_message_ids))

    # --------------------------------------------------- приём и история

    def set_sinks(self, sink, read_sink=None, status_sink=None) -> None:  # noqa: ANN001
        """Те же приёмники, что и у боевого провайдера: путь входящего сообщения
        в базу проверяется целиком ещё до подключения настоящего Telegram."""
        self._sink = sink
        self._read_sink = read_sink
        self._status_sink = status_sink

    async def deliver(self, account: TelegramAccount, event: Any) -> None:
        """Вбросить входящее событие так, будто оно пришло из Telegram."""
        if getattr(self, "_sink", None) is None:
            raise Invalid("Приёмник входящих не подключён")
        await self._sink(account.id, event)

    async def mark_read(self, account: TelegramAccount, chat_id: int, max_id: int) -> None:
        await asyncio.sleep(0)

    async def iter_history(
        self, account: TelegramAccount, since, per_dialog_limit: int = 500
    ):  # noqa: ANN001, ANN201
        """История демо-аккаунта: переписка уже лежит в базе после `make seed`,
        поэтому подтягивать нечего. Метод есть, чтобы контракт совпадал с боевым
        и шлюз не расходился между режимами."""
        return
        yield  # pragma: no cover — превращает функцию в генератор
