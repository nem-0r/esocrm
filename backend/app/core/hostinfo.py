"""Что процесс видит о железе: ядра, память хоста и своего контейнера, диск, нагрузка.

Всё читается из `/proc` и `/sys/fs/cgroup` — без новых зависимостей. Не Linux
(Mac разработчика вне Docker) — функции возвращают `None`, и вызывающие просто не
используют недостающий сигнал.

Для чего нужно каждое:

- ядра — сколько процессов поднимать (`gateway/topology.py`, `core/sizing.py`);
- потолок и занятая память контейнера — не брать работу, когда контейнер подходит к
  своему потолку: за ним приходит ядро и убивает контейнер целиком;
- память и процессор хоста — не нагружать сервер, на котором рядом база и менеджеры;
- диск — не заполнить его скачиванием файлов (на том же диске лежит база).
"""

import os
import shutil

_MB = 1024 * 1024


def detected_cores() -> int:
    """Ядра, реально видные ЭТОМУ процессу.

    `sched_getaffinity` учитывает ограничение контейнера через cpuset (если
    оно есть), `cpu_count` — честное число ядер хоста в остальных случаях.
    Не имеет отношения к Docker `cpus:` — тот лимит делит время ядра, а не
    прячет ядра из вида, и никак не сузил бы это число."""
    try:
        return len(os.sched_getaffinity(0))
    except AttributeError:
        # sched_getaffinity есть только на Linux — на другой платформе просто
        # берём общее число ядер, какое видит процесс.
        return os.cpu_count() or 1


def _read_text(path: str) -> str | None:
    try:
        with open(path) as handle:
            return handle.read().strip()
    except OSError:
        return None


def _read_int(path: str) -> int | None:
    raw = _read_text(path)
    return int(raw) if raw is not None and raw.isdigit() else None


def _stat_value(path: str, key: str) -> int | None:
    raw = _read_text(path)
    if raw is None:
        return None
    for line in raw.splitlines():
        name, _, value = line.partition(" ")
        if name == key and value.strip().isdigit():
            return int(value)
    return None


def cgroup_memory_limit_bytes() -> int | None:
    """Потолок памяти контейнера (cgroup v2, затем v1). `None` — не задан."""
    limit = _read_int("/sys/fs/cgroup/memory.max") or _read_int(
        "/sys/fs/cgroup/memory/memory.limit_in_bytes"
    )
    # Без ограничения cgroup отдаёт «max» или заведомо огромное число.
    if limit is None or limit > 1 << 50:
        return None
    return limit


def cgroup_memory_limit_mb() -> int | None:
    limit = cgroup_memory_limit_bytes()
    return None if limit is None else limit // _MB


def cgroup_memory_working_set_bytes() -> int | None:
    """Сколько памяти контейнер занимает по-настоящему: без кеша файлов.

    Так же считает `docker stats`: использование минус неактивный кеш. Кеш ядро
    вытеснит само, поэтому приближение к потолку по полному использованию (оно
    включает кеш) давало бы ложную тревогу.
    """
    current = _read_int("/sys/fs/cgroup/memory.current")
    if current is not None:
        cache = _stat_value("/sys/fs/cgroup/memory.stat", "inactive_file") or 0
        return max(0, current - cache)
    usage = _read_int("/sys/fs/cgroup/memory/memory.usage_in_bytes")
    if usage is not None:
        cache = _stat_value("/sys/fs/cgroup/memory/memory.stat", "total_inactive_file") or 0
        return max(0, usage - cache)
    return None


def container_memory_ratio() -> float | None:
    """Доля занятого потолка памяти контейнера; `None`, если потолка нет."""
    limit = cgroup_memory_limit_bytes()
    used = cgroup_memory_working_set_bytes()
    if not limit or used is None:
        return None
    return used / limit


def host_memory_ratio() -> float | None:
    """Доля занятой памяти хоста: 1 − доступно/всего, по `/proc/meminfo`.

    `MemAvailable`, не `MemFree` — то, что ядро само считает реально
    свободным с учётом вытесняемого кэша; то же самое число показывает
    `free -h` в столбце «available».
    """
    raw = _read_text("/proc/meminfo")
    if raw is None:
        return None
    values: dict[str, int] = {}
    for line in raw.splitlines():
        key, _, rest = line.partition(":")
        if key in ("MemTotal", "MemAvailable"):
            values[key] = int(rest.split()[0])  # килобайты
    total = values.get("MemTotal")
    if not total:
        return None
    return 1 - values.get("MemAvailable", 0) / total


def host_load_ratio() -> float | None:
    """Доля занятости процессора хоста: минутный load average на число ядер."""
    try:
        load1, _, _ = os.getloadavg()
    except (OSError, AttributeError):
        return None
    return load1 / (os.cpu_count() or 1)


def process_rss_mb() -> float | None:
    """Резидентная память этого процесса, МБ (для журнала метрик шлюза)."""
    raw = _read_text("/proc/self/status")
    if raw is None:
        return None
    for line in raw.splitlines():
        if line.startswith("VmRSS:"):
            return int(line.split()[1]) / 1024
    return None


def disk_usage(path: str = "/") -> tuple[int, int]:
    """(всего, свободно) в байтах для диска, на котором лежат данные.

    Внутри контейнера корень показывает диск хоста (проверено на проде: 76/60 ГБ в
    контейнере против 77/61 на хосте), поэтому отдельных монтирований не нужно.
    """
    total, _, free = shutil.disk_usage(path)
    return total, free
