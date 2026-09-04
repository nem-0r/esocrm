"""Redis: разноска живых событий и онлайн-статус.

Правило: потеря Redis не должна ничего ломать — максимум страница перестанет
обновляться сама. Всё, что нельзя терять, живёт в PostgreSQL.

Правило это соблюдается здесь, а не у вызывающих: любая операция с Redis
переживает недоступность сервера и возвращает пустой ответ. Иначе перезапуск
Redis означал бы, что менеджер не может отправить сообщение — а сообщение
к Redis отношения не имеет, оно уже лежит в базе и в очереди отправки.
"""

import contextlib
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import redis.asyncio as aioredis
from redis.exceptions import RedisError

from app.core.config import settings

log = logging.getLogger("astra.redis")

# Об одной и той же недоступности не пишем в лог на каждый запрос: при упавшем
# Redis это сотни строк в секунду, за которыми не видно остального.
_silent = False


def _survive(action: str, error: Exception) -> None:
    global _silent
    if not _silent:
        log.warning("Redis недоступен (%s): %s. Работаем без живых обновлений.", action, error)
        _silent = True


def _recovered() -> None:
    global _silent
    if _silent:
        log.info("Redis снова отвечает, живые обновления восстановлены.")
        _silent = False

CHANNEL = "astra:events"
PRESENCE_PREFIX = "astra:presence:"
VIEWING_PREFIX = "astra:viewing:"

_client: aioredis.Redis | None = None


def get_redis() -> aioredis.Redis:
    global _client
    if _client is None:
        _client = aioredis.from_url(settings.redis_url, decode_responses=True)
    return _client


async def publish(
    event_type: str, data: dict[str, Any], audience: dict[str, Any] | None = None
) -> None:
    """Разослать событие. Недоступность Redis не считается ошибкой действия:
    сообщение уже сохранено, оплата уже проведена — не обновится только экран."""
    payload = {"type": event_type, "data": data, "audience": audience or {}}
    try:
        await get_redis().publish(CHANNEL, json.dumps(payload, default=str))
        _recovered()
    except (RedisError, OSError) as exc:
        _survive(f"событие {event_type}", exc)


async def subscribe() -> AsyncIterator[dict[str, Any]]:
    pubsub = get_redis().pubsub()
    await pubsub.subscribe(CHANNEL)
    try:
        async for raw in pubsub.listen():
            if raw.get("type") != "message":
                continue
            try:
                yield json.loads(raw["data"])
            except (ValueError, TypeError):
                continue
    finally:
        # Канал уже мог оборваться — прощаться с мёртвым соединением
        # незачем, и трейсбек об этом в логах только мешает.
        with contextlib.suppress(RedisError, OSError):
            await pubsub.unsubscribe(CHANNEL)
            await pubsub.aclose()


async def touch_presence(user_id: int) -> None:
    try:
        await get_redis().setex(f"{PRESENCE_PREFIX}{user_id}", settings.presence_ttl_seconds, "1")
    except (RedisError, OSError) as exc:
        _survive("отметка присутствия", exc)


async def drop_presence(user_id: int) -> None:
    try:
        await get_redis().delete(f"{PRESENCE_PREFIX}{user_id}")
    except (RedisError, OSError) as exc:
        _survive("снятие присутствия", exc)


async def is_online(user_id: int) -> bool:
    """Без Redis все считаются офлайн — это честнее, чем «онлайн» наугад:
    последний визит всё равно виден по отметке в базе."""
    try:
        return bool(await get_redis().exists(f"{PRESENCE_PREFIX}{user_id}"))
    except (RedisError, OSError) as exc:
        _survive("проверка присутствия", exc)
        return False


async def online_user_ids(user_ids: list[int]) -> set[int]:
    if not user_ids:
        return set()
    keys = [f"{PRESENCE_PREFIX}{uid}" for uid in user_ids]
    try:
        flags = await get_redis().mget(keys)
    except (RedisError, OSError) as exc:
        _survive("список присутствия", exc)
        return set()
    return {uid for uid, flag in zip(user_ids, flags, strict=True) if flag}


async def mark_viewing(conversation_id: int, user_id: int) -> None:
    key = f"{VIEWING_PREFIX}{conversation_id}"
    try:
        r = get_redis()
        await r.hset(key, str(user_id), "1")
        await r.expire(key, 90)
    except (RedisError, OSError) as exc:
        _survive("отметка просмотра", exc)


async def viewers(conversation_id: int) -> list[int]:
    try:
        raw = await get_redis().hkeys(f"{VIEWING_PREFIX}{conversation_id}")
    except (RedisError, OSError) as exc:
        _survive("список просматривающих", exc)
        return []
    return [int(v) for v in raw]
