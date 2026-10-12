"""Доставка сообщений и связь аккаунта (D-36, D-37, замечание BUG-01).

Без базы и сети: проверяется то, что решается чистой логикой —

- какие статусы аккаунта считаются «без связи» и что отвечает отправка;
- как строка списка чатов получает направление и статус последнего сообщения;
- что `SessionLost` в очереди исходящих — окончательная ошибка, без повторов;
- что уведомление `account_error` создаётся один раз на переход в `error`.
"""

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from telethon.errors import (
    AuthKeyUnregisteredError,
    FloodWaitError,
    SessionRevokedError,
    UserDeactivatedBanError,
)

from app.core.errors import Conflict
from app.gateway import handlers, outbox
from app.gateway.mtproto_provider import (
    ClientBlocked,
    MTProtoProvider,
    RetryAfter,
    SessionLost,
    session_lost_reason,
)
from app.models import (
    AccountStatus,
    Client,
    Conversation,
    Direction,
    FunnelStage,
    Message,
    MessageKind,
    MessageStatus,
    Notification,
    NotificationKind,
    Outbox,
    OutboxStatus,
    TelegramAccount,
)
from app.schemas.conversation import AccountBrief, ConversationRow
from app.services import account_link, conversation_service, notification_service

NOW = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)


# --- связь аккаунта ----------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "linked"),
    [
        (AccountStatus.CONNECTED, True),
        (AccountStatus.ERROR, False),
        (AccountStatus.DISCONNECTED, False),
        # Вход не завершён или сессия отозвана: сессии нет, слать некуда.
        (AccountStatus.PENDING, False),
    ],
)
def test_account_has_link_only_when_connected(status: AccountStatus, linked: bool) -> None:
    assert account_link.has_link(status) is linked


def test_no_link_error_is_409_with_readable_russian_text() -> None:
    error = account_link.no_link_error(7, AccountStatus.ERROR)
    assert isinstance(error, Conflict)
    assert error.status_code == 409
    assert error.code == "account_offline"
    assert error.message == (
        "Аккаунт без связи — сообщение не отправлено. Сообщите руководителю"
    )
    # Интерфейс по коду и подробностям отличает «нет связи» от прочих конфликтов.
    assert error.details == {"account_id": 7, "account_status": "error"}


def test_no_link_error_tells_admin_what_to_do_instead_of_asking_himself() -> None:
    error = account_link.no_link_error(7, AccountStatus.DISCONNECTED, for_admin=True)
    assert error.status_code == 409
    assert error.code == "account_offline"
    assert "Сообщите руководителю" not in error.message
    assert "Профиль → Аккаунты" in error.message
    assert error.details["account_status"] == "disconnected"


@pytest.mark.parametrize(
    ("previous", "new", "expected"),
    [
        (AccountStatus.CONNECTED, AccountStatus.ERROR, True),
        (AccountStatus.PENDING, AccountStatus.ERROR, True),
        (AccountStatus.DISCONNECTED, AccountStatus.ERROR, True),
        (None, AccountStatus.ERROR, True),
        # Повторная запись той же ошибки — не новый обрыв: уведомление не нужно.
        (AccountStatus.ERROR, AccountStatus.ERROR, False),
        (AccountStatus.ERROR, AccountStatus.CONNECTED, False),
        (AccountStatus.CONNECTED, AccountStatus.CONNECTED, False),
        (AccountStatus.CONNECTED, AccountStatus.DISCONNECTED, False),
    ],
)
def test_entered_error_only_on_transition(
    previous: AccountStatus | None, new: AccountStatus, expected: bool
) -> None:
    assert account_link.entered_error(previous, new) is expected


# --- строка списка чатов -----------------------------------------------------


def _row(
    *,
    direction: Direction | None,
    status: MessageStatus | None,
    is_internal: bool = False,
    text: str | None = "Здравствуйте",
    message_id: int | None = 55,
    account_status: AccountStatus = AccountStatus.CONNECTED,
    account_reason: str | None = None,
) -> tuple:
    client = Client(id=3, telegram_id=1001, tg_first_name="Ксения")
    account = TelegramAccount(
        id=2,
        title="Марина · допы",
        funnel_stage=FunnelStage.SALES,
        status=account_status,
        status_reason=account_reason,
    )
    conversation = Conversation(
        id=9,
        client_id=3,
        account_id=2,
        tg_chat_id=1001,
        unread_count=0,
        is_blocked_by_client=False,
        updated_at=NOW,
    )
    conversation.client = client
    conversation.account = account
    return (
        conversation,
        None,
        text,
        direction,
        MessageKind.TEXT if direction else None,
        None,
        message_id,
        status,
        is_internal,
    )


