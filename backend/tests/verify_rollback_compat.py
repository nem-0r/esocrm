"""База остаётся читаемой для предыдущей версии.

Автооткат выкатки (deploy/deploy.sh) возвращает прошлый код на ту же базу —
миграции назад не катятся (D-28). Значит, всё, что новая версия записала,
прошлая обязана прочитать. Колонки тут не опасны: новые прошлый код просто не
видит. Опасны значения перечислений Postgres: незнакомое значение SQLAlchemy
прошлой версии не прочитает, и чат с таким сообщением перестанет открываться.

Запускается после всех наборов, которые пишут данные: смотрит на то, что они
оставили в базе.

Запуск: docker compose exec -T api python -m tests.verify_rollback_compat
"""

import asyncio
import sys

from sqlalchemy import text

from app.core.db import SessionLocal
from tests.support import Checks

# Значения, которые понимает версия до релиза 27.09.2026. Расширять — только
# когда на проде стоит версия, которая новые значения уже читает.
PREVIOUS_RELEASE_ENUMS: dict[tuple[str, str], set[str]] = {
    ("messages", "kind"): {"text", "photo", "video", "document", "voice", "service"},
}


async def run() -> int:
    c = Checks("Совместимость базы с предыдущей версией (автооткат)")
    async with SessionLocal() as db:
        for (table, column), known in PREVIOUS_RELEASE_ENUMS.items():
            used = set(
                (await db.execute(text(f"select distinct {column}::text from {table}"))).scalars()  # noqa: S608 — имена из константы выше
            )
            unknown = sorted(used - known)
            c.check(
                f"{table}.{column}: только значения, знакомые прошлой версии",
                not unknown,
                f"незнакомые: {unknown}",
            )
    return c.finish()


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
