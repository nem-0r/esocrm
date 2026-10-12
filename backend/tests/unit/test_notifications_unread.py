"""Бейдж колокольчика и список уведомлений считают одно и то же окно.

Раньше список показывал только последние 31 день, а счётчик считал всё: бейдж
мог гореть непрочитанными, которых в списке нет. Здесь база не нужна — смотрим,
какие запросы сервис отправляет, и сравниваем их условия. Реальные строки в
базе проверяет `tests/verify_profile_remarks.py`.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.dialects import postgresql

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
