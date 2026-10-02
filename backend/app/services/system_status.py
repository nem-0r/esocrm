"""Состояние сервера для руководителя: диск, нагрузка, шлюз, план ресурсов.

Данные берёт и приложение (диск, нагрузка — через регулятор шлюза), и база (аккаунты,
процессы шлюза). Всё, что требует внимания, собирается в `alerts` с готовым текстом —
интерфейс только показывает его и ничего не додумывает.
"""

import os
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import hostinfo
from app.gateway import governor, lease
from app.models import TelegramAccount
from app.services import settings_service

GB = governor.GB


def _total_memory_mb() -> int | None:
    raw = hostinfo._read_text("/proc/meminfo")  # noqa: SLF001 — тот же модуль-хелпер
    if raw is None:
        return None
    for line in raw.splitlines():
        if line.startswith("MemTotal:"):
            return int(line.split()[1]) // 1024
    return None


def plan_info() -> dict[str, Any]:
    """Под какой размер сервера рассчитан последний применённый план ресурсов.

    Штамп плана выкатка кладёт в окружение (`deploy/resource_plan.py`); если сервер
    с тех пор вырос или уменьшился — план устарел, и руководителю предлагается его
    пересчитать. Нет штампа — плана ещё нет (старая выкатка): тоже не повод тревожить.
    """
    cores_raw = os.environ.get("ASTRA_PLAN_CORES", "").strip()
    ram_raw = os.environ.get("ASTRA_PLAN_RAM_MB", "").strip()
    cores_now = hostinfo.detected_cores()
    ram_now = _total_memory_mb()
    if not (cores_raw.isdigit() and ram_raw.isdigit()):
        return {"applied": False, "cores": cores_now, "ram_mb": ram_now, "stale": False}
    plan_cores, plan_ram = int(cores_raw), int(ram_raw)
    stale = cores_now != plan_cores or (
        ram_now is not None and abs(ram_now - plan_ram) > max(256, plan_ram // 10)
    )
    return {
        "applied": True,
        "cores": cores_now,
        "ram_mb": ram_now,
        "plan_cores": plan_cores,
        "plan_ram_mb": plan_ram,
        "stale": stale,
    }


async def build(db: AsyncSession) -> dict[str, Any]:
    current = governor.signals(force=True)
    now = datetime.now(UTC)

    accounts_total = int(
        await db.scalar(
            select(func.count())
            .select_from(TelegramAccount)
            .where(TelegramAccount.is_active.is_(True), TelegramAccount.deleted_at.is_(None))
        )
        or 0
    )
    # Без процесса шлюза дольше минуты: аренда не продлевается — аккаунт не принимает
    # сообщений. Меньше минуты — обычное дело при выкатке и подключении.
    unassigned = int(
        await db.scalar(
            select(func.count())
            .select_from(TelegramAccount)
            .where(
                TelegramAccount.is_active.is_(True),
                TelegramAccount.deleted_at.is_(None),
                (TelegramAccount.lease_until.is_(None))
                | (TelegramAccount.lease_until < now - timedelta(seconds=60)),
            )
        )
        or 0
    )
    workers = await lease.live_workers(db)
    policy = await settings_service.get_value(db, "history_media_policy")
    plan = plan_info()

    free_gb = current.disk_free / GB
    total_gb = current.disk_total / GB
    alerts: list[dict[str, str]] = []
    level = current.disk_level
    if level == governor.DiskLevel.TIGHT:
        alerts.append(
            {
                "level": "warning",
                "text": (
                    f"На диске мало места: свободно {free_gb:.0f} ГБ из {total_gb:.0f}. "
                    "Тяжёлые файлы из истории пока не скачиваются. Освободите место "
                    "или расширьте диск, пока не стало хуже."
                ),
            }
        )
    elif level == governor.DiskLevel.PAUSED:
        alerts.append(
            {
                "level": "critical",
                "text": (
                    f"На диске почти нет места: свободно {free_gb:.0f} ГБ из {total_gb:.0f}. "
                    "Скачивание файлов из истории приостановлено, оно продолжится само, "
                    "когда место появится. Освободите место или расширьте диск."
                ),
            }
        )
    elif level == governor.DiskLevel.CRITICAL:
        alerts.append(
            {
                "level": "critical",
                "text": (
                    f"Диск заполнен: свободно {free_gb:.1f} ГБ. Новые файлы не скачиваются, "
                    "загрузка файлов менеджерами отключена. Срочно освободите место — "
                    "иначе остановится база данных."
                ),
            }
        )
    if workers == 0:
        alerts.append(
            {
                "level": "critical",
                "text": "Шлюз Telegram не запущен: сообщения не принимаются и не отправляются.",
            }
        )
    elif unassigned:
        alerts.append(
            {
                "level": "warning",
                "text": (
                    f"Аккаунтов без процесса шлюза: {unassigned}. Они сейчас не принимают "
                    "сообщения. Если не пройдёт за несколько минут — сообщите разработчику."
                ),
            }
        )
    if plan["stale"]:
        alerts.append(
            {
                "level": "info",
                "text": (
                    "Размер сервера изменился: настройки памяти и базы ещё рассчитаны "
                    f"под {plan['plan_cores']} ядер и {plan['plan_ram_mb']} МБ. Они "
                    "пересчитаются при следующей выкатке (или сообщите разработчику — "
                    "это одна команда)."
                ),
            }
        )

    return {
        "disk": {
            "free_gb": round(free_gb, 1),
            "total_gb": round(total_gb, 1),
            "level": int(level),
            "label": governor.DISK_LABELS[level],
        },
        "pressure": None if current.pressure is None else round(current.pressure, 2),
        "cores": hostinfo.detected_cores(),
        "memory_mb": _total_memory_mb(),
        "gateway": {"workers": workers, "accounts": accounts_total, "unassigned": unassigned},
        "plan": plan,
        "history_media_policy": policy,
        "alerts": alerts,
    }