@pytest.mark.parametrize(
    "status",
    [MessageStatus.QUEUED, MessageStatus.SENT, MessageStatus.READ, MessageStatus.FAILED],
)
def test_outgoing_last_message_carries_its_status(status: MessageStatus) -> None:
    data = conversation_service._row_data(
        _row(direction=Direction.OUT, status=status), None, NOW
    )
    row = ConversationRow(**data)
    assert row.last_message_id == 55
    assert row.last_message_direction == Direction.OUT
    assert row.last_message_status == status
    assert row.last_message_preview == "Вы: Здравствуйте"


def test_incoming_last_message_has_no_status_icon() -> None:
    # У входящего в базе тоже есть статус (read/sent), но показывать его нечему.
    data = conversation_service._row_data(
        _row(direction=Direction.IN, status=MessageStatus.READ), None, NOW
    )
    row = ConversationRow(**data)
    assert row.last_message_direction == Direction.IN
    assert row.last_message_status is None


def test_internal_note_has_no_delivery_status() -> None:
    # Служебная заметка клиенту не уходит: у неё нечего доставлять.
    data = conversation_service._row_data(
        _row(direction=Direction.OUT, status=MessageStatus.SENT, is_internal=True), None, NOW
    )
    row = ConversationRow(**data)
    assert row.last_message_direction == Direction.OUT
    assert row.last_message_status is None


def test_chat_without_messages_has_empty_last_message_fields() -> None:
    data = conversation_service._row_data(
        _row(direction=None, status=None, text=None, message_id=None), None, NOW
    )
    row = ConversationRow(**data)
    assert row.last_message_id is None
    assert row.last_message_direction is None
    assert row.last_message_status is None
    assert row.last_message_preview is None


def test_row_exposes_account_status_for_the_banner() -> None:
    data = conversation_service._row_data(
        _row(
            direction=Direction.OUT,
            status=MessageStatus.SENT,
            account_status=AccountStatus.ERROR,
            account_reason="Сессия отозвана",
        ),
        None,
        NOW,
    )
    row = ConversationRow(**data)
    assert row.account.status == AccountStatus.ERROR
    assert row.account.status_reason == "Сессия отозвана"
    dumped = row.model_dump(mode="json")
    assert dumped["account"]["status"] == "error"
    assert dumped["last_message_status"] == "sent"


def test_new_fields_are_optional_for_old_clients_and_stored_events() -> None:
    """Старая сборка фронтенда и события, записанные до обновления, не ломаются."""
    brief = AccountBrief(id=1, title="Старый", funnel_stage=FunnelStage.SALES)
    assert brief.status is None
    assert brief.status_reason is None
    row = ConversationRow(
        id=1,
        client={"id": 1, "name": "Кто-то", "data_complete": False},
        account=brief,
        updated_at=NOW,
    )
    assert row.last_message_id is None
    assert row.last_message_direction is None
    assert row.last_message_status is None


# --- очередь исходящих: SessionLost без повторов -----------------------------


class _FakeDb:
    def __init__(self) -> None:
        self.commits = 0

    async def commit(self) -> None:
        self.commits += 1


def _pair(attempts: int = 0) -> tuple[Outbox, Message]:
    message = Message(
        id=11, conversation_id=9, direction=Direction.OUT, status=MessageStatus.QUEUED
    )
    row = Outbox(
        id=21,
        message_id=11,
        account_id=2,
        conversation_id=9,
        status=OutboxStatus.SENDING,
        attempts=attempts,
    )
    return row, message


