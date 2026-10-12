"""Бейдж колокольчика и список уведомлений считают одно и то же окно.

Раньше список показывал только последние 31 день, а счётчик считал всё: бейдж
мог гореть непрочитанными, которых в списке нет. Здесь база не нужна — смотрим,
какие запросы сервис отправляет, и сравниваем их условия. Реальные строки в
базе проверяет `tests/verify_profile_remarks.py`.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from app.models import NotificationKind
from app.services import notification_service as svc

FROZEN_NOW = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)


class _Result:
    def scalars(self) -> "_Result":
        return self

    def all(self) -> list[Any]:
        return []

    def scalar_one(self) -> int:
        return 0


class _FakeDb:
    """Запоминает запросы, которые сервис отправил в базу."""

    def __init__(self) -> None:
        self.statements: list[Any] = []

    async def execute(self, stmt: Any) -> _Result:
        self.statements.append(stmt)
        return _Result()


def _where(stmt: Any) -> str:
    """Условие запроса как текст, с подставленными значениями."""
    return str(
        stmt.whereclause.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )


@pytest.fixture
def frozen_window(monkeypatch: pytest.MonkeyPatch) -> datetime:
    start = svc.window_start(FROZEN_NOW)
    monkeypatch.setattr(svc, "window_start", lambda now=None: start)
    return start


def test_window_is_31_days_back_from_now() -> None:
    assert svc.NOTIFICATIONS_WINDOW_DAYS == 31
    assert svc.window_start(FROZEN_NOW) == FROZEN_NOW - timedelta(days=31)


def test_window_start_without_argument_is_about_31_days_ago() -> None:
    expected = datetime.now(UTC) - timedelta(days=31)
    assert abs((svc.window_start() - expected).total_seconds()) < 5


async def test_unread_count_uses_the_same_window_as_the_list(frozen_window: datetime) -> None:
    user = SimpleNamespace(id=7)
    db = _FakeDb()

    await svc.unread_count(db, user)  # type: ignore[arg-type]
    await svc.list_for(db, user, only_unread=True)  # type: ignore[arg-type]

    count_stmt, list_stmt = db.statements
    # Окно по дате есть в обоих запросах и одинаково.
    assert "notifications.created_at >=" in _where(count_stmt)
    assert str(frozen_window.year) in _where(count_stmt)
    # Условия «чьё», «непрочитанное», «в окне» совпадают дословно.
    assert _where(count_stmt) == _where(list_stmt)


async def test_unread_count_filters_by_user_and_unread(frozen_window: datetime) -> None:
    db = _FakeDb()
    await svc.unread_count(db, SimpleNamespace(id=42))  # type: ignore[arg-type]

    where = _where(db.statements[0])
    assert "notifications.user_id = 42" in where
    assert "notifications.read_at IS NULL" in where


async def test_list_without_only_unread_has_same_window_but_no_read_filter(
    frozen_window: datetime,
) -> None:
    db = _FakeDb()
    await svc.list_for(db, SimpleNamespace(id=42))  # type: ignore[arg-type]

    where = _where(db.statements[0])
    assert "notifications.created_at >=" in where
    assert "read_at" not in where


async def test_unread_count_returns_database_number() -> None:
    class _Counting(_FakeDb):
        async def execute(self, stmt: Any) -> Any:
            await super().execute(stmt)
            return SimpleNamespace(scalar_one=lambda: 5)

    assert await svc.unread_count(_Counting(), SimpleNamespace(id=1)) == 5  # type: ignore[arg-type]


# --- событие `notification.new` уходит только после коммита -------------------


class _NotifyDb:
    """Сессия для `notify`: настоящая `Session` без базы (к ней цепляются события
    сессии), а запись строк подменена."""

    def __init__(self) -> None:
        self.sync_session = Session()
        self.added: list[Any] = []

    def add_all(self, rows: list[Any]) -> None:
        self.added.extend(rows)

    async def flush(self) -> None:
        for number, row in enumerate(self.added, start=1):
            row.id = number
            row.created_at = FROZEN_NOW

    def commit(self) -> None:
        """Как `session.commit()` на корневой транзакции: событие сессии `after_commit`."""
        self.sync_session.dispatch.after_commit(self.sync_session)

    def release_savepoint(self) -> None:
        """`after_commit` приходит и при освобождении точки сохранения — до настоящего коммита."""
        self.sync_session.in_nested_transaction = lambda: True  # type: ignore[method-assign]
        try:
            self.sync_session.dispatch.after_commit(self.sync_session)
        finally:
            del self.sync_session.in_nested_transaction

    def rollback(self) -> None:
        root = SimpleNamespace(nested=False, parent=None)
        self.sync_session.dispatch.after_soft_rollback(self.sync_session, root)


@pytest.fixture
def published(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any], list[int]]]:
    events: list[tuple[str, dict[str, Any], list[int]]] = []

    async def fake_emit(event: str, data: dict[str, Any], user_ids: list[int]) -> None:
        events.append((event, data, user_ids))

    monkeypatch.setattr(svc, "emit", fake_emit)
    return events


async def _notify(db: _NotifyDb, user_ids: list[int]) -> None:
    await svc.notify(
        db,  # type: ignore[arg-type]
        user_ids=user_ids,
        kind=NotificationKind.DEAL_PAID,
        entity_type="deal",
        entity_id=5,
        payload={"number": "DEAL-5"},
    )


async def test_new_notification_is_not_published_before_commit(
    published: list[Any],
) -> None:
    """Событие приходит на экран и тут же вызывает перезапрос счётчика. Если оно
    ушло до коммита, перезапрос не видит строку, и бейдж показывает старое число."""
    db = _NotifyDb()
    await _notify(db, [3, 1, 3])
    await asyncio.sleep(0)
    assert published == []


async def test_new_notification_is_published_once_per_user_after_commit(
    published: list[Any],
) -> None:
    db = _NotifyDb()
    await _notify(db, [3, 1, 3])
    db.commit()
    await asyncio.sleep(0)

    assert [(event, user_ids) for event, _, user_ids in published] == [
        ("notification.new", [1]),
        ("notification.new", [3]),
    ]
    first = published[0][1]["notification"]
    assert first["kind"] == NotificationKind.DEAL_PAID
    assert first["title"] == "Оплата получена"
    assert first["read_at"] is None

    # Следующий коммит той же сессии ничего не повторяет.
    db.commit()
    await asyncio.sleep(0)
    assert len(published) == 2


async def test_releasing_a_savepoint_is_not_a_commit(published: list[Any]) -> None:
    """Уведомление об обрыве аккаунта пишется внутри точки сохранения; её закрытие
    ещё не фиксирует транзакцию, и показывать событие рано."""
    db = _NotifyDb()
    await _notify(db, [1])
    db.release_savepoint()
    await asyncio.sleep(0)
    assert published == []

    db.commit()
    await asyncio.sleep(0)
    assert len(published) == 1


async def test_rolled_back_notification_is_never_published(published: list[Any]) -> None:
    db = _NotifyDb()
    await _notify(db, [1])
    db.rollback()
    db.commit()
    await asyncio.sleep(0)
    assert published == []


async def test_rolled_back_savepoint_drops_only_its_own_events(published: list[Any]) -> None:
    db = _NotifyDb()
    session = db.sync_session
    outer = SimpleNamespace(nested=True, parent=SimpleNamespace(nested=False, parent=None))
    inner = SimpleNamespace(nested=True, parent=outer)

    await _notify(db, [1])  # вне точки сохранения
    session.get_nested_transaction = lambda: inner  # type: ignore[method-assign]
    await _notify(db, [2])  # внутри вложенной точки сохранения
    del session.get_nested_transaction

    session.dispatch.after_soft_rollback(session, outer)  # откатили внешнюю точку
    db.commit()
    await asyncio.sleep(0)

    assert [user_ids for _, _, user_ids in published] == [[1]]


async def test_session_closed_without_commit_leaves_no_phantom_events(
    published: list[Any],
) -> None:
    """Сессию закрыли, не коммитя (ошибка в запросе): её события не должны уйти
    ни при каком последующем коммите."""
    db = _NotifyDb()
    await _notify(db, [1])
    root = SimpleNamespace(nested=False, parent=None)
    db.sync_session.dispatch.after_transaction_end(db.sync_session, root)
    db.commit()
    await asyncio.sleep(0)
    assert published == []
