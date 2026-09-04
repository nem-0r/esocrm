"""Точка входа планировщика: сроки сделок, предагрегаты, чистка."""

import asyncio
import logging

from app.core.config import settings

logging.basicConfig(level=settings.log_level)
log = logging.getLogger("astra.scheduler")


async def main() -> None:
    from app.scheduler.jobs import start_scheduler

    await start_scheduler()
    # Задачи живут в планировщике; держим процесс до сигнала остановки.
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
