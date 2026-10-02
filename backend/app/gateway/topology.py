"""Сколько процессов шлюза поднимать — по железу, которое видно контейнеру,
а не по числу, вписанному в docker-compose.

Зачем. Раньше число процессов задавалось руками в docker-compose (сколько
реплик) и было верным ровно для одного конкретного сервера. Апгрейд VPS —
больше ядер, больше памяти — требовал вручную пересчитывать и переписывать
файл. Здесь то же самое, но вычисляется при каждом старте контейнера: сервер
стал больше — следующий перезапуск сам поднимет больше процессов, без правки
конфигурации.

Одно ядро сознательно не отдаётся под шлюз: на нём же работают api/db/redis/
scheduler, и на однопроцессорной машине это не даёт шлюзу задушить всё
остальное. Меньше одного процесса не бывает — процесс есть всегда, даже на
машине с одним ядром.
"""

import os

from app.core.hostinfo import cgroup_memory_limit_mb, detected_cores

__all__ = ["detected_cores", "expected_workers", "worker_count"]

RESERVED_CORES = 1
MIN_WORKERS = 1
# Больше двенадцати процессов не имеет смысла: дальше упирается база, а каждый
# процесс стоит ≈ 130 МБ памяти (замер на проде, docs/17-resource-autoscaling.md §2).
MAX_WORKERS = 12
# Память процесса шлюза и супервизора (измерено на проде) и доля потолка памяти
# контейнера, которую можно отдать под сами процессы: остальное — под аккаунты
# (сессии Telegram) и всплески (скачивание файла до 20 МБ в память).
GATEWAY_PROCESS_MB = 128
GATEWAY_SUPERVISOR_MB = 94
PROCESSES_MEMORY_SHARE = 0.35


def worker_count() -> int:
    """Сколько процессов шлюза поднять в этом контейнере.

    `GATEWAY_WORKERS` — явное число для стендов, где шлюзу нечего делать, а
    память на счету (тестовый стенд CI, ноутбук разработчика): по процессу на
    ядро там — это сотни мегабайт впустую. На сервере переменная не задаётся,
    и число, как и прежде, считается по ядрам.
    """
    raw = os.environ.get("GATEWAY_WORKERS", "").strip()
    if raw.isdigit() and int(raw) >= MIN_WORKERS:
        return int(raw)
    by_cores = max(MIN_WORKERS, detected_cores() - RESERVED_CORES)
    by_memory = memory_bound_workers()
    count = by_cores if by_memory is None else min(by_cores, by_memory)
    return max(MIN_WORKERS, min(count, MAX_WORKERS))


def memory_bound_workers() -> int | None:
    """Сколько процессов выдерживает потолок памяти контейнера; `None` — потолка нет.

    Число процессов зависит только от ядер, и на сервере с 16 ядрами и прежним
    потолком 1,5 ГБ получилось бы 15 × 128 МБ ≈ 1,9 ГБ — ядро убило бы контейнер,
    все аккаунты переподключились бы разом. Ограничиваем тем, что влезает.
    """
    limit = cgroup_memory_limit_mb()
    if limit is None:
        return None
    room = limit * PROCESSES_MEMORY_SHARE - GATEWAY_SUPERVISOR_MB
    return max(MIN_WORKERS, int(room // GATEWAY_PROCESS_MB))


def expected_workers() -> int:
    """Сколько процессов шлюза поднимает контейнер вместе с этим.

    Супервизор кладёт число в окружение до запуска процессов (`worker.main`).
    Нужно, чтобы процесс при старте дождался соседей и не принял себя за
    единственного. Нет переменной (запуск без супервизора) — считаем, что он один.
    """
    raw = os.environ.get("GATEWAY_EXPECTED_WORKERS", "").strip()
    return int(raw) if raw.isdigit() and int(raw) >= MIN_WORKERS else 1
