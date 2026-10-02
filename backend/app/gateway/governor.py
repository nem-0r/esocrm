"""Регулятор нагрузки шлюза: что можно делать прямо сейчас, а что подождёт.

Зачем (docs/17-resource-autoscaling.md §5.2). Подтяжка истории и скачивание файлов —
фоновая тяжёлая работа. Без регулятора она «давит, пока не упадёт»: у одного аккаунта
8,6 ГБ файлов за пять месяцев, у десяти — ≈ 86 ГБ, а диск сервера 77 ГБ, и на нём же
лежит база. Заполненный диск — это остановка всей CRM. Регулятор собирает сигналы и
отвечает на вопросы «можно ли…»:

- **диск** — четыре уровня (хватает → тесно → пауза → критично); на каждом шлюз
  делает меньше, а файлы, которые нельзя скачать сейчас, не теряются: они остаются в
  очереди на докачку и скачиваются сами, когда место появится;
- **нагрузка** — процессор и память хоста и потолок памяти контейнера (`load.pressure`):
  чем занятее сервер, тем длиннее паузы подтяжки и ниже скорость скачивания — менеджеры
  и база важнее фоновой загрузки;
- **очередь подтяжек** — не больше нескольких одновременно на весь сервер (общий
  счётчик в базе): по решению D-21 аккаунты вводятся в строй постепенно, а Telegram
  не любит, когда десять сессий разом читают всю переписку.

Пороги по диску — доля с абсолютным минимумом, чтобы одинаково работать на диске
77 ГБ и на 1 ТБ. Решения — чистые функции от сигналов: их легко проверять без сервера.
"""

import asyncio
import contextlib
import json
import logging
import os
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import IntEnum, StrEnum

from sqlalchemy import text

from app.core import hostinfo
from app.core.config import settings
from app.gateway import load, topology

log = logging.getLogger("astra.gateway.governor")

MB = 1024 * 1024
GB = 1024 * MB

# ------------------------------------------------------------------ диск


class DiskLevel(IntEnum):
    OK = 0  # места хватает — всё как обычно
    TIGHT = 1  # тесно — тяжёлое из истории не скачиваем, руководителю предупреждение
    PAUSED = 2  # мало — скачивание файлов из истории на паузе
    CRITICAL = 3  # почти нет — не скачиваем никакие новые файлы


DISK_LABELS = {
    DiskLevel.OK: "места достаточно",
    DiskLevel.TIGHT: "места становится мало",
    DiskLevel.PAUSED: "места мало — скачивание файлов из истории приостановлено",
    DiskLevel.CRITICAL: "места почти нет — новые файлы не скачиваются",
}


