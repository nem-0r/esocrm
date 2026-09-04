"""WebSocket живых обновлений.

Клиент шлёт ping каждые 25 секунд и viewing при открытии чата — чтобы в шапке
было видно, кто ещё здесь. Двое менеджеров в одном чате не блокируются,
но видят друг друга.
"""

import logging
from datetime import UTC, datetime

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


async def _authenticate(ws: WebSocket) -> User | None:
    token = ws.cookies.get(SESSION_COOKIE) or ws.query_params.get("token")
    if not token:
        return None
    async with SessionLocal() as db:
        row = await db.execute(
            select(User)
            .join(AuthSession, AuthSession.user_id == User.id)
            .where(
                AuthSession.token_hash == token_hash(token),
                AuthSession.revoked_at.is_(None),
                AuthSession.expires_at > datetime.now(UTC),
                User.is_active.is_(True),
            )
        )
        return row.scalar_one_or_none()


@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    user = await _authenticate(ws)
    if user is None:
        await ws.close(code=4401)
        return

    await hub.connect(user.id, ws)
    await touch_presence(user.id)
    await emit("presence.updated", {"user_id": user.id, "online": True})

    try:
        while True:
            msg = await ws.receive_json()
            kind = msg.get("type")

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
