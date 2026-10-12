"""WebSocket живых обновлений.

Клиент шлёт ping каждые 25 секунд и viewing при открытии чата — чтобы в шапке
было видно, кто ещё здесь. Двое менеджеров в одном чате не блокируются,
но видят друг друга.

Сессия проверяется не только при подключении. После сброса пароля, отключения
сотрудника, выхода или истечения срока сокет не должен продолжать получать
события: сессия перепроверяется на каждом пинге и по таймеру (если клиент
молчит), и умершая сессия закрывает сокет кодом 4401 — тем же, что и отказ
при подключении.
"""

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from app.core.crypto import token_hash
from app.core.db import SessionLocal
from app.core.deps import SESSION_COOKIE
from app.core.redis_bus import mark_viewing, touch_presence, viewers
from app.models import AuthSession, User
from app.realtime.events import emit, emit_to_conversation
from app.realtime.hub import hub

router = APIRouter()
log = logging.getLogger("astra.ws")

# Код закрытия «сессии нет»: при подключении и когда сессия умерла на ходу.
SESSION_ENDED = 4401
# Не реже чем раз в столько секунд сессия перепроверяется, даже если клиент
# молчит. Обычный клиент пингует раз в 25 с — и проверяется на каждом пинге.
REVALIDATE_SECONDS = 30.0


def _session_token(ws: WebSocket) -> str | None:
    return ws.cookies.get(SESSION_COOKIE) or ws.query_params.get("token")


async def _user_for_token(token: str) -> User | None:
    """Владелец сессии, если она жива: не отозвана, не истекла, сотрудник активен
    и не удалён. Те же условия, что у обычных запросов (`core/deps.py`)."""
    async with SessionLocal() as db:
        row = await db.execute(
            select(User)
            .join(AuthSession, AuthSession.user_id == User.id)
            .where(
                AuthSession.token_hash == token_hash(token),
                AuthSession.revoked_at.is_(None),
                AuthSession.expires_at > datetime.now(UTC),
                User.is_active.is_(True),
                User.deleted_at.is_(None),
            )
        )
        return row.scalar_one_or_none()


async def _session_alive(token: str, user_id: int) -> bool:
    user = await _user_for_token(token)
    return user is not None and user.id == user_id


@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    token = _session_token(ws)
    user = await _user_for_token(token) if token else None
    if token is None or user is None:
        await ws.close(code=SESSION_ENDED)
        return

    await hub.connect(user.id, ws)
    await touch_presence(user.id)
    await emit("presence.updated", {"user_id": user.id, "online": True})

    loop = asyncio.get_running_loop()
    checked_at = loop.time()
    try:
        while True:
            # Ждём сообщение не дольше, чем до следующей обязательной проверки.
            # Отмена ожидания безопасна: непрочитанное сообщение не теряется.
            timed_out = False
            msg: dict[str, Any] = {}
            try:
                raw = await asyncio.wait_for(
                    ws.receive_json(),
                    timeout=max(checked_at + REVALIDATE_SECONDS - loop.time(), 0.0),
                )
                msg = raw if isinstance(raw, dict) else {}
            except TimeoutError:
                timed_out = True
            kind = msg.get("type")

            if timed_out or kind == "ping" or loop.time() - checked_at >= REVALIDATE_SECONDS:
                if not await _session_alive(token, user.id):
                    # Сначала убрать из рассылки, потом закрыть: ни одного события
                    # после того, как сессия признана мёртвой.
                    hub.disconnect(user.id, ws)
                    await ws.close(code=SESSION_ENDED)
                    return
                checked_at = loop.time()

            if kind == "ping":
                await touch_presence(user.id)
                await ws.send_json({"type": "pong"})

            elif kind == "viewing":
                conversation_id = int(msg.get("conversation_id") or 0)
                if conversation_id:
                    await mark_viewing(conversation_id, user.id)
                    async with SessionLocal() as db:
                        await emit_to_conversation(
                            db,
                            conversation_id,
                            "conversation.viewers",
                            {
                                "conversation_id": conversation_id,
                                "user_ids": await viewers(conversation_id),
                            },
                        )
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        log.info("Соединение закрыто: %s", exc)
    finally:
        hub.disconnect(user.id, ws)
