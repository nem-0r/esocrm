"""Канал команд из API в шлюз.

Зачем он нужен. Живое подключение к Telegram держит процесс шлюза, а кнопку
«Подключить» нажимают в API. Вход в аккаунт нельзя разорвать между процессами:
код подтверждения выдаётся конкретному соединению, и подтвердить его должен тот
же процесс, который его запросил. Поэтому API не ходит в Telegram сам — он
просит об этом шлюз, который держит аренду аккаунта, и ждёт ответ.

Устроено на pub/sub, а не на очереди, намеренно: команда без исполнителя
бессмысленна. Если аккаунт сейчас никем не арендован, ждать выполнения нечего —
лучше сразу сказать «шлюз недоступен», чем оставить кнопку в задумчивости.

Потеря Redis ломает только команды, ради которых он и заведён: переписка,
сделки и история живут в PostgreSQL и от этого канала не зависят.
"""

import asyncio
import contextlib
import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from app.core.errors import AppError
from app.core.redis_bus import get_redis

log = logging.getLogger("astra.commands")

COMMAND_CHANNEL = "astra:gw:cmd"
REPLY_PREFIX = "astra:gw:reply:"

DEFAULT_TIMEOUT = 45.0
# Пауза перед переподключением к Redis, если канал команд оборвался.
RECONNECT_DELAY = 3.0


class GatewayUnavailable(AppError):
    """Шлюз не ответил: процесс не поднят, аренда не выдана или он занят.

    Наследуемся от AppError, чтобы руководитель увидел причину текстом, а не
    «внутреннюю ошибку»: недоступный шлюз — штатная ситуация, а не сбой кода.
    """

    def __init__(self, message: str) -> None:
        super().__init__(503, "gateway_unavailable", message)


class GatewayError(AppError):
    """Шлюз ответил отказом — текст пришёл от Telegram и годится для показа."""

    def __init__(self, message: str) -> None:
        super().__init__(422, "telegram_error", message)


async def call(
    account_id: int,
    command: str,
    args: dict[str, Any] | None = None,
    timeout: float = DEFAULT_TIMEOUT,  # noqa: ASYNC109 — ждём ответ по сети, свой предел уместен
) -> dict[str, Any]:
    """Выполнить команду на том шлюзе, который держит аккаунт, и дождаться ответа."""
    request_id = uuid.uuid4().hex
    redis = get_redis()
    pubsub = redis.pubsub()
    reply_channel = f"{REPLY_PREFIX}{request_id}"
    # Подписываемся до отправки: иначе быстрый шлюз ответит раньше, чем мы начнём слушать.
    await pubsub.subscribe(reply_channel)
    try:
        await redis.publish(
            COMMAND_CHANNEL,
            json.dumps(
                {
                    "id": request_id,
                    "account_id": account_id,
                    "command": command,
                    "args": args or {},
                }
            ),
        )
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise GatewayUnavailable(
                    "Шлюз Telegram не ответил. Проверьте, что он запущен и аккаунт активен."
                )
            raw = await pubsub.get_message(ignore_subscribe_messages=True, timeout=remaining)
            if raw is None or raw.get("type") != "message":
                continue
            payload = json.loads(raw["data"])
            if payload.get("ok"):
                return payload.get("result") or {}
            raise GatewayError(payload.get("error") or "Шлюз не смог выполнить команду")
    finally:
        await pubsub.unsubscribe(reply_channel)
        await pubsub.aclose()


Handler = Callable[[int, str, dict[str, Any]], Awaitable[dict[str, Any]]]


async def serve(
    handler: Handler,
    holds: Callable[[int], bool],
    stop: asyncio.Event,
) -> None:
    """Сторона шлюза: слушать команды и выполнять те, чьи аккаунты арендованы нами.

    `holds` спрашивается на каждую команду, а не один раз: аренда переезжает
    между процессами, и вчерашний держатель не должен отвечать за чужой аккаунт.

    Обрыв Redis не должен ронять шлюз: отправка сообщений и приём из Telegram
    от этого канала не зависят, поэтому здесь бесконечное переподключение,
    а не выход из процесса.
    """
    while not stop.is_set():
        try:
            await _serve_once(handler, holds, stop)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — канал восстанавливается сам
            log.warning(
                "Канал команд оборвался (%s), переподключаюсь через %s с", exc, RECONNECT_DELAY
            )
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=RECONNECT_DELAY)


async def _serve_once(handler: Handler, holds: Callable[[int], bool], stop: asyncio.Event) -> None:
    redis = get_redis()
    pubsub = redis.pubsub()
    await pubsub.subscribe(COMMAND_CHANNEL)
    log.info("Шлюз слушает команды в %s", COMMAND_CHANNEL)
    try:
        while not stop.is_set():
            raw = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if raw is None or raw.get("type") != "message":
                continue
            try:
                request = json.loads(raw["data"])
            except (ValueError, TypeError):
                continue
            account_id = int(request.get("account_id", 0))
            if not holds(account_id):
                continue
            reply_channel = f"{REPLY_PREFIX}{request['id']}"
            try:
                result = await handler(account_id, request["command"], request.get("args") or {})
                answer = {"ok": True, "result": result}
            except Exception as exc:  # noqa: BLE001 — текст ошибки уходит руководителю
                log.exception("Команда %s для аккаунта %s не выполнена", request, account_id)
                answer = {"ok": False, "error": str(exc)}
            await redis.publish(reply_channel, json.dumps(answer, default=str))
    finally:
        with contextlib.suppress(Exception):
            await pubsub.unsubscribe(COMMAND_CHANNEL)
            await pubsub.aclose()
