"""Устойчивость: сервер не отвечает 5xx на то, что прислал пользователь.

Регрессия найденных при тестировании релиза 27.09 дефектов:

- одновременные записи в один чат (двойной клик, две вкладки, ответ вместе
  с пересылкой) рвались взаимной блокировкой базы — менеджер видел 500;
- числа за пределами int64 (id, service_id, user_id) и нулевой байт в тексте
  давали 500 вместо понятного отказа;
- период с годом 0001 или 9999 в статистике ронял расчёт даты.

Запуск: docker compose exec -T api python -m tests.verify_robustness
"""

import asyncio
import sys

from tests.support import ADMIN, BASE, MANAGER, Checks, login, until


async def run() -> int:
    c = Checks("Устойчивость к параллельным и заведомо неверным запросам")
    admin = await login(ADMIN)
    manager = await login(MANAGER)
    try:
        page = (await admin.get(f"{BASE}/conversations", params={"limit": 5})).json()["items"]
        source, target = page[0]["id"], page[1]["id"]

        c.section("Одновременные записи в один чат")
        rs = await asyncio.gather(
            *[admin.post(f"{BASE}/conversations/{source}/messages", json={"text": f"гонка {i}"}) for i in range(10)]
        )
        codes = sorted(r.status_code for r in rs)
        c.check("10 одновременных отправок в один чат — все приняты", codes == [201] * 10, codes)
        # После всплеска параллельных запросов в пуле десять соединений. Пока идёт
        # ожидание отправки, сервер закрывает простаивающие (keep-alive 5 с), и
        # httpx на следующем запросе получает ReadError — ошибка самого теста,
        # не сервера. Берём свежее соединение.
        await admin.aclose()
        admin = await login(ADMIN)
        first = (await admin.post(f"{BASE}/conversations/{source}/messages", json={"text": "источник"})).json()

        async def _sent():  # noqa: ANN202
            row = (await admin.get(f"{BASE}/conversations/{source}/messages", params={"limit": 3})).json()["items"]
            return row and all(m["status"] in ("sent", "read") for m in row if m["id"] == first["id"])

        await until(_sent, 15)
        await admin.aclose()
        admin = await login(ADMIN)
        forward = {"source_conversation_id": source, "message_ids": [first["id"]]}
        rs = await asyncio.gather(
            *[admin.post(f"{BASE}/conversations/{target}/forward", json=forward) for _ in range(8)]
        )
        codes = sorted(r.status_code for r in rs)
        c.check("8 одновременных пересылок в один чат — все приняты", codes == [201] * 8, codes)
        rs = await asyncio.gather(
            *[admin.post(f"{BASE}/conversations/{target}/forward", json=forward) for _ in range(5)],
            *[admin.post(f"{BASE}/conversations/{target}/messages", json={"text": "вперемешку"}) for _ in range(5)],
        )
        codes = sorted(r.status_code for r in rs)
        c.check("пересылки вперемешку с обычными сообщениями — все приняты", codes == [201] * 10, codes)

        c.section("Числа за пределами int64 — отказ, а не 500")
        huge = 10**20
        cases = {
            "чат": await admin.post(f"{BASE}/conversations/{huge}/messages", json={"text": "x"}),
            "файл": await admin.get(f"{BASE}/files/{huge}"),
            "услуга (правка)": await admin.patch(f"{BASE}/services/{huge}", json={"name": "x"}),
            "пересылка: source": await admin.post(
                f"{BASE}/conversations/{target}/forward", json={"source_conversation_id": huge, "message_ids": [1]}
            ),
            "пересылка: message_ids": await admin.post(
                f"{BASE}/conversations/{target}/forward", json={"source_conversation_id": source, "message_ids": [huge]}
            ),
            "статистика: user_id": await admin.get(f"{BASE}/stats/overview", params={"user_id": huge}),
            "сделка: service_id": await admin.post(
                f"{BASE}/deals",
                json={
                    "conversation_id": source,
                    "payment_method": "requisites",
                    "custom_requisites_text": "x",
                    "items": [{"name": "x", "amount": 100, "service_id": huge}],
                },
            ),
        }
        for label, r in cases.items():
            c.check(f"{label}: 4xx", 400 <= r.status_code < 500, f"{r.status_code} {r.text[:100]}")

        c.section("Недопустимые символы в тексте")
        r = await admin.post(f"{BASE}/conversations/{source}/messages", json={"text": "a\x00b"})
        c.check("нулевой байт в сообщении — 422", r.status_code == 422, f"{r.status_code} {r.text[:100]}")
        r = await admin.post(f"{BASE}/services", json={"name": "a\x00b"})
        c.check("нулевой байт в названии услуги — 422", r.status_code == 422, f"{r.status_code} {r.text[:100]}")
        r = await manager.post(f"{BASE}/conversations/{source}/messages", json={"text": "текст с эмодзи 🌙✨ и <b>тегами</b>"})
        c.check("эмодзи и теги принимаются как обычный текст", r.status_code in (201, 404), r.status_code)

        c.section("Статистика: крайние даты")
        for label, params in {
            "с 0001 года": {"date_from": "0001-01-01", "date_to": "2026-09-30"},
            "до 9999 года": {"date_from": "2026-01-01", "date_to": "9999-12-31"},
            "0001–9999": {"date_from": "0001-01-01", "date_to": "9999-12-31"},
        }.items():
            for endpoint in ("overview", "series", "services", "accounts", "managers"):
                r = await admin.get(f"{BASE}/stats/{endpoint}", params=params)
                c.check(f"{endpoint}, {label} — 422 с понятным текстом", r.status_code == 422 and "1900" in r.text, f"{r.status_code} {r.text[:80]}")
        r = await admin.get(f"{BASE}/stats/overview", params={"date_from": "1900-01-01", "date_to": "1900-12-31"})
        c.check("1900 год (до появления данных) — пустая сводка, 200", r.status_code == 200, r.status_code)
    finally:
        await admin.aclose()
        await manager.aclose()
    return c.finish()


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