def disk_thresholds(total: int) -> tuple[int, int, int]:
    """Границы уровней (тесно, пауза, критично) — свободное место в байтах.

    Доля диска, но не меньше абсолютного минимума: на диске 77 ГБ это 15 / 10 / 5 ГБ,
    на 200 ГБ — 20 / 12 / 6 ГБ. Растущая база и скачивание файлов не должны подходить
    к последним гигабайтам вплотную: Postgres на полном диске останавливается.
    """
    return (
        max(15 * GB, total // 10),
        max(10 * GB, total * 6 // 100),
        max(5 * GB, total * 3 // 100),
    )


def disk_level_for(free: int, total: int) -> DiskLevel:
    tight, paused, critical = disk_thresholds(total)
    if free < critical:
        return DiskLevel.CRITICAL
    if free < paused:
        return DiskLevel.PAUSED
    if free < tight:
        return DiskLevel.TIGHT
    return DiskLevel.OK


# -------------------------------------------------------------- сигналы


@dataclass(frozen=True, slots=True)
class Signals:
    disk_free: int
    disk_total: int
    disk_level: DiskLevel
    # Худшая из долей занятости процессора/памяти (шкала 0.6 «сдерживаемся» / 0.9 «стоп»).
    pressure: float | None
    taken_at: float


_CACHE_SECONDS = 5.0
_cache: Signals | None = None
DEFAULT_OVERRIDE_FILE = "/tmp/astra-probe-override.json"  # noqa: S108 — только вне продакшена


def _override() -> dict[str, float]:
    """Подмена сигналов для проверок на стенде (только вне продакшена).

    Файл с JSON (по умолчанию `/tmp/astra-probe-override.json`, путь меняется
    переменной `ASTRA_PROBE_OVERRIDE_FILE`): `disk_free_gb`, `disk_total_gb`, `pressure`.
    Нужен, чтобы проверять поведение при полном диске, не заполняя настоящий. В
    продакшене файл игнорируется — подменить сигналы сервера там нельзя.
    """
    if settings.is_production:
        return {}
    path = os.environ.get("ASTRA_PROBE_OVERRIDE_FILE", "").strip() or DEFAULT_OVERRIDE_FILE
    try:
        with open(path) as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    return {key: float(value) for key, value in data.items() if isinstance(value, int | float)}


def reset_cache() -> None:
    global _cache
    _cache = None


def signals(*, force: bool = False) -> Signals:
    """Текущие сигналы; не чаще раза в несколько секунд — вызывается на каждое сообщение."""
    global _cache
    now = time.monotonic()
    if not force and _cache is not None and now - _cache.taken_at < _CACHE_SECONDS:
        return _cache
    total, free = hostinfo.disk_usage("/")
    pressure = load.pressure()
    override = _override()
    if "disk_total_gb" in override:
        total = int(override["disk_total_gb"] * GB)
    if "disk_free_gb" in override:
        free = int(override["disk_free_gb"] * GB)
    if "pressure" in override:
        pressure = override["pressure"]
    _cache = Signals(
        disk_free=free,
        disk_total=total,
        disk_level=disk_level_for(free, total),
        pressure=pressure,
        taken_at=now,
    )
    return _cache


def disk_level() -> DiskLevel:
    return signals().disk_level


# --------------------------------------------------------- решения: файлы

# Файл тяжелее этого из истории при тесноте на диске не скачиваем; по настройке
# «Файлы из истории» тоже.
HEAVY_BYTES = 5 * MB
_HEAVY_KINDS = frozenset({"video"})
_MINIMAL_KINDS = frozenset({"photo", "voice", "sticker"})
POLICIES = ("all", "light", "minimal")

REASON_POLICY = "Не скачан по настройке «Файлы из истории» — откройте в Telegram"
REASON_DISK = "Ждёт свободного места на диске"


class MediaChoice(StrEnum):
    DOWNLOAD = "download"  # скачать сейчас
    DEFER = "defer"  # отложить: останется в очереди докачки и скачается, когда место будет
    SKIP = "skip"  # не скачивать совсем (настройка руководителя): только упоминание


@dataclass(frozen=True, slots=True)
class MediaDecision:
    choice: MediaChoice
    reason: str | None = None


def decide_media(
    *, kind: str | None, size: int, live: bool, policy: str, level: DiskLevel
) -> MediaDecision:
    """Скачивать ли файл сообщения.

    `live` — сообщение пришло вживую, а не из подтяжки истории: свежие файлы клиентов
    важнее и малы, их придерживаем только когда места почти нет. Настройка «Файлы из
    истории» (`policy`) касается только истории.
    """
    if level >= DiskLevel.CRITICAL:
        return MediaDecision(MediaChoice.DEFER, REASON_DISK)
    if live:
        return MediaDecision(MediaChoice.DOWNLOAD)
    heavy = kind in _HEAVY_KINDS or size > HEAVY_BYTES
    if policy == "minimal" and not (kind in _MINIMAL_KINDS and size <= HEAVY_BYTES):
        return MediaDecision(MediaChoice.SKIP, REASON_POLICY)
    if policy == "light" and heavy:
        return MediaDecision(MediaChoice.SKIP, REASON_POLICY)
    if level >= DiskLevel.PAUSED or (level == DiskLevel.TIGHT and heavy):
        return MediaDecision(MediaChoice.DEFER, REASON_DISK)
    return MediaDecision(MediaChoice.DOWNLOAD)


_POLICY_TTL = 30.0
_policy_cache: tuple[float, str] | None = None


async def history_policy() -> str:
    """Настройка руководителя «Файлы из истории»; не чаще раза в полминуты."""
    global _policy_cache
    now = time.monotonic()
    if _policy_cache is not None and now - _policy_cache[0] < _POLICY_TTL:
        return _policy_cache[1]
    value = "all"
    try:
        from app.core.db import SessionLocal
        from app.services import settings_service

        async with SessionLocal() as db:
            raw = await settings_service.get_value(db, "history_media_policy")
        if raw in POLICIES:
            value = str(raw)
    except Exception:  # noqa: BLE001 — нет настройки: ведём себя как раньше, а не падаем
        log.exception("Не прочитал настройку «Файлы из истории» — качаю всё, как раньше")
    _policy_cache = (now, value)
    return value


def reset_policy_cache() -> None:
    global _policy_cache
    _policy_cache = None


# ----------------------------------------------------------- решения: темп

DIALOG_PAUSE_SECONDS = 0.4
_RATE_MID = 20 * MB  # скорость скачивания из истории при заметной нагрузке, байт/с
_RATE_HIGH = 5 * MB  # при сильной


def _stress(pressure: float | None) -> float:
    """0 — сервер свободен, 1 — предел: доля пути от «сдерживаемся» до «стоп»."""
    if pressure is None or pressure <= load.LOW_WATERMARK:
        return 0.0
    span = load.HIGH_WATERMARK - load.LOW_WATERMARK
    return min(1.0, (pressure - load.LOW_WATERMARK) / span)


def dialog_pause(base: float = DIALOG_PAUSE_SECONDS) -> float:
    """Пауза между диалогами при подтяжке: ровный темп ради Telegram, а когда сервер
    занят — до четырёх раз длиннее, чтобы уступить менеджерам."""
    return base * (1 + 3 * _stress(signals().pressure))


async def pace_media(size: int) -> None:
    """Притормозить после скачанного из истории файла, если сервер занят.

    Свободен — без задержки. Заметная нагрузка — не быстрее 20 МБ/с, сильная — 5 МБ/с:
    изоляции диска на виртуальном сервере почти нет, и поток записи в хранилище файлов
    сказывается на базе.
    """
    pressure = signals().pressure
    if pressure is None or pressure <= load.LOW_WATERMARK:
        return
    rate = _RATE_HIGH if pressure >= load.HIGH_WATERMARK else _RATE_MID
    await asyncio.sleep(size / rate)


def backfill_limit() -> int | None:
    """Какие файлы фоновой докачке можно брать сейчас: `None` — любые, число — не
    крупнее этого размера в байтах, `0` — никакие."""
    current = signals()
    if current.disk_level >= DiskLevel.PAUSED or (
        current.pressure is not None and current.pressure >= load.HIGH_WATERMARK
    ):
        return 0
    if current.disk_level == DiskLevel.TIGHT:
        return HEAVY_BYTES
    return None


# ------------------------------------------------- очередь подтяжек истории

# Любое число, общее для всех процессов шлюза: советующая блокировка Postgres с этим
# ключом и номером места. Блокировка транзакционная — пропадает вместе с соединением,
# если процесс упал, и не может «утечь» в пул соединений.
_ADVISORY_KEY = 0x41535452  # «ASTR»


class SyncSlot:
    """Место в общей очереди подтяжек истории (`async with SyncSlot(...)`).

    Одновременно подтягивают историю не больше `topology.sync_slots()` аккаунтов на весь
    сервер (по всем процессам шлюза). Остальные ждут: так массовое подключение аккаунтов
    не превращается в десять одновременных чтений всей переписки.
    """

    def __init__(
        self,
        account_id: int,
        *,
        max_slots: int | None = None,
        poll_seconds: float = 5.0,
        stop: asyncio.Event | None = None,
        on_wait: Callable[[], Awaitable[None]] | None = None,
        key: int = _ADVISORY_KEY,
    ) -> None:
        self.account_id = account_id
        self.key = key
        self.on_wait = on_wait
        self.max_slots = max_slots if max_slots is not None else topology.sync_slots()
        self.poll_seconds = poll_seconds
        self.stop = stop
        self.slot: int | None = None
        self._conn = None

    async def __aenter__(self) -> "SyncSlot":
        from app.core.db import engine

        waited_since: float | None = None
        while True:
            conn = await engine.connect()
            try:
                for slot in range(self.max_slots):
                    got = await conn.scalar(
                        text("select pg_try_advisory_xact_lock(:key, :slot)"),
                        {"key": self.key, "slot": slot},
                    )
                    if got:
                        self._conn, self.slot = conn, slot
                        if waited_since is not None:
                            log.info(
                                "Подтяжка истории аккаунта %s дождалась места %s (ждала %.0f с)",
                                self.account_id,
                                slot,
                                time.monotonic() - waited_since,
                            )
                        return self
            except BaseException:
                await conn.close()
                raise
            await conn.rollback()
            await conn.close()
            if waited_since is None:
                waited_since = time.monotonic()
                log.info(
                    "Подтяжка истории аккаунта %s ждёт очереди: идёт максимум %s одновременно",
                    self.account_id,
                    self.max_slots,
                )
                if self.on_wait is not None:
                    with contextlib.suppress(Exception):
                        await self.on_wait()
            await self._wait()

    async def _wait(self) -> None:
        delay = self.poll_seconds * (0.7 + 0.6 * random.random())  # noqa: S311 — не криптография
        if self.stop is None:
            await asyncio.sleep(delay)
            return
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self.stop.wait(), timeout=delay)
        if self.stop.is_set():
            raise asyncio.CancelledError

    async def __aexit__(self, *_exc: object) -> None:
        conn, self._conn = self._conn, None
        if conn is not None:
            with contextlib.suppress(Exception):
                await conn.rollback()  # снимает транзакционную блокировку
            await conn.close()


# ------------------------------------------------------------ метрики

METRICS_INTERVAL_SECONDS = 60.0


def metrics_line(worker_id: str, accounts: int) -> str:
    """Одна строка для журнала: по ней видно, сколько на деле занимает аккаунт."""
    current = signals(force=True)
    working_set = hostinfo.cgroup_memory_working_set_bytes()
    limit = hostinfo.cgroup_memory_limit_bytes()
    rss = hostinfo.process_rss_mb()
    container = (
        f"{working_set // MB}/{limit // MB} МБ"
        if working_set is not None and limit
        else "нет данных"
    )
    pressure = "нет данных" if current.pressure is None else f"{current.pressure:.2f}"
    parts = [f"Метрики шлюза {worker_id}: аккаунтов {accounts}"]
    if rss is not None:
        parts.append(f"память процесса {rss:.0f} МБ")
    parts += [
        f"контейнер {container}",
        f"нагрузка {pressure}",
        f"диск свободно {current.disk_free / GB:.1f} из {current.disk_total / GB:.0f} ГБ "
        f"({DISK_LABELS[current.disk_level]})",
    ]
    return ", ".join(parts)


async def metrics_loop(stop: asyncio.Event, worker_id: str, held_account_ids) -> None:  # noqa: ANN001
    """Раз в минуту — строка метрик в журнал. Дёшево и нужно для калибровки: сколько
    памяти занимает один аккаунт, как растёт диск."""
    while not stop.is_set():
        try:
            accounts = len(await held_account_ids())
            log.info(metrics_line(worker_id, accounts))
            current = signals()
            if current.disk_level >= DiskLevel.TIGHT:
                log.warning(
                    "Диск: %s (свободно %.1f ГБ)",
                    DISK_LABELS[current.disk_level],
                    current.disk_free / GB,
                )
        except Exception:
            log.exception("Не собрал метрики шлюза")
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=METRICS_INTERVAL_SECONDS)
