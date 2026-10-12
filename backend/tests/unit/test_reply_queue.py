"""Очередь ответов (D-34): общее определение «ждёт дольше порога», порядок, курсор, skip.

Без Postgres. Порядок и курсор проверяются настоящим исполнением запроса в
SQLite в памяти: логика — обычные сравнения и `CASE`, диалект им не важен.
Условие порога и область видимости, которые зависят от функций Postgres,
проверяются по тексту запроса и по связанным параметрам.
"""

from datetime import UTC, datetime, time, timedelta

import pytest
from sqlalchemy import (
    Column,
    DateTime,
    Integer,
    MetaData,
    Table,
    create_engine,
    insert,
    select,
)
from sqlalchemy.dialects import postgresql

from app.core.errors import Invalid
from app.services import reply_queue, worktime
from app.services.conversation_service import decode_cursor, encode_cursor

NOW = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)


def _hours(enabled: bool) -> worktime.WorkHours:
    return worktime.WorkHours(
        {
            "working_hours_enabled": enabled,
            "working_days": [1, 2, 3, 4, 5],
            "working_hours_start": "10:00",
            "working_hours_end": "19:00",
            "timezone": "Europe/Moscow",
        }
    )


def _sql(clause) -> tuple[str, dict]:
    compiled = clause.compile(dialect=postgresql.dialect())
    return str(compiled), compiled.params


# ----------------------------------------------------------- порог


def test_without_work_hours_threshold_is_plain_cutoff():
    threshold = reply_queue.Threshold(minutes=30, hours=_hours(False), now=NOW)
    text, params = _sql(reply_queue.over_threshold(threshold))
    assert "astra_working_seconds" not in text
    assert NOW - timedelta(minutes=30) in params.values()


def test_with_work_hours_threshold_counts_working_seconds():
    threshold = reply_queue.Threshold(minutes=45, hours=_hours(True), now=NOW)
    text, params = _sql(reply_queue.over_threshold(threshold))
    assert "astra_working_seconds" in text
    # Порог переводится в секунды, а не сравнивается минутами с секундами.
    assert 45 * 60 in params.values()
    assert time(10, 0) in params.values() and time(19, 0) in params.values()
    assert "Europe/Moscow" in params.values()


def test_threshold_never_looks_at_seen_flag():
    """«Просмотрено» не убирает чат из баннера, плитки и фильтра (D-34)."""
    for enabled in (False, True):
        threshold = reply_queue.Threshold(minutes=30, hours=_hours(enabled), now=NOW)
        text, _ = _sql(reply_queue.over_threshold(threshold))
        assert "awaiting_seen_at" not in text


# ----------------------------------------------------------- skip


@pytest.mark.parametrize("raw", [None, "", " ", ",", " , "])
def test_skip_empty(raw):
    assert reply_queue.parse_skip(raw) == []


def test_skip_keeps_order_and_drops_duplicates():
    assert reply_queue.parse_skip("5, 3,5 ,9,3") == [5, 3, 9]


@pytest.mark.parametrize("raw", ["a", "1,x", "0", "-4", "1;2", "1.5"])
def test_skip_rejects_garbage(raw):
    with pytest.raises(Invalid):
        reply_queue.parse_skip(raw)


def test_skip_limit_is_fifty():
    fifty = ",".join(str(i) for i in range(1, 51))
    assert len(reply_queue.parse_skip(fifty)) == 50
    with pytest.raises(Invalid):
        reply_queue.parse_skip(fifty + ",51")
    # Повторы не считаются: это всё тот же список.
    assert reply_queue.parse_skip(",".join(["7"] * 80)) == [7]


# ----------------------------------------------------------- курсор и порядок (SQLite)

meta = MetaData()
chats = Table(
    "chats",
    meta,
    Column("id", Integer, primary_key=True),
    Column("since", DateTime),
    Column("seen", DateTime),
    Column("last", DateTime),
)


def _engine(rows: list[dict]):
    engine = create_engine("sqlite://")
    meta.create_all(engine)
    with engine.begin() as conn:
        conn.execute(insert(chats), rows)
    return engine


def _t(minute: int, second: int = 0) -> datetime:
    return datetime(2026, 10, 11, 9, minute, second)


def _walk(engine, limit: int) -> list[list[int]]:
    """Постранично, как list_conversations: курсор → keyset → сортировка → limit+1."""
    pages: list[list[int]] = []
    cursor = None
    while True:
        stmt = select(chats.c.id, chats.c.since)
        parsed = decode_cursor(cursor)
        if parsed and parsed[0] is not None:
            stmt = stmt.where(reply_queue.keyset_after(chats.c.since, chats.c.id, parsed))
        stmt = stmt.order_by(chats.c.since.asc(), chats.c.id.asc()).limit(limit + 1)
        with engine.connect() as conn:
            found = conn.execute(stmt).all()
        page = found[:limit]
        pages.append([row.id for row in page])
        if len(found) <= limit:
            return pages
        cursor = encode_cursor(page[-1].since, page[-1].id)


