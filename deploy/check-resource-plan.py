#!/usr/bin/env python3
"""Проверка плана ресурсов вместе с docker-compose.prod.yml, без запуска контейнеров.

Для каждого размера сервера: считает план, кладёт блок во временный .env, просит compose
собрать конфигурацию и сверяет с планом то, что реально попадёт в контейнеры: потолки
памяти, веса процессора, защиту от OOM, размер /dev/shm и флаги Postgres. Без блока
значения должны быть ровно нынешними (так compose ведёт себя, пока план не применён).

Запуск (нужны docker и compose v2.23+, демон не нужен): python3 deploy/check-resource-plan.py
CI вызывает его в задаче «Интеграция».
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "deploy"))
import resource_plan as rp  # noqa: E402

BASE_ENV = (
    "DOMAIN=crm.example.ru\nPOSTGRES_USER=u\nPOSTGRES_PASSWORD=p\nPOSTGRES_DB=d\n"
    "S3_ACCESS_KEY=a\nS3_SECRET_KEY=s\n"
)
PROFILES = [(4, 7934), (6, 16384), (8, 32768), (16, 65536)]
failed: list[str] = []


def check(title: str, condition: bool, detail: object = "") -> None:
    print(f"  {'✓' if condition else '✗'} {title}" + ("" if condition else f" — {detail}"))
    if not condition:
        failed.append(title)


def to_bytes(value: object) -> int:
    text = str(value)
    if text.isdigit():
        return int(text)
    units = {"K": 1024, "M": 1024**2, "G": 1024**3}
    return int(text[:-1]) * units[text[-1].upper()]


def compose_config(env_path: str) -> dict:
    result = subprocess.run(
        ["docker", "compose", "-f", os.path.join(ROOT, "docker-compose.prod.yml"),
         "--env-file", env_path, "config", "--format", "json"],
        capture_output=True, text=True, cwd=ROOT,
    )
    if result.returncode != 0:
        raise SystemExit(f"compose отверг конфигурацию: {result.stderr}")
    return json.loads(result.stdout)


def limit(service: dict) -> int:
    return to_bytes(service["deploy"]["resources"]["limits"]["memory"])


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        env_path = os.path.join(tmp, ".env")

        print("Без плана: значения ровно нынешние")
        with open(env_path, "w") as handle:
            handle.write(BASE_ENV)
        services = compose_config(env_path)["services"]
        check("база: потолок 2 ГБ, веса и защита от OOM заданы",
              limit(services["db"]) == 2 * 1024**3 and str(services["db"]["cpu_shares"]) == "2048"
              and str(services["db"]["oom_score_adj"]) == "-500", services["db"].get("deploy"))
        check("api: 1,5 ГБ; шлюз: 1,5 ГБ", limit(services["api"]) == 1536 * 1024**2
              and limit(services["gateway"]) == 1536 * 1024**2)
        check("хранилище файлов 1 ГБ, redis 512 МБ", limit(services["storage"]) == 1024**3
              and limit(services["cache"]) == 512 * 1024**2)
        command = " ".join(services["db"]["command"]) if isinstance(services["db"]["command"], list) else services["db"]["command"]
        check("Postgres: прежние shared_buffers 512MB, max_connections 100",
              "shared_buffers=512MB" in command and "max_connections=100" in command, command)
        check("у базы и хранилища больше нет жёсткого потолка процессора",
              "cpus" not in services["db"]["deploy"]["resources"]["limits"]
              and "cpus" not in services["storage"]["deploy"]["resources"]["limits"])
        check("api и шлюз без потолка процессора",
              "cpus" not in services["api"]["deploy"]["resources"]["limits"]
              and "cpus" not in services["gateway"]["deploy"]["resources"]["limits"])

        for cores, ram in PROFILES:
            print(f"\nСервер {cores} ядер / {ram // 1024} ГБ")
            with open(env_path, "w") as handle:
                handle.write(BASE_ENV)
            code = subprocess.run(
                [sys.executable, os.path.join(ROOT, "deploy", "resource_plan.py"), "--apply",
                 "--env-file", env_path, "--cores", str(cores), "--ram-mb", str(ram)],
                capture_output=True, text=True,
            )
            check("план посчитан и записан", code.returncode == rp.EXIT_CHANGED, code.stderr or code.stdout[-200:])
            plan = rp.compute(cores, ram, rp.DEFAULT_ACCOUNTS)
            services = compose_config(env_path)["services"]
            for service, key in (("db", "ASTRA_DB_MEMORY"), ("api", "ASTRA_API_MEMORY"),
                                 ("gateway", "ASTRA_GATEWAY_MEMORY"), ("storage", "ASTRA_STORAGE_MEMORY"),
                                 ("cache", "ASTRA_CACHE_MEMORY")):
                check(f"{service}: потолок памяти из плана ({plan.values[key]})",
                      limit(services[service]) == to_bytes(plan.values[key]),
                      (limit(services[service]), plan.values[key]))
            command = services["db"]["command"]
            command = " ".join(command) if isinstance(command, list) else command
            for flag in ("ASTRA_DB_SHARED_BUFFERS", "ASTRA_DB_MAX_CONNECTIONS", "ASTRA_DB_EFFECTIVE_CACHE_SIZE",
                         "ASTRA_DB_WORK_MEM", "ASTRA_DB_MAX_WAL_SIZE"):
                setting = flag.removeprefix("ASTRA_DB_").lower()
                check(f"Postgres: {setting}={plan.values[flag]} в команде базы",
                      f"{setting}={plan.values[flag]}" in command, command)
            check("shm базы из плана", to_bytes(services["db"]["shm_size"]) == to_bytes(plan.values["ASTRA_DB_SHM"].replace("m", "M")))
            check("веса: база > api > шлюз",
                  int(services["db"]["cpu_shares"]) > int(services["api"]["cpu_shares"]) > int(services["gateway"]["cpu_shares"]))
            check("шлюзу, а не базе, достаётся при нехватке памяти",
                  int(services["gateway"]["oom_score_adj"]) > 0 > int(services["db"]["oom_score_adj"]))
            check("штамп плана доезжает до контейнеров api (для страницы состояния)",
                  "ASTRA_PLAN_CORES" in open(env_path).read())
    print(f"\nИтог: {'всё сошлось' if not failed else str(len(failed)) + ' не сошлось'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
