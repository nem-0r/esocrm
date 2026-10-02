#!/usr/bin/env python3
"""План ресурсов: размер сервера -> потолки памяти, веса процессора, настройки базы.

Зачем (docs/17-resource-autoscaling.md §5.3). Потолок памяти и вес процессора
контейнер сам изменить не может — их задаёт Docker при создании контейнера. Поэтому
после апгрейда сервера (больше ядер и памяти) процессы шлюза и api подстраиваются сами
(они считают ядра при старте), а потолки контейнеров и настройки Postgres оставались
прежними. Этот скрипт считает их по размеру сервера и записывает в **помеченный блок
файла `.env`**; `docker-compose.prod.yml` читает значения оттуда (с прежними значениями
по умолчанию), а `docker compose up -d` пересоздаёт только те сервисы, у которых
значения изменились. Блок лежит в `.env`, потому что compose читает его всегда, откуда
бы ни запускали — выкатка, резервное копирование, ручная команда.

Запуск:

    python3 deploy/resource_plan.py                    # показать план, ничего не меняя
    python3 deploy/resource_plan.py --check            # 0 — блок актуален, 10 — устарел
    python3 deploy/resource_plan.py --apply            # записать блок (10 — изменился)
    python3 deploy/resource_plan.py --restore ФАЙЛ     # вернуть блок, сохранённый --save-previous
    python3 deploy/resource_plan.py --cores 6 --ram-mb 16384 --accounts 30   # «а если сервер такой?»

Правила безопасности:

- **План никогда не опускается ниже нынешних проверенных значений** — только растёт;
- секреты и остальное содержимое `.env` не затрагиваются ни байтом, запись атомарная;
- `ASTRA_AUTOSIZE=off` в `.env` — выключатель: скрипт ничего не меняет;
- блок можно удалить руками — compose вернётся к значениям по умолчанию, равным нынешним.

Только стандартная библиотека: скрипт запускается на сервере без установки зависимостей.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile
from dataclasses import dataclass, field

VERSION = 1
BEGIN = (
    f"# >>> astra-resource-plan v{VERSION} — блок пишет deploy/resource_plan.py, "
    "руками не править >>>"
)
END = "# <<< astra-resource-plan <<<"
BEGIN_PREFIX = "# >>> astra-resource-plan"
END_PREFIX = "# <<< astra-resource-plan"

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_CHANGED = 10

# ----------------------------------------------------------- измерено на проде
# 01.10.2026, docker top / docker stats (docs/17-resource-autoscaling.md §2).
API_WORKER_MB = 126
API_HEADROOM_MB = 100  # запас на процесс api под всплески (загрузка файла, перекодирование)
API_BASE_MB = 300  # общие библиотеки, ffmpeg, супервизор
GW_PROCESS_MB = 128
GW_SUPERVISOR_MB = 94
# Допущение, пока не измерено на живых аккаунтах: одна сессия Telegram со всеми буферами.
# Откалибровать по строке «Метрики шлюза» из журнала после подключения аккаунтов.
ACCOUNT_MB = 60
GW_HEADROOM = 1.3
DEFAULT_ACCOUNTS = 30

# --------------------------------------------- нынешние значения: ниже не опускаемся
FLOOR_MB = {"api": 1536, "gateway": 1536, "db": 2048, "storage": 1024, "cache": 512}
FLOOR_SHARED_BUFFERS_MB = 512
FLOOR_EFFECTIVE_CACHE_MB = 1536
FLOOR_WORK_MEM_MB = 16
FLOOR_MAINTENANCE_MB = 64
FLOOR_MAX_CONNECTIONS = 100
# Остальные контейнеры стека (из docker-compose.prod.yml): учитываются в проверке суммы.
FIXED_CAPS_MB = {"scheduler": 512, "web": 192, "nginx": 256}

CPU_SHARES = {"db": 2048, "api": 1024, "gateway": 512, "storage": 512}


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


@dataclass
class Plan:
    cores: int
    ram_mb: int
    accounts: int
    api_workers: int = 0
    gateway_processes: int = 0
    values: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def mb(self, key: str) -> int:
        return int(self.values[key].rstrip("MmGg")) * (1024 if self.values[key][-1] in "Gg" else 1)


def _mb(value: float) -> str:
    return f"{int(round(value))}M"


def compute(cores: int, ram_mb: int, accounts: int = DEFAULT_ACCOUNTS) -> Plan:
    """Посчитать план для сервера из `cores` ядер и `ram_mb` МБ памяти."""
    if cores < 1 or ram_mb < 512 or accounts < 1:
        raise ValueError(f"нелепый размер сервера: {cores} ядер, {ram_mb} МБ, {accounts} аккаунтов")
    plan = Plan(cores=cores, ram_mb=ram_mb, accounts=accounts)

    plan.api_workers = int(clamp(cores // 2, 2, 8))
    plan.gateway_processes = int(clamp(cores - 1, 1, 12))

    api_mb = max(
        FLOOR_MB["api"],
        API_BASE_MB + plan.api_workers * (API_WORKER_MB + API_HEADROOM_MB),
    )
    gateway_need = (GW_SUPERVISOR_MB + plan.gateway_processes * GW_PROCESS_MB + accounts * ACCOUNT_MB)
    gateway_mb = max(FLOOR_MB["gateway"], gateway_need * GW_HEADROOM)
    shared_buffers = int(clamp(round(0.25 * ram_mb), FLOOR_SHARED_BUFFERS_MB, 8192))
    # Потолок базы — вдвое больше shared_buffers: внутри потолка помимо буферов живут
    # соединения, рабочая память запросов и кеш файлов самой базы.
    db_mb = max(FLOOR_MB["db"], clamp(2 * shared_buffers, 2048, 32768))
    storage_mb = max(FLOOR_MB["storage"], clamp(round(0.06 * ram_mb), 1024, 4096))
    cache_max = int(clamp(round(0.03 * ram_mb), 384, 1536))
    cache_mb = max(FLOOR_MB["cache"], round(cache_max * 1.35))
    effective_cache = max(FLOOR_EFFECTIVE_CACHE_MB, round(0.70 * ram_mb))
    maintenance = int(clamp(ram_mb // 16, FLOOR_MAINTENANCE_MB, 2048))
    parallel_per_gather = int(clamp(cores // 2, 2, 4))
    # Формула pgtune для нагрузки «веб»: оперативная память минус shared_buffers,
    # поделённая на число соединений, умноженное на три узла плана, и на параллельность.
    work_mem = int(
        clamp(
            (ram_mb - shared_buffers) / (150 * 3) / parallel_per_gather,
            FLOOR_WORK_MEM_MB,
            64,
        )
    )
    shm = int(clamp(round(0.03 * ram_mb), 256, 2048))

    plan.values = {
        "ASTRA_PLAN_VERSION": str(VERSION),
        "ASTRA_PLAN_CORES": str(cores),
        "ASTRA_PLAN_RAM_MB": str(ram_mb),
        "ASTRA_PLAN_ACCOUNTS": str(accounts),
        # база
        "ASTRA_DB_MEMORY": _mb(db_mb),
        "ASTRA_DB_SHM": f"{shm}m",
        "ASTRA_DB_CPU_SHARES": str(CPU_SHARES["db"]),
        "ASTRA_DB_SHARED_BUFFERS": f"{shared_buffers}MB",
        "ASTRA_DB_EFFECTIVE_CACHE_SIZE": f"{effective_cache}MB",
        "ASTRA_DB_WORK_MEM": f"{work_mem}MB",
        "ASTRA_DB_MAINTENANCE_WORK_MEM": f"{maintenance}MB",
        "ASTRA_DB_MAX_CONNECTIONS": str(max(FLOOR_MAX_CONNECTIONS, 150)),
        "ASTRA_DB_MAX_WAL_SIZE": "4GB",
        # приложение
        "ASTRA_API_MEMORY": _mb(api_mb),
        "ASTRA_API_CPU_SHARES": str(CPU_SHARES["api"]),
        "ASTRA_GATEWAY_MEMORY": _mb(gateway_mb),
        "ASTRA_GATEWAY_CPU_SHARES": str(CPU_SHARES["gateway"]),
        # хранилище файлов и кеш
        "ASTRA_STORAGE_MEMORY": _mb(storage_mb),
        "ASTRA_STORAGE_CPU_SHARES": str(CPU_SHARES["storage"]),
        "ASTRA_CACHE_MEMORY": _mb(cache_mb),
        "ASTRA_CACHE_MAXMEMORY": f"{cache_max}mb",
    }
    _check(plan)
    return plan


def _check(plan: Plan) -> None:
    """Здравый смысл: нелепый план лучше отвергнуть, чем применить.

    Ошибка — исключение (план не пишется); подозрительное, но допустимое — предупреждение.
    """
    values = plan.values
    mb = plan.mb
    shared = int(values["ASTRA_DB_SHARED_BUFFERS"].removesuffix("MB"))
    if shared * 2 > mb("ASTRA_DB_MEMORY"):
        raise ValueError("shared_buffers занимает больше половины потолка памяти базы")
    maintenance = int(values["ASTRA_DB_MAINTENANCE_WORK_MEM"].removesuffix("MB"))
    if maintenance * 4 > mb("ASTRA_DB_MEMORY"):
        raise ValueError("maintenance_work_mem слишком велик для потолка памяти базы")
    for key, floor in (
        ("ASTRA_API_MEMORY", FLOOR_MB["api"]),
        ("ASTRA_GATEWAY_MEMORY", FLOOR_MB["gateway"]),
        ("ASTRA_DB_MEMORY", FLOOR_MB["db"]),
        ("ASTRA_STORAGE_MEMORY", FLOOR_MB["storage"]),
        ("ASTRA_CACHE_MEMORY", FLOOR_MB["cache"]),
    ):
        if mb(key) < floor:
            raise ValueError(f"{key} ниже нынешнего проверенного значения {floor} МБ")

    caps = (
        mb("ASTRA_API_MEMORY")
        + mb("ASTRA_GATEWAY_MEMORY")
        + mb("ASTRA_DB_MEMORY")
        + mb("ASTRA_STORAGE_MEMORY")
        + mb("ASTRA_CACHE_MEMORY")
        + sum(FIXED_CAPS_MB.values())
    )
    # Потолки — это предельные значения, а не занятая память: все сразу они не заняты.
    # Но сумма сильно больше памяти сервера — повод предупредить.
    if caps > 1.5 * plan.ram_mb:
        plan.warnings.append(
            f"сумма потолков памяти ({caps} МБ) заметно больше памяти сервера "
            f"({plan.ram_mb} МБ) — защищают базу приоритеты (oom_score_adj), но на таком "
            "сервере лучше брать больше памяти или меньше аккаунтов"
        )
    expected = (
        (plan.api_workers * API_WORKER_MB + 30)
        + (GW_SUPERVISOR_MB + plan.gateway_processes * GW_PROCESS_MB + plan.accounts * ACCOUNT_MB)
        + (150 + 0.2 * shared)
        + 500  # хранилище файлов под нагрузкой
        + 81  # планировщик, веб, nginx, redis
        + max(1024, 0.12 * plan.ram_mb)  # система и кеш файлов
    )
    if expected > 0.85 * plan.ram_mb:
        plan.warnings.append(
            f"ожидаемое потребление при {plan.accounts} аккаунтах ≈ {round(expected)} МБ — "
            f"больше 85 % памяти сервера ({plan.ram_mb} МБ): для такого числа аккаунтов "
            "нужен сервер с бо́льшей памятью"
        )


# --------------------------------------------------------------- файл .env


def read_text(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    except FileNotFoundError:
        return ""


def split_env(text: str) -> tuple[str, dict[str, str], bool]:
    """(содержимое без блока, значения из блока, найден ли блок)."""
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    block: dict[str, str] = {}
    inside = False
    found = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(BEGIN_PREFIX):
            inside = True
            found = True
            continue
        if inside and stripped.startswith(END_PREFIX):
            inside = False
            continue
        if inside:
            if "=" in stripped and not stripped.startswith("#"):
                key, _, value = stripped.partition("=")
                block[key.strip()] = value.strip()
            continue
        out.append(line)
    # Убираем пустые строки, которыми мы же отбивали блок от остального файла.
    while out and not out[-1].strip():
        out.pop()
    return "".join(out), block, found


def env_value(text: str, key: str) -> str | None:
    """Значение переменной из части файла вне блока (последнее побеждает)."""
    pattern = re.compile(rf"^\s*{re.escape(key)}=(.*)$", re.MULTILINE)
    matches = pattern.findall(text)
    if not matches:
        return None
    value = matches[-1].strip().rstrip("\r")
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    return value


def render_block(values: dict[str, str]) -> str:
    body = "".join(f"{key}={value}\n" for key, value in values.items())
    return f"{BEGIN}\n{body}{END}\n"


def compose_env(rest: str, block_text: str) -> str:
    base = rest.rstrip("\n")
    separator = "\n\n" if base else ""
    return f"{base}{separator}{block_text}"


def atomic_write(path: str, content: str) -> None:
    """Записать файл целиком или не записывать вовсе; права и владелец прежние."""
    directory = os.path.dirname(os.path.abspath(path))
    fd, temp = tempfile.mkstemp(prefix=".env.", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if os.path.exists(path):
            info = os.stat(path)
            os.chmod(temp, info.st_mode & 0o7777)
            try:
                os.chown(temp, info.st_uid, info.st_gid)
            except PermissionError:
                pass  # не root — владелец и так мы
        else:
            os.chmod(temp, 0o600)
        os.replace(temp, path)
    except BaseException:
        if os.path.exists(temp):
            os.unlink(temp)
        raise


# ------------------------------------------------------------------ железо


def detect() -> tuple[int, int]:
    """(ядра, память МБ) этого сервера."""
    try:
        cores = len(os.sched_getaffinity(0))
    except AttributeError:
        cores = os.cpu_count() or 1
    ram_mb = 0
    try:
        with open("/proc/meminfo") as handle:
            for line in handle:
                if line.startswith("MemTotal:"):
                    ram_mb = int(line.split()[1]) // 1024
                    break
    except OSError:
        pass
    if ram_mb <= 0:
        raise RuntimeError("не удалось определить объём памяти (нет /proc/meminfo) — укажите --ram-mb")
    return cores, ram_mb


# -------------------------------------------------------------------- вывод


def describe(plan: Plan) -> str:
    v = plan.values
    lines = [
        f"Сервер: {plan.cores} ядер, {plan.ram_mb} МБ памяти; план под {plan.accounts} аккаунтов",
        f"Процессы (считаются сами при старте контейнеров): api {plan.api_workers}, "
        f"шлюз Telegram {plan.gateway_processes}",
        "",
        "Потолки памяти:  "
        f"api {v['ASTRA_API_MEMORY']}  шлюз {v['ASTRA_GATEWAY_MEMORY']}  база {v['ASTRA_DB_MEMORY']}  "
        f"файлы {v['ASTRA_STORAGE_MEMORY']}  redis {v['ASTRA_CACHE_MEMORY']}",
        "Postgres:        "
        f"shared_buffers {v['ASTRA_DB_SHARED_BUFFERS']}  effective_cache_size "
        f"{v['ASTRA_DB_EFFECTIVE_CACHE_SIZE']}  work_mem {v['ASTRA_DB_WORK_MEM']}  "
        f"max_connections {v['ASTRA_DB_MAX_CONNECTIONS']}  max_wal_size {v['ASTRA_DB_MAX_WAL_SIZE']}",
        "Веса процессора: "
        f"база {v['ASTRA_DB_CPU_SHARES']}  api {v['ASTRA_API_CPU_SHARES']}  "
        f"шлюз {v['ASTRA_GATEWAY_CPU_SHARES']}  файлы {v['ASTRA_STORAGE_CPU_SHARES']}",
    ]
    for warning in plan.warnings:
        lines.append(f"ВНИМАНИЕ: {warning}")
    return "\n".join(lines)


def diff_text(old: dict[str, str], new: dict[str, str]) -> str:
    keys = [key for key in new if old.get(key) != new[key]] + [
        key for key in old if key not in new
    ]
    if not keys:
        return "значения не изменились"
    rows = []
    for key in keys:
        rows.append(f"  {key}: {old.get(key, '—')} → {new.get(key, '—')}")
    return "\n".join(rows)


# --------------------------------------------------------------------- main


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="План ресурсов под размер сервера")
    parser.add_argument("--env-file", default=".env", help="файл .env (по умолчанию ./.env)")
    parser.add_argument("--cores", type=int, help="подставить число ядер вместо измеренного")
    parser.add_argument("--ram-mb", type=int, help="подставить память МБ вместо измеренной")
    parser.add_argument("--accounts", type=int, help="под сколько аккаунтов считать (30)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="записать блок в .env")
    mode.add_argument("--check", action="store_true", help="только проверить, актуален ли блок")
    mode.add_argument("--restore", metavar="ФАЙЛ", help="вернуть блок, сохранённый --save-previous")
    parser.add_argument("--save-previous", metavar="ФАЙЛ", help="сохранить прежний блок (для отката)")
    args = parser.parse_args(argv)

    env_text = read_text(args.env_file)
    rest, current, found = split_env(env_text)

    if args.restore:
        previous = read_text(args.restore).strip("\n")
        new_content = compose_env(rest, previous + "\n" if previous else "")
        if new_content.rstrip("\n") == env_text.rstrip("\n"):
            print("блок уже такой, как в сохранённой копии")
            return EXIT_OK
        atomic_write(args.env_file, new_content.rstrip("\n") + "\n")
        print("блок плана ресурсов возвращён к сохранённому")
        return EXIT_CHANGED

    if (env_value(rest, "ASTRA_AUTOSIZE") or "").lower() in ("off", "0", "false", "no"):
        print("ASTRA_AUTOSIZE=off — план ресурсов выключен, ничего не меняю")
        return EXIT_OK

    try:
        detected = detect() if not (args.cores and args.ram_mb) else (args.cores, args.ram_mb)
        cores = args.cores or detected[0]
        ram_mb = args.ram_mb or detected[1]
        accounts_raw = env_value(rest, "ASTRA_EXPECTED_ACCOUNTS")
        accounts = args.accounts or (
            int(accounts_raw) if accounts_raw and accounts_raw.isdigit() else DEFAULT_ACCOUNTS
        )
        plan = compute(cores, ram_mb, accounts)
    except (ValueError, RuntimeError) as exc:
        print(f"план ресурсов не посчитан: {exc}", file=sys.stderr)
        return EXIT_ERROR

    print(describe(plan))
    changed = (not found) or current != plan.values
    if args.check or not args.apply:
        print("\nБлок в .env:", "актуален" if not changed else "устарел или отсутствует")
        if changed:
            print(diff_text(current, plan.values))
        if args.check:
            return EXIT_CHANGED if changed else EXIT_OK
        print("\n(ничего не записано; для записи — флаг --apply)")
        return EXIT_OK

    if not changed:
        print("\nблок в .env уже актуален")
        return EXIT_OK
    print("\nИзменения:")
    print(diff_text(current, plan.values))
    if args.save_previous:
        previous_block = "".join(f"{key}={value}\n" for key, value in current.items())
        atomic_write(args.save_previous, (BEGIN + "\n" + previous_block + END + "\n") if found else "")
    atomic_write(args.env_file, compose_env(rest, render_block(plan.values)))
    print(f"\nблок записан в {args.env_file}")
    return EXIT_CHANGED


if __name__ == "__main__":
    sys.exit(main())
