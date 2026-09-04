"""Живые обновления.

Шлюз и API публикуют события в Redis, хаб разносит их по открытым WebSocket-соединениям.
Потеря Redis не должна ничего ломать: клиент при обрыве переподключается и дозагружает
пропущенное запросом, а не показывает молча устаревшее.
"""

import asyncio
import logging
from collections import defaultdict
from typing import Any

from fastapi import WebSocket

from app.core.redis_bus import subscribe

log = logging.getLogger("astra.realtime")


class Hub:
    def __init__(self) -> None:
        # user_id -> открытые соединения этого пользователя (вкладки, телефон)
        self._sockets: dict[int, set[WebSocket]] = defaultdict(set)
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._pump())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None

    async def connect(self, user_id: int, ws: WebSocket) -> None:
        await ws.accept()
        self._sockets[user_id].add(ws)

    def disconnect(self, user_id: int, ws: WebSocket) -> None:
        self._sockets[user_id].discard(ws)
        if not self._sockets[user_id]:
            self._sockets.pop(user_id, None)

    @property
    def connected_user_ids(self) -> list[int]:
        return list(self._sockets.keys())

    async def send_to(self, user_ids: list[int], payload: dict[str, Any]) -> None:
        dead: list[tuple[int, WebSocket]] = []
        for uid in user_ids:
            for ws in list(self._sockets.get(uid, ())):
                try:
                    await ws.send_json(payload)
                except Exception:
                    dead.append((uid, ws))
        for uid, ws in dead:
            self.disconnect(uid, ws)

    async def broadcast(self, payload: dict[str, Any]) -> None:
        await self.send_to(self.connected_user_ids, payload)

    async def _pump(self) -> None:
        """Читает Redis и разносит события. Падение канала не должно ронять API."""
        while True:
            try:
                async for event in subscribe():
                    audience = event.get("audience") or {}
                    payload = {"type": event["type"], "data": event["data"]}
                    user_ids = audience.get("user_ids")
                    if user_ids:
                        await self.send_to([int(u) for u in user_ids], payload)
                    else:
                        await self.broadcast(payload)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("Канал живых событий оборвался, переподключаюсь: %s", exc)
                await asyncio.sleep(2)


hub = Hub()