def test_pagination_oldest_first_without_gaps_or_duplicates_on_equal_times():
    # Много одинаковых времён: именно здесь курсор по одному времени теряет строки.
    rows = [{"id": i, "since": _t(10 + i // 4)} for i in range(1, 22)]
    rows += [{"id": 22, "since": _t(5, 30)}, {"id": 23, "since": _t(5, 30)}]
    engine = _engine(rows)
    for limit in (1, 2, 3, 4, 5, 7, 30):
        pages = _walk(engine, limit)
        flat = [i for page in pages for i in page]
        expected = [
            r["id"] for r in sorted(rows, key=lambda r: (r["since"], r["id"]))
        ]
        assert flat == expected, f"limit={limit}"
        assert all(len(page) <= limit for page in pages)


def test_pagination_survives_row_leaving_between_pages():
    """Ответили на уже показанный чат — следующая страница не сдвигается."""
    rows = [{"id": i, "since": _t(i)} for i in range(1, 9)]
    engine = _engine(rows)
    with engine.connect() as conn:
        first = conn.execute(
            select(chats.c.id, chats.c.since).order_by(chats.c.since, chats.c.id).limit(3)
        ).all()
    cursor = encode_cursor(first[-1].since, first[-1].id)
    with engine.begin() as conn:
        conn.execute(chats.delete().where(chats.c.id == 2))
    parsed = decode_cursor(cursor)
    with engine.connect() as conn:
        rest = conn.execute(
            select(chats.c.id)
            .where(reply_queue.keyset_after(chats.c.since, chats.c.id, parsed))
            .order_by(chats.c.since, chats.c.id)
        ).all()
    assert [r.id for r in rest] == [4, 5, 6, 7, 8]


def test_cursor_from_another_sort_reads_from_the_start():
    """Курсор «без времени» принадлежит списку «Все»: здесь он ничего не отрезает."""
    engine = _engine([{"id": i, "since": _t(i)} for i in range(1, 5)])
    with engine.connect() as conn:
        rows = conn.execute(
            select(chats.c.id)
            .where(reply_queue.keyset_after(chats.c.since, chats.c.id, (None, 3)))
            .order_by(chats.c.id)
        ).all()
    assert [r.id for r in rows] == [1, 2, 3, 4]


def test_cursor_roundtrip_keeps_microseconds_and_zone():
    stamp = datetime(2026, 10, 11, 9, 5, 3, 123456, tzinfo=UTC)
    assert decode_cursor(encode_cursor(stamp, 77)) == (stamp, 77)


def _order(engine, where=None) -> list[int]:
    stmt = select(chats.c.id).order_by(
        *reply_queue.next_order(chats.c.since, chats.c.seen, chats.c.last, chats.c.id)
    )
    if where is not None:
        stmt = stmt.where(where)
    with engine.connect() as conn:
        return [row.id for row in conn.execute(stmt)]


def test_next_order_unseen_first_then_seen_then_the_rest():
    engine = _engine(
        [
            {"id": 1, "since": _t(10), "seen": None, "last": _t(10)},  # не открыт
            {"id": 2, "since": _t(5), "seen": _t(40), "last": _t(5)},  # открыт, ждёт давно
            {"id": 3, "since": _t(20), "seen": None, "last": _t(20)},  # не открыт, свежее
            {"id": 4, "since": None, "seen": None, "last": _t(50)},  # не ждёт, свежий
            {"id": 5, "since": None, "seen": None, "last": _t(30)},  # не ждёт, старый
            {"id": 6, "since": _t(10), "seen": None, "last": _t(11)},  # ровно как №1
            # Открыт ДО нового ожидания: для нового ожидания он снова «не открыт».
            {"id": 7, "since": _t(15), "seen": _t(1), "last": _t(15)},
            {"id": 8, "since": None, "seen": None, "last": None},  # пустой чат
        ]
    )
    # Не открытые по давности (при равенстве — по id), затем открытые, затем остальные.
    assert _order(engine) == [1, 6, 7, 3, 2, 4, 5, 8]


def test_next_order_with_skip_never_repeats_until_all_awaiting_taken():
    engine = _engine(
        [
            {"id": 1, "since": _t(10), "seen": None, "last": _t(10)},
            {"id": 2, "since": _t(5), "seen": _t(40), "last": _t(5)},
            {"id": 3, "since": _t(20), "seen": None, "last": _t(20)},
            {"id": 4, "since": None, "seen": None, "last": _t(50)},
        ]
    )
    taken: list[int] = []
    while True:
        order = _order(engine, where=chats.c.id.notin_(taken) & chats.c.since.isnot(None))
        if not order:
            break
        taken.append(order[0])
    assert taken == [1, 3, 2]
    assert len(set(taken)) == len(taken)


def test_next_order_uses_id_as_tiebreak_for_waiting_only():
    engine = _engine(
        [
            {"id": 9, "since": _t(10), "seen": None, "last": _t(10)},
            {"id": 3, "since": _t(10), "seen": None, "last": _t(10)},
            {"id": 8, "since": None, "seen": None, "last": _t(20)},
            {"id": 4, "since": None, "seen": None, "last": _t(20)},
        ]
    )
    # Ждущие — по возрастанию id (как в списке), остальные — по убыванию.
    assert _order(engine) == [3, 9, 8, 4]

