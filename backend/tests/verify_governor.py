"""Регулятор нагрузки на живом стенде: очередь подтяжек, докачка при тесном диске,
состояние сервера для руководителя, защита загрузки при полном диске, настройка.

Запуск: docker compose exec -T api python -m tests.verify_governor

Диск «заполняется» не по-настоящему: сигнал подменяется файлом
`/tmp/astra-probe-override.json` (работает только вне продакшена, governor._override).
"""

import asyncio
import json
import os
import sys
import time

from sqlalchemy import text

from app.core.db import SessionLocal
from app.gateway import backfill, governor
from tests.support import ADMIN, BASE, MANAGER, Checks, login

OVERRIDE = governor.DEFAULT_OVERRIDE_FILE
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000100000001080600000"
    "01f15c4890000000a4944415478da6360000002000155ff2ba00000000049454e44ae426082"
)
TEST_KEY = 0x7E57  # свой ключ: стенд сам тоже подтягивает историю, и мы не должны ему мешать


def set_override(**values: float) -> None:
    with open(OVERRIDE, "w") as handle:
        json.dump(values, handle)
    governor.reset_cache()


def clear_override() -> None:
    if os.path.exists(OVERRIDE):
        os.remove(OVERRIDE)
    governor.reset_cache()


async def run() -> int:  # noqa: PLR0915
    c = Checks("Регулятор нагрузки шлюза")
    admin = await login(ADMIN)
    manager = await login(MANAGER)
    clear_override()
    try:
        # ------------------------------------------------ очередь подтяжек
        c.section("Очередь подтяжек: не больше N одновременно на весь сервер")
        events: list[tuple[float, str, int]] = []

        async def worker(number: int, hold: float) -> None:
            async with governor.SyncSlot(number, max_slots=2, poll_seconds=0.2, key=TEST_KEY):
                events.append((time.monotonic(), "in", number))
                await asyncio.sleep(hold)
                events.append((time.monotonic(), "out", number))

        await asyncio.gather(worker(1, 1.2), worker(2, 1.2), worker(3, 0.2), worker(4, 0.2))
        events.sort()
        running = peak = 0
        for _, kind, _n in events:
            running += 1 if kind == "in" else -1
            peak = max(peak, running)
        c.check("одновременно шло не больше двух подтяжек", peak == 2, f"пик {peak}")
        c.check("все четыре в итоге отработали", sum(1 for e in events if e[1] == "in") == 4)
        starts = sorted(t for t, kind, _n in events if kind == "in")
        first_out = min(t for t, kind, _n in events if kind == "out")
        c.check(
            "третья и четвёртая по очереди дождались освободившегося места",
            all(t >= first_out - 0.05 for t in starts[2:]),
            (starts, first_out),
        )

        t0 = time.monotonic()
        async with governor.SyncSlot(5, max_slots=1, key=TEST_KEY):
            pass
        c.check("после выхода место свободно сразу", time.monotonic() - t0 < 1.0)
        try:
            async with governor.SyncSlot(6, max_slots=1, key=TEST_KEY):
                raise RuntimeError("подтяжка упала")
        except RuntimeError:
            pass
        t0 = time.monotonic()
        async with governor.SyncSlot(7, max_slots=1, key=TEST_KEY):
            pass
        c.check("упавшая подтяжка не оставляет место занятым", time.monotonic() - t0 < 1.0)

        # ------------------------------------------- докачка и тесный диск
        c.section("Докачка файлов: тесный диск пропускает только небольшие")
        async with SessionLocal() as db:
            row = (
                await db.execute(
                    text(
                        "select m.id, m.conversation_id, c.client_id, c.account_id "
                        "from messages m join conversations c on c.id = m.conversation_id "
                        "order by m.id limit 1"
                    )
                )
            ).one()
            message_id, conversation_id, client_id, account_id = row
            ids = []
            for name, size in (("small.jpg", 1 * 1024 * 1024), ("big.mp4", 60 * 1024 * 1024)):
                meta = json.dumps(
                    {"source": {"account_id": account_id, "chat_id": 1, "tg_message_id": 1}}
                )
                new = await db.execute(
                    text(
                        "insert into attachments (message_id, conversation_id, client_id, file_name, "
                        "size_bytes, status, kind, meta) values (:m, :c, :cl, :n, :s, 'pending', "
                        "'document', cast(:meta as jsonb)) returning id"
                    ),
                    {"m": message_id, "c": conversation_id, "cl": client_id, "n": name, "s": size, "meta": meta},
                )
                ids.append(new.scalar_one())
            await db.commit()
        try:
            both = await backfill._due([account_id], None)
            c.check("при свободном диске в очереди оба файла", set(ids) <= set(both), both)
            small = await backfill._due([account_id], governor.HEAVY_BYTES)
            c.check("при тесном диске — только небольшой", ids[0] in small and ids[1] not in small, small)
            c.check("при тесном диске крупный не потерян, а ждёт", ids[1] in set(both))
        finally:
            async with SessionLocal() as db:
                await db.execute(text("delete from attachments where id = any(:ids)"), {"ids": ids})
                await db.commit()

        # ------------- файл, отложенный из-за диска: виден, потом докачивается
        c.section("Файл, отложенный из-за диска: виден с причиной и докачивается сам")
        from datetime import UTC, datetime

        from app.models import TelegramAccount
        from app.services import inbound_service as svc

        peer_id = 9_900_000_777
        async with SessionLocal() as db:
            account_row = await db.get(TelegramAccount, account_id)
            event = svc.InboundMessage(
                peer=svc.PeerData(tg_user_id=peer_id, access_hash=1, username=None, first_name="Замер", last_name=None),
                tg_message_id=424242,
                date=datetime.now(UTC),
                text="фото",
                outgoing=False,
                random_id=None,
                live=False,
                media_kind="photo",
                attachments=[
                    {
                        "kind": "photo",
                        "file_name": "p.jpg",
                        "mime_type": "image/jpeg",
                        "status": "pending",
                        "size": 2048,
                        "source": {"account_id": account_id, "chat_id": peer_id, "tg_message_id": 424242},
                        "error": governor.REASON_DISK,
                        "paused": "disk",
                    }
                ],
                meta=None,
                is_edit=False,
                edit_date=None,
            )
            message = await svc.ingest(db, account_row, event)
            await db.commit()
            conv_id = message.conversation_id
        try:
            items = (await admin.get(f"{BASE}/conversations/{conv_id}/messages")).json()["items"]
            att = items[0]["attachments"][0]
            c.check(
                "менеджер видит файл со статусом «докачивается» и причиной «ждёт места»",
                att["status"] == "pending" and att["error"] == governor.REASON_DISK,
                att,
            )
            await backfill.fetch_one(att["id"])
            att = (await admin.get(f"{BASE}/conversations/{conv_id}/messages")).json()["items"][0]["attachments"][0]
            c.check("когда место есть — файл докачан сам", att["status"] == "ready", att)
            c.check("и пометка про диск исчезла", not att.get("error"), att)
        finally:
            async with SessionLocal() as db:
                await db.execute(text("delete from attachments where conversation_id = :c"), {"c": conv_id})
                await db.execute(text("delete from messages where conversation_id = :c"), {"c": conv_id})
                await db.execute(text("delete from conversations where id = :c"), {"c": conv_id})
                await db.execute(text("delete from telegram_peers where tg_user_id = :p"), {"p": peer_id})
                await db.execute(text("delete from clients where telegram_id = :p"), {"p": peer_id})
                await db.commit()

        # ----------------------------------------- состояние сервера (API)
        c.section("Состояние сервера для руководителя")
        r = await manager.get(f"{BASE}/system/status")
        c.check("менеджеру недоступно (403)", r.status_code == 403, r.status_code)
        r = await admin.get(f"{BASE}/system/status")
        c.check("руководителю отдаётся", r.status_code == 200, r.status_code)
        body = r.json()
        c.check(
            "в ответе диск, нагрузка, шлюз, план и оповещения",
            all(key in body for key in ("disk", "pressure", "gateway", "plan", "alerts", "cores")),
            list(body),
        )
        c.check("диск в порядке — оповещений о диске нет", not any("диск" in a["text"].lower() for a in body["alerts"]))

        for free, level, label in ((12, 1, "тесно"), (7, 2, "пауза"), (2, 3, "критично")):
            set_override(disk_free_gb=free, disk_total_gb=77)
            body = (await admin.get(f"{BASE}/system/status")).json()
            c.check(
                f"свободно {free} ГБ: уровень «{label}» и есть оповещение руководителю",
                body["disk"]["level"] == level and any("диск" in a["text"].lower() for a in body["alerts"]),
                body["disk"],
            )
        clear_override()

        # ------------------------------ полный диск: загрузка файлов закрыта
        c.section("Полный диск: менеджер не может загрузить файл, база защищена")
        set_override(disk_free_gb=2, disk_total_gb=77)
        r = await admin.post(f"{BASE}/files/upload", files={"file": ("a.png", PNG, "image/png")})
        c.check(
            "загрузка файла отклонена понятным текстом (422)",
            r.status_code == 422 and "место на диске" in r.text,
            f"{r.status_code} {r.text[:120]}",
        )
        r = await admin.post(f"{BASE}/files/voice", files={"file": ("v.webm", b"x" * 100, "audio/webm")})
        c.check("запись голоса тоже (422)", r.status_code == 422 and "место на диске" in r.text, r.status_code)
        set_override(disk_free_gb=12, disk_total_gb=77)
        r = await admin.post(f"{BASE}/files/upload", files={"file": ("a.png", PNG, "image/png")})
        c.check("при «тесно» загрузка ещё работает", r.status_code == 201, r.status_code)
        clear_override()
        r = await admin.post(f"{BASE}/files/upload", files={"file": ("a.png", PNG, "image/png")})
        c.check("место появилось — загрузка работает", r.status_code == 201, r.status_code)

        # ---------------------------------------------- настройка файлов
        c.section("Настройка «Файлы из истории»")
        before = (await admin.get(f"{BASE}/settings")).json().get("history_media_policy")
        c.check("по умолчанию «всё», как раньше", before == "all", before)
        for value in ("light", "minimal", "all"):
            r = await admin.patch(f"{BASE}/settings", json={"history_media_policy": value})
            now = (await admin.get(f"{BASE}/settings")).json().get("history_media_policy")
            c.check(f"значение «{value}» сохраняется", r.status_code == 200 and now == value, r.text[:100])
        r = await admin.patch(f"{BASE}/settings", json={"history_media_policy": "всё-всё"})
        c.check("неизвестное значение отклонено (422)", r.status_code == 422, r.status_code)
        r = await manager.patch(f"{BASE}/settings", json={"history_media_policy": "light"})
        c.check("менеджер настройку менять не может (403)", r.status_code == 403, r.status_code)
        governor.reset_policy_cache()
        c.check("регулятор читает сохранённое значение", await governor.history_policy() == "all")
    finally:
        clear_override()
        await admin.aclose()
        await manager.aclose()
    return c.finish()


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
