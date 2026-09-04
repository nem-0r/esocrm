"""Точка входа процесса шлюза.

Цикл всегда один и тот же (`app.gateway.runner.run`) — демо-режим и боевой
режим отличаются только тем, что вернёт `get_provider()`: рабочую имитацию
или, пока настоящего MTProto нет, честный отказ вместо тихой заглушки.
"""

import asyncio
import logging

from app.core.config import settings
from app.gateway.runner import run

logging.basicConfig(level=settings.log_level)
log = logging.getLogger("astra.gateway")


async def main() -> None:
    if settings.demo_mode:
        log.info("Демо-режим: вход в аккаунты и отправка сообщений имитируются.")
    await run()


if __name__ == "__main__":
    asyncio.run(main())
