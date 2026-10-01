"""Запуск api: число процессов uvicorn считается по железу (`core.sizing`).

`python -m app.core.serve` вместо длинной команды в docker-compose с числом
процессов, вписанным руками. Число кладётся в окружение до запуска: каждый
процесс строит свой пул соединений с базой и должен знать, на сколько делить
общий бюджет (`core.db._pool_kwargs`).
"""

import os

import uvicorn

from app.core import sizing


def main() -> None:
    workers = sizing.api_workers()
    os.environ["API_WORKERS_RESOLVED"] = str(workers)
    print(
        f"Запускаю api: процессов {workers} "
        f"(ядер: {sizing.detected_cores()}, потолок памяти: {sizing.memory_limit_mb()} МБ)",
        flush=True,
    )
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",  # noqa: S104 — слушает внутри контейнера, наружу только через nginx
        port=int(os.environ.get("API_PORT", "8000")),
        proxy_headers=True,
        forwarded_allow_ips="*",
        workers=workers,
    )


if __name__ == "__main__":
    main()
