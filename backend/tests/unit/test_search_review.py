"""Правки по независимому ревью поиска (D-35): индекс, права на суммы, ключи, длина запроса.

Без базы: смотрим, какой SQL сервис отправляет, и как собирается ответ. Живую
проверку с настоящими строками делает `tests/verify_search.py`.
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.v1 import search as search_api
from app.core.db import get_db
from app.core.deps import get_current_user
from app.models import Message, UserRole
from app.services import search_service

NOW = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)


class _Result:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows


class _FakeDb:
    """Запоминает текст SQL и параметры каждого запроса; на все отвечает заранее заданным."""

    def __init__(self, *answers: list[Any]) -> None:
        self.sql: list[str] = []
        self.params: list[dict[str, Any]] = []
        self._answers = list(answers)

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> _Result:
        self.sql.append(str(stmt))
        self.params.append(params or {})
        return _Result(self._answers.pop(0) if self._answers else [])


def _index_expression() -> str:
    """Выражение индекса `ix_messages_text_fts` ровно так, как оно записано в модели."""
    index = next(i for i in Message.__table__.indexes if i.name == "ix_messages_text_fts")
    return str(index.expressions[0])


# --- 1. полнотекстовый поиск идёт через индекс ---------------------------------


def test_message_search_expression_is_the_one_the_index_is_built_on() -> None:
    """Планировщик берёт GIN-индекс, только если выражение совпадает с индексным
    дословно. `to_tsvector('russian', m.text)` без `coalesce` — это seq scan по всем
    сообщениям."""
    in_index = _index_expression()
    assert in_index == "to_tsvector('russian', coalesce(text, ''))"
    assert in_index.replace("coalesce(text,", "coalesce(m.text,") == search_service.MESSAGE_FTS


async def test_chat_search_query_filters_by_the_indexed_expression() -> None:
    db = _FakeDb()
    await search_service._chats(  # noqa: SLF001
        db,  # type: ignore[arg-type]
        "расклад на любовь",
        {"q": "расклад на любовь", "limit": 5, "fetch": 51},
        "",
        "cl.display_name",
        5,
    )
    by_text = next(sql for sql in db.sql if "plainto_tsquery" in sql and "with hits" in sql)
    assert f"{search_service.MESSAGE_FTS} @@ plainto_tsquery('russian', :q)" in by_text
    assert "to_tsvector('russian', m.text)" not in by_text


# --- 2. суммы в группе «Клиенты» режутся по видимости ----------------------------


def _manager() -> SimpleNamespace:
    return SimpleNamespace(id=2, role=UserRole.MANAGER)


def _admin() -> SimpleNamespace:
    return SimpleNamespace(id=1, role=UserRole.ADMIN)


async def _clients_sql(user: SimpleNamespace, visible: list[int] | None) -> tuple[str, dict]:
    async def fake_visible(_db: object, _user: object) -> list[int] | None:
        return visible

    db = _FakeDb([])
    original = search_service.visible_account_ids
    search_service.visible_account_ids = fake_visible  # type: ignore[assignment]
    try:
        await search_service.search(db, user, "ксения", ["clients"], 5)  # type: ignore[arg-type]
    finally:
        search_service.visible_account_ids = original  # type: ignore[assignment]
    assert len(db.sql) == 1
    return db.sql[0], db.params[0]


async def test_paid_amount_of_a_manager_counts_only_conversations_he_can_see() -> None:
    sql, params = await _clients_sql(_manager(), [2])
    # Подзапрос суммы оплат ходит в диалоги сделок и режет их теми же правилами,
    # что и сам список: свой или ничейный диалог на своём аккаунте (D-26).
    paid = sql[sql.index("from deals d") : sql.index("as paid_amount")]
    assert "pc.account_id = any(:account_ids)" in paid
    assert "pc.responsible_id = :scope_uid or pc.responsible_id is null" in paid
    assert params["account_ids"] == [2]
    assert params["scope_uid"] == 2


async def test_paid_amount_of_an_admin_is_not_cut() -> None:
    sql, _ = await _clients_sql(_admin(), None)
    assert "pc.account_id" not in sql
    assert "pc.responsible_id" not in sql


# --- 3. строки группы «Чаты» имеют устойчивый ключ -------------------------------


def _chat_row(conversation_id: int, message_id: int | None, snippet: str | None = "x") -> Any:
    return SimpleNamespace(
        conversation_id=conversation_id,
        message_id=message_id,
        created_at=NOW,
        client_name="Ксения",
        account_title="Марина · допы",
        snippet=snippet,
    )


async def test_chat_rows_always_carry_conversation_id_and_never_repeat() -> None:
    """Диалог без единого сообщения находится по имени клиента и приходит с
    `message_id = null`; ключ строки на экране — `conversation_id` плюс адрес
    сообщения, а при его отсутствии — пометка «chat». Такие ключи не повторяются."""
    by_name = [_chat_row(10, 100), _chat_row(11, None, None)]
    by_text = [_chat_row(10, 100), _chat_row(10, 90), _chat_row(12, 120)]
    db = _FakeDb(by_name, by_text)
    rows, total = await search_service._chats(  # noqa: SLF001
        db,  # type: ignore[arg-type]
        "ксения",
        {"q": "ксения", "limit": 10, "fetch": 51},
        "",
        "cl.display_name",
        10,
    )
    assert total == 4
    assert all(isinstance(row["conversation_id"], int) for row in rows)
    keys = [f"{row['conversation_id']}:{row['message_id'] or 'chat'}" for row in rows]
    assert len(keys) == len(set(keys))
    assert "11:chat" in keys


# --- длина запроса ограничена ----------------------------------------------------


@pytest.fixture
def search_app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    app = FastAPI()
    app.include_router(search_api.router, prefix="/search")
    seen: list[str] = []

    async def fake_search(_db: object, _user: object, query: str, *_args: object) -> dict:
        seen.append(query)
        return {"clients": []}

    async def fake_user() -> SimpleNamespace:
        return _manager()

    async def fake_db() -> None:
        return None

    monkeypatch.setattr(search_service, "search", fake_search)
    app.dependency_overrides[get_current_user] = fake_user
    app.dependency_overrides[get_db] = fake_db
    app.state.seen = seen
    return app


async def test_search_query_longer_than_255_is_rejected_with_422(search_app: FastAPI) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=search_app), base_url="http://test"
    ) as client:
        too_long = await client.get("/search", params={"q": "я" * 256})
        at_limit = await client.get("/search", params={"q": "я" * 255})
    assert too_long.status_code == 422
    assert at_limit.status_code == 200
    assert search_app.state.seen == ["я" * 255]