@pytest.fixture
def published(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    seen: list[int] = []

    async def fake_publish(_db: object, _conversation_id: int, message: Message) -> None:
        seen.append(message.id)

    monkeypatch.setattr(outbox, "publish_update", fake_publish)
    return seen


async def test_session_lost_fails_at_once_without_retries(published: list[int]) -> None:
    row, message = _pair()
    reason = "Сессия недействительна — нужен повторный вход"
    await outbox._handle_exception(
        _FakeDb(), [(row, message)], SimpleNamespace(), SessionLost(reason)
    )
    assert row.status == OutboxStatus.FAILED
    assert row.attempts == outbox.MAX_ATTEMPTS
    assert message.status == MessageStatus.FAILED
    assert message.error_text == reason
    assert published == [message.id]


async def test_ordinary_error_is_still_retried_with_backoff(published: list[int]) -> None:
    row, message = _pair()
    before = datetime.now(UTC)
    await outbox._handle_exception(
        _FakeDb(), [(row, message)], SimpleNamespace(), RuntimeError("сеть моргнула")
    )
    assert row.status == OutboxStatus.PENDING
    assert row.attempts == 1
    assert message.status == MessageStatus.QUEUED
    assert row.next_attempt_at >= before + timedelta(seconds=outbox.BACKOFF_SECONDS[0] - 1)


async def test_client_block_stays_final_too(
    published: list[int], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Контроль соседней ветки: правка не задела блокировку клиентом."""
    row, message = _pair()
    conversation = SimpleNamespace(id=9, is_blocked_by_client=False)

    async def fake_detail(_db: object, _id: int) -> object:
        return object()

    async def fake_emit(_db: object, _detail: object) -> None:
        return None

    monkeypatch.setattr(conversation_service, "build_detail", fake_detail)
    monkeypatch.setattr(conversation_service, "emit_updated", fake_emit)
    await outbox._handle_exception(
        _FakeDb(), [(row, message)], conversation, ClientBlocked("Клиент заблокировал номер")
    )
    assert message.status == MessageStatus.FAILED
    assert conversation.is_blocked_by_client is True


# --- шлюз: сессия умерла во время отправки -----------------------------------


@pytest.mark.parametrize(
    "error",
    [AuthKeyUnregisteredError(None), SessionRevokedError(None), UserDeactivatedBanError(None)],
)
def test_session_lost_reason_is_russian(error: Exception) -> None:
    reason = session_lost_reason(error)
    # Текст идёт на экран менеджера и руководителя — английской строки Telethon там быть не должно.
    assert reason.isascii() is False
    assert "key" not in reason.lower()
    assert "session" not in reason.lower()


async def test_dead_session_found_on_send_is_reported_as_account_error() -> None:
    """Сессию отозвали, пока аккаунт работал: отправка узнаёт об этом из отказа Telegram.
    Статус аккаунта обязан стать `error` — иначе нет ни баннера, ни уведомления."""
    reports: list[tuple[int, str, str | None]] = []

    async def status_sink(account_id: int, status: str, reason: str | None) -> None:
        reports.append((account_id, status, reason))

    async def noop(*_args: object, **_kwargs: object) -> None:
        return None

    provider = MTProtoProvider()
    provider.set_sinks(noop, None, status_sink)
    account = SimpleNamespace(id=2)

    translated = await provider._translated(account, SessionRevokedError(None))
    assert isinstance(translated, SessionLost)
    assert reports == [(2, "error", "Сеанс завершён в Telegram — нужен повторный вход")]


async def test_other_send_errors_do_not_touch_account_status() -> None:
    reports: list[tuple[int, str, str | None]] = []

    async def status_sink(account_id: int, status: str, reason: str | None) -> None:
        reports.append((account_id, status, reason))

    async def noop(*_args: object, **_kwargs: object) -> None:
        return None

    provider = MTProtoProvider()
    provider.set_sinks(noop, None, status_sink)
    account = SimpleNamespace(id=2)

    flood = await provider._translated(account, FloodWaitError(request=None, capture=30))
    assert isinstance(flood, RetryAfter)
    other = await provider._translated(account, RuntimeError("сеть"))
    assert isinstance(other, RuntimeError)
    assert reports == []


# --- уведомление об обрыве ---------------------------------------------------


def test_account_error_notification_text() -> None:
    payload = notification_service.account_error_payload(
        2, "Марина · допы", "Сессия отозвана"
    )
    assert payload == {
        "account_id": 2,
        "account_title": "Марина · допы",
        "reason": "Сессия отозвана",
        "kind": "account_error",
    }
    title, text = notification_service.render(
        Notification(
            kind=NotificationKind.ACCOUNT_ERROR,
            entity_type="account",
            entity_id=2,
            payload=payload,
        )
    )
    assert title == "Аккаунт потерял связь"
    assert text == "Марина · допы · Сессия отозвана"


def test_account_error_notification_without_reason_still_reads() -> None:
    payload = notification_service.account_error_payload(2, "Марина · допы", None)
    _, text = notification_service.render(
        Notification(
            kind=NotificationKind.ACCOUNT_ERROR,
            entity_type="account",
            entity_id=2,
            payload=payload,
        )
    )
    assert text == "Марина · допы · нет связи"


class _StatusDb:
    """Подмена сессии базы для write_status: один аккаунт и счёт коммитов."""

    def __init__(self, account: TelegramAccount) -> None:
        self.account = account
        self.locked = False
        self.commits = 0
        self.rollbacks = 0

    @asynccontextmanager
    async def begin_nested(self):  # noqa: ANN201
        yield self

    async def get(self, _model: object, _id: int, *, with_for_update: object = None):  # noqa: ANN201
        self.locked = bool(with_for_update)
        return self.account

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


@pytest.fixture
def status_env(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    account = TelegramAccount(
        id=2,
        title="Марина · допы",
        funnel_stage=FunnelStage.SALES,
        status=AccountStatus.CONNECTED,
    )
    db = _StatusDb(account)
    env = SimpleNamespace(db=db, account=account, notified=[], emitted=[])

    @asynccontextmanager
    async def fake_session():  # noqa: ANN202
        yield db

    async def fake_log_event(*_args: object, **_kwargs: object) -> None:
        return None

    async def fake_emit(_db: object, account_id: int, event: str, data: dict) -> None:
        env.emitted.append((account_id, event, data["status"]))

    async def fake_notify(_db: object, acc: TelegramAccount, reason: str | None) -> None:
        env.notified.append((acc.id, reason))

    monkeypatch.setattr(handlers, "SessionLocal", fake_session)
    monkeypatch.setattr(handlers, "log_event", fake_log_event)
    monkeypatch.setattr(handlers, "emit_to_account", fake_emit)
    monkeypatch.setattr(notification_service, "notify_account_error", fake_notify)
    return env


async def test_write_status_notifies_once_per_transition_to_error(
    status_env: SimpleNamespace,
) -> None:
    await handlers.write_status(2, "error", "Сессия отозвана")
    assert status_env.account.status == AccountStatus.ERROR
    assert status_env.notified == [(2, "Сессия отозвана")]
    # Строку аккаунта берём под замком: два одновременных отчёта об обрыве
    # (запуск сессии и отправка) иначе породили бы два уведомления.
    assert status_env.db.locked is True

    # Тот же обрыв пришёл ещё раз (повторная попытка отправки) — не новое событие.
    await handlers.write_status(2, "error", "Сессия отозвана")
    await handlers.write_status(2, "error", "другой текст причины")
    assert len(status_env.notified) == 1
    assert status_env.account.status_reason == "другой текст причины"

    # Связь вернули и потеряли снова — это второй обрыв, второе уведомление.
    await handlers.write_status(2, "connected", None)
    await handlers.write_status(2, "error", "Снова отозвали")
    assert [reason for _, reason in status_env.notified] == ["Сессия отозвана", "Снова отозвали"]


async def test_write_status_other_statuses_do_not_notify(status_env: SimpleNamespace) -> None:
    await handlers.write_status(2, "disconnected", None)
    await handlers.write_status(2, "connected", None)
    assert status_env.notified == []
    assert [e[1] for e in status_env.emitted] == ["account.status", "account.status"]


async def test_failed_notification_does_not_lose_the_status(
    status_env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Главное — записать, что аккаунт потерял связь: сбой уведомления этого не отменяет."""

    async def broken(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("база недоступна")

    monkeypatch.setattr(notification_service, "notify_account_error", broken)
    await handlers.write_status(2, "error", "Сессия отозвана")
    assert status_env.account.status == AccountStatus.ERROR
    assert status_env.db.commits == 1
    assert status_env.db.rollbacks == 0
    assert status_env.emitted == [(2, "account.status", "error")]
