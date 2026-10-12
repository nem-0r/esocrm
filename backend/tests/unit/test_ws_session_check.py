"""WebSocket перепроверяет сессию, пока открыт (ревью сброса пароля, D-38).

Раньше сессия проверялась только при подключении: после сброса пароля или
отключения сотрудника открытый сокет продолжал получать события, а пинг раз в
25 секунд держал его вечно. Теперь сессия перепроверяется на каждом пинге и по
таймеру — даже если клиент молчит; умершая сессия закрывает сокет кодом 4401.

База и Redis подменены: проверяем сам цикл, а сквозной сценарий «сбросили
пароль — сокет закрылся» — в `tests/verify_auth_reset.py`.
"""

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import WebSocketDisconnect

from app.api.v1 import ws as ws_module
from app.realtime.hub import hub

USER_ID = 4242
RAW = "tok"  # значение cookie сессии в подменённом сокете
STOP = object()


class FakeSocket:
    """Ровно то, чем пользуется обработчик: cookie, приём, отправка, закрытие."""

    def __init__(self, token: str | None = RAW) -> None:
        self.cookies = {ws_module.SESSION_COOKIE: token} if token else {}
        self.query_params: dict[str, str] = {}
        self.incoming: asyncio.Queue[Any] = asyncio.Queue()
        self.sent: list[dict[str, Any]] = []
        self.closed_with: int | None = None
        self.closed = asyncio.Event()

    async def accept(self) -> None:
        return None

    async def receive_json(self) -> Any:
        item = await self.incoming.get()
        if item is STOP:
            raise WebSocketDisconnect(code=1000)
        return item

    async def send_json(self, payload: dict[str, Any]) -> None:
        self.sent.append(payload)

    async def close(self, code: int = 1000) -> None:
        self.closed_with = code
        self.closed.set()


@pytest.fixture
def session(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    state = SimpleNamespace(alive=True, checks=0)

    async def user_for_token(token: str) -> Any:
        return SimpleNamespace(id=USER_ID) if state.alive else None

    async def session_alive(token: str, user_id: int) -> bool:
        assert token == RAW and user_id == USER_ID
        state.checks += 1
        return state.alive

    async def noop(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(ws_module, "_user_for_token", user_for_token)
    monkeypatch.setattr(ws_module, "_session_alive", session_alive)
    monkeypatch.setattr(ws_module, "touch_presence", noop)
    monkeypatch.setattr(ws_module, "emit", noop)
    return state


async def start(sock: FakeSocket) -> asyncio.Task[None]:
    task = asyncio.create_task(ws_module.websocket_endpoint(sock))  # type: ignore[arg-type]
    await asyncio.sleep(0.01)
    return task


async def finish(sock: FakeSocket, task: asyncio.Task[None]) -> None:
    if not task.done():
        await sock.incoming.put(STOP)
    await asyncio.wait_for(task, timeout=2)


def connected(sock: FakeSocket) -> bool:
    return any(s is sock for s in hub._sockets.get(USER_ID, ()))


async def test_live_session_answers_pings_and_stays_open(session: SimpleNamespace) -> None:
    sock = FakeSocket()
    task = await start(sock)
    for _ in range(3):
        await sock.incoming.put({"type": "ping"})
        await asyncio.sleep(0.01)
    assert sock.sent == [{"type": "pong"}] * 3
    assert sock.closed_with is None
    assert session.checks == 3  # каждый пинг — проверка сессии
    assert connected(sock)
    await finish(sock, task)
    assert not connected(sock)


async def test_ping_after_password_reset_closes_the_socket(session: SimpleNamespace) -> None:
    sock = FakeSocket()
    task = await start(sock)
    await sock.incoming.put({"type": "ping"})
    await asyncio.sleep(0.01)
    assert sock.sent == [{"type": "pong"}]

    session.alive = False  # сбросили пароль / отключили сотрудника / сессия истекла
    await sock.incoming.put({"type": "ping"})
    await asyncio.wait_for(task, timeout=2)

    assert sock.closed_with == 4401
    assert sock.sent == [{"type": "pong"}]  # на мёртвую сессию «понг» не отвечаем
    assert not connected(sock)  # и события ему больше не разносятся


async def test_silent_client_is_rechecked_by_timer(
    session: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Клиент, который перестал слать пинги, не должен слушать события вечно.
    monkeypatch.setattr(ws_module, "REVALIDATE_SECONDS", 0.05)
    sock = FakeSocket()
    task = await start(sock)
    await asyncio.sleep(0.2)
    assert sock.closed_with is None
    assert session.checks >= 2  # таймер проверял, сессия жива — сокет открыт

    session.alive = False
    await asyncio.wait_for(sock.closed.wait(), timeout=2)
    await asyncio.wait_for(task, timeout=2)
    assert sock.closed_with == 4401
    assert not connected(sock)


async def test_frequent_viewing_messages_do_not_postpone_the_check(
    session: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Поток сообщений «смотрю чат» без пингов не должен откладывать проверку.
    monkeypatch.setattr(ws_module, "REVALIDATE_SECONDS", 0.05)

    async def noop(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(ws_module, "mark_viewing", noop)
    monkeypatch.setattr(ws_module, "emit_to_conversation", noop)
    monkeypatch.setattr(ws_module, "viewers", noop)

    class _Db:
        async def __aenter__(self) -> "_Db":
            return self

        async def __aexit__(self, *_exc: object) -> None:
            return None

    monkeypatch.setattr(ws_module, "SessionLocal", _Db)
    sock = FakeSocket()
    task = await start(sock)
    session.alive = False
    for _ in range(40):
        if task.done():
            break
        await sock.incoming.put({"type": "viewing", "conversation_id": 1})
        await asyncio.sleep(0.01)
    await asyncio.wait_for(task, timeout=2)
    assert sock.closed_with == 4401


async def test_without_a_session_the_socket_is_refused(session: SimpleNamespace) -> None:
    sock = FakeSocket(token=None)
    await asyncio.wait_for(ws_module.websocket_endpoint(sock), timeout=2)  # type: ignore[arg-type]
    assert sock.closed_with == 4401
    assert not connected(sock)

    session.alive = False
    revoked = FakeSocket()
    await asyncio.wait_for(ws_module.websocket_endpoint(revoked), timeout=2)  # type: ignore[arg-type]
    assert revoked.closed_with == 4401
