"""Сколько процессов api поднимать — по железу, которое видно контейнеру.

Так же, как у шлюза (`gateway/topology.py`): число считается при каждом старте,
а не вписано в docker-compose. Сервер стал больше — следующий запуск сам поднимет
больше процессов, переписывать конфигурацию не нужно.

Что считается:

- по ядрам: половина видимых ядер, но не меньше двух — вторая половина остаётся
  базе, шлюзу и кешу, которые живут на том же сервере;
- по памяти: процесс api занимает ≈ 250 МБ, и контейнер не должен упереться в свой
  потолок памяти (его убьёт ядро — это хуже, чем меньше процессов);
- не больше 8: дальше узким местом становится база, а не api.

На нынешнем сервере (4 ядра, потолок 1,5 ГБ) получается 2 — ровно столько, сколько
было вписано руками, поведение не меняется. Явное число — `API_WORKERS`.
"""

import os

from app.gateway.topology import detected_cores

MIN_WORKERS = 2
MAX_WORKERS = 8
# Что занимает процесс api и что — контейнер сам по себе (общие библиотеки, ffmpeg).
MB_PER_WORKER = 250
MB_BASE = 300


def _read_int(path: str) -> int | None:
    try:
        with open(path) as handle:
            raw = handle.read().strip()
    except OSError:
        return None
    return int(raw) if raw.isdigit() else None


def memory_limit_mb() -> int | None:
    """Потолок памяти контейнера (cgroup v2, затем v1), МБ. `None` — не задан."""
    limit = _read_int("/sys/fs/cgroup/memory.max") or _read_int(
        "/sys/fs/cgroup/memory/memory.limit_in_bytes"
    )
    # Без ограничения cgroup отдаёт «max» или заведомо огромное число.
    if limit is None or limit > 1 << 50:
        return None
    return limit // (1024 * 1024)


def api_workers() -> int:
    raw = os.environ.get("API_WORKERS", "").strip()
    if raw.isdigit() and int(raw) >= 1:
        return min(int(raw), 32)
    by_cores = max(MIN_WORKERS, detected_cores() // 2)
    limit = memory_limit_mb()
    by_memory = max(1, (limit - MB_BASE) // MB_PER_WORKER) if limit else MAX_WORKERS
    return max(1, min(by_cores, by_memory, MAX_WORKERS))


if __name__ == "__main__":
    print(api_workers())
