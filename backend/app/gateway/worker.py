"""Точка входа процесса шлюза — на деле супервизор нескольких процессов.

Раньше здесь поднимался один процесс на весь контейнер, а число контейнеров
задавалось руками в docker-compose (`deploy.replicas`). Теперь наоборот:
контейнер один, а сколько внутри него процессов — решает `topology.worker_count()`
по числу ядер, которые реально видны ЭТОМУ контейнеру. Апгрейд сервера
(больше ядер) не требует трогать конфигурацию — следующий перезапуск
контейнера сам поднимет больше процессов.

Процессы, не потоки и не задачи asyncio: только отдельный процесс операционной
системы может по-настоящему занять отдельное ядро одновременно с другими —
GIL не даст этого сделать потокам одного процесса.

Каждый процесс — тот же цикл аренды, что был единственной точкой входа
раньше (`runner.run()`): он ничего не знает про то, что рядом работают
такие же процессы. Их безопасное сосуществование обеспечивает не эта точка
входа, а аренда аккаунтов через базу (`app/gateway/lease.py`, с гонкой,
закрытой на уровне SQL, `SELECT ... FOR UPDATE SKIP LOCKED`) — это было
проверено и работает независимо от того, в одном они контейнере или в разных.

Если один из процессов падает, супервизор поднимает вместо него новый — не
дожидаясь перезапуска всего контейнера: чужие аккаунты, которые держал
упавший процесс, и так подхватит любой другой живой процесс через 30 секунд
(протухание аренды), но нет причин ждать так долго, если можно поднять замену
почти сразу.
"""

import asyncio
import logging
import multiprocessing
import signal
import threading
from types import FrameType

from app.core.config import settings
from app.gateway import topology
from app.gateway.runner import run

logging.basicConfig(level=settings.log_level)
log = logging.getLogger("astra.gateway.supervisor")

# Как часто проверять, что все процессы живы, и раз в сколько секунд повторно
# логировать факт работы — то же, на что ориентирована аренда (heartbeat),
# чтобы упавший процесс подхватывался быстрее, чем истекла бы его аренда.
CHECK_INTERVAL_SECONDS = 5
# Сколько ждать процесс при штатной остановке: он должен успеть отпустить
# аренду своих аккаунтов (release_all в runner.run()), не просто оборваться.
SHUTDOWN_TIMEOUT_SECONDS = 30


def _run_one_worker() -> None:
    """Код одного дочернего процесса — то же, что раньше было всей точкой входа."""
    if settings.demo_mode:
        log.info("Демо-режим: вход в аккаунты и отправка сообщений имитируются.")
    asyncio.run(run())


def main() -> None:
    count = topology.worker_count()
    log.info(
        "Поднимаю %s процессов шлюза (ядер видно контейнеру: %s)",
        count,
        topology.detected_cores(),
    )

    # Обработчики сигналов ставим ДО того, как поднимаем хоть один процесс:
    # поднять N процессов не мгновенно, и если SIGTERM придёт в этом окне
    # (быстрый перезапуск контейнера сразу после старта), а обработчика ещё
    # нет — сработает поведение по умолчанию (немедленное убийство
    # супервизора), уже запущенные процессы останутся без присмотра и без
    # released аренды до истечения по таймауту. Ставим раньше — и такой
    # SIGTERM просто взведёт stop_event, который цикл ниже увидит сразу
    # после того, как доспавнит оставшихся.
    stop_event = threading.Event()

    def _handle_stop(signum: int, frame: FrameType | None) -> None:  # noqa: ARG001
        stop_event.set()

    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)

    # "spawn", не системный дефолт ("fork" на Linux): движок БД создаётся при
    # импорте модуля (app/core/db.py), до разделения на процессы. fork скопировал
    # бы уже существующий объект движка в каждый дочерний процесс — пока пул
    # соединений ещё пуст (SQLAlchemy их не открывает заранее), это скорее всего
    # ничем не грозило бы, но полагаться на "скорее всего" в вопросе соединений
    # с базой — не тот случай. spawn поднимает каждый процесс с нуля, отдельным
    # интерпретатором: общих файловых дескрипторов и унаследованного состояния
    # нет в принципе, а не только «пока не доказано обратное».
    ctx = multiprocessing.get_context("spawn")
    workers: list[multiprocessing.process.BaseProcess] = []
    for _ in range(count):
        if stop_event.is_set():
            break
        proc = ctx.Process(target=_run_one_worker)
        proc.start()
        workers.append(proc)

    while not stop_event.is_set():
        stop_event.wait(timeout=CHECK_INTERVAL_SECONDS)
        for i, proc in enumerate(workers):
            if stop_event.is_set():
                break
            if not proc.is_alive():
                log.warning(
                    "Процесс шлюза %s завершился (код %s) — поднимаю новый вместо него",
                    proc.pid,
                    proc.exitcode,
                )
                replacement = ctx.Process(target=_run_one_worker)
                replacement.start()
                workers[i] = replacement

    log.info("Получен сигнал остановки — закрываю %s процессов шлюза", len(workers))
    for proc in workers:
        proc.terminate()
    for proc in workers:
        proc.join(timeout=SHUTDOWN_TIMEOUT_SECONDS)
        if proc.is_alive():
            log.warning(
                "Процесс шлюза %s не остановился за %s с — добиваю",
                proc.pid,
                SHUTDOWN_TIMEOUT_SECONDS,
            )
            proc.kill()


if __name__ == "__main__":
    main()
