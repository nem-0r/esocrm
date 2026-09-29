"""Справочник услуг и его связь со сделками и статистикой.

- менеджер видит только продающиеся услуги и ничего не меняет;
- руководитель заводит, правит, снимает с продажи, удаляет; дубль названия
  (без учёта регистра) не проходит;
- «подсказки из истории» — названия из сделок, которых нет в справочнике;
- позиция сделки запоминает услугу и цену по прайсу на момент продажи;
  правка справочника её не меняет; снятую с продажи в новую сделку не выбрать;
- статистика по услугам склеивает позиции одной услуги, как бы их ни назвали.

Запуск: docker compose exec -T api python -m tests.verify_services
"""

import asyncio
import sys
import uuid

from sqlalchemy import text

from app.core.db import SessionLocal
from tests.support import ADMIN, BASE, MANAGER, Checks, login


async def run() -> int:  # noqa: PLR0915
    c = Checks("Справочник услуг")
    admin = await login(ADMIN)
    manager = await login(MANAGER)
    suffix = uuid.uuid4().hex[:6]
    created_ids: list[int] = []
    try:
        c.section("Чтение")
        services = (await manager.get(f"{BASE}/services")).json()
        c.check("менеджер видит справочник", isinstance(services, list) and len(services) >= 1, services)
        c.check("у услуг есть цены", all("price" in s for s in services))
        inactive_view = (await manager.get(f"{BASE}/services", params={"include_inactive": True})).json()
        c.check("снятые с продажи менеджеру не показываются", all(s["is_active"] for s in inactive_view))
        # Аудит №9: в окне оплаты показывались только первые 8 услуг — но это
        # была подрезка на фронтенде (ItemsEditor.MAX_SHOWN), сервер всегда
        # отдавал весь список. Сверяем длину с независимым счётом по базе —
        # регрессия, если сервер когда-нибудь незаметно обзаведётся лимитом.
        async with SessionLocal() as db:
            active_count = (
                await db.execute(
                    text("select count(*) from services where deleted_at is null and is_active")
                )
            ).scalar_one()
        c.check("справочник отдаётся целиком, без обрезки", int(active_count), len(services))
        sort_orders = [s["sort_order"] for s in services]
        c.check("порядок — по sort_order справочника", sorted(sort_orders), sort_orders)

        c.section("Права")
        r = await manager.post(f"{BASE}/services", json={"name": f"Попытка {suffix}", "price": 100000})
        c.check("менеджер не создаёт услуги", r.status_code == 403, r.status_code)
        r = await manager.get(f"{BASE}/services/suggestions")
        c.check("подсказки — только руководителю", r.status_code == 403, r.status_code)

        c.section("Руководитель ведёт справочник")
        name = f"Тестовая услуга {suffix}"
        r = await admin.post(f"{BASE}/services", json={"name": name, "price": 350000, "description": "проверка"})
        service = r.json() if r.status_code == 201 else {}
        c.check("услуга создана", r.status_code == 201, r.text[:200])
        if service.get("id"):
            created_ids.append(service["id"])
        r = await admin.post(f"{BASE}/services", json={"name": name.upper(), "price": 1})
        c.check("дубль названия (другим регистром) — 409", r.status_code == 409, r.status_code)
        r = await admin.post(f"{BASE}/services", json={"name": f"Бесплатная {suffix}", "price": 0})
        c.check("нулевая цена — 422", r.status_code == 422, r.status_code)
        r = await admin.post(f"{BASE}/services", json={"name": f"По договорённости {suffix}", "price": None})
        c.check("услуга без цены — можно", r.status_code == 201, r.text[:120])
        if r.status_code == 201:
            created_ids.append(r.json()["id"])

        c.section("Подсказки из истории")
        hints = (await admin.get(f"{BASE}/services/suggestions")).json()
        names = {h["name"] for h in hints}
        c.check("вписанная руками «Матрица судьбы» подсказана", "Матрица судьбы" in names, names)
        matrix = next((h for h in hints if h["name"] == "Матрица судьбы"), None)
        c.check("с числом продаж и типичной ценой", bool(matrix) and matrix["uses"] > 0 and matrix["typical_price"] == 650000, matrix)
        catalog_names = {s["name"] for s in services}
        c.check("то, что уже в справочнике, не подсказывается", not (names & catalog_names), names & catalog_names)

        c.section("Сделка запоминает услугу и цену по прайсу")
        async with SessionLocal() as db:
            conv_id, requisite_id = (
                await db.execute(
                    text(
                        """
                        select c.id, (select id from payment_requisites where is_active and deleted_at is null order by id limit 1)
                        from conversations c join telegram_accounts a on a.id = c.account_id
                        where a.is_active and not c.is_blocked_by_client order by c.id limit 1
                        """
                    )
                )
            ).one()
        r = await admin.post(
            f"{BASE}/deals",
            json={
                "conversation_id": conv_id,
                "payment_method": "requisites",
                "requisite_id": requisite_id,
                "items": [
                    {"name": "Своё название для той же услуги", "amount": 300000, "service_id": service.get("id")},
                    {"name": "Без справочника", "amount": 100000},
                ],
            },
        )
        deal = r.json() if r.status_code in (200, 201) else {}
        c.check("сделка создана", r.status_code in (200, 201), r.text[:200])
        items = deal.get("items", [])
        c.check(
            "позиция связана с услугой, цена по прайсу — снимком",
            len(items) == 2 and items[0]["service_id"] == service.get("id") and items[0]["list_price"] == 350000,
            items,
        )
        c.check("позиция без справочника — без связи", len(items) == 2 and items[1]["service_id"] is None)

        r = await admin.patch(f"{BASE}/services/{service.get('id')}", json={"price": 999900})
        c.check("цену в справочнике можно поменять", r.status_code == 200, r.status_code)
        again = (await admin.get(f"{BASE}/deals/{deal.get('id')}")).json()
        c.check("старая сделка сохранила прежнюю цену по прайсу", again.get("items", [{}])[0].get("list_price") == 350000)

        r = await admin.patch(f"{BASE}/services/{service.get('id')}", json={"is_active": False})
        c.check("услугу можно снять с продажи", r.status_code == 200 and r.json()["is_active"] is False)
        active = (await manager.get(f"{BASE}/services")).json()
        c.check("снятая пропала из списка менеджера", all(s["id"] != service.get("id") for s in active))
        r = await admin.post(
            f"{BASE}/deals",
            json={
                "conversation_id": conv_id,
                "payment_method": "requisites",
                "requisite_id": requisite_id,
                "items": [{"name": name, "amount": 300000, "service_id": service.get("id")}],
            },
        )
        c.check("снятую с продажи в новую сделку не выбрать", r.status_code == 422 and "снята с продажи" in r.text, r.text[:120])
        r = await admin.post(
            f"{BASE}/deals",
            json={
                "conversation_id": conv_id,
                "payment_method": "requisites",
                "requisite_id": requisite_id,
                "items": [{"name": "x", "amount": 300000, "service_id": 987654321}],
            },
        )
        c.check("несуществующая услуга — 422", r.status_code == 422, r.status_code)

        c.section("Правку сделки со снятой услугой можно сохранить (аудит №8)")
        # Позиция «deal» уже ссылается на «service», которую только что сняли
        # с продажи (строка выше). Раньше правка такой сделки была тупиком:
        # сервер отвечал тем же «снята с продажи», что и при выборе её в
        # новой сделке, — хотя позиция и так уже в сделке, менять её не просят.
        r = await admin.patch(
            f"{BASE}/deals/{deal.get('id')}",
            json={
                "items": [
                    {"name": items[0]["name"], "amount": 320000, "service_id": service.get("id")},
                    {"name": items[1]["name"], "amount": items[1]["amount"]},
                ],
                "comment": "проверка: услугу сняли с продажи после выбора",
            },
        )
        c.check(
            "правка со снятой (уже выбранной) услугой — не 422", r.status_code == 200, r.text[:200]
        )
        edited = r.json() if r.status_code == 200 else {}
        edited_items = edited.get("items", [])
        c.check(
            "услуга и позиция остались связаны",
            len(edited_items) == 2 and edited_items[0].get("service_id") == service.get("id"),
            edited_items,
        )
        c.check(
            "цена по прайсу — прежний снимок, а не текущая (уже другая) цена услуги",
            bool(edited_items) and edited_items[0].get("list_price") == 350000,
            edited_items[:1],
        )
        c.check(
            "сумма позиции менеджер всё равно может поправить",
            bool(edited_items) and edited_items[0].get("amount") == 320000,
            edited_items[:1],
        )
        # А вот НОВУЮ позицию с чужой (никогда не стоявшей в этой сделке)
        # услугой правка по-прежнему не пропустит — исключение только для того,
        # что уже было в сделке.
        r = await admin.patch(
            f"{BASE}/deals/{deal.get('id')}",
            json={
                "items": [{"name": "x", "amount": 100000, "service_id": 987654321}],
                "comment": "проверка: новая позиция с чужой услугой",
            },
        )
        c.check(
            "правка с НОВОЙ несуществующей услугой всё ещё отклоняется",
            r.status_code == 422,
            r.status_code,
        )

        # Черновик больше не нужен — отменяем, чтобы не висел «ждёт оплаты».
        if deal.get("id"):
            await admin.post(f"{BASE}/deals/{deal['id']}/cancel", json={"reason": "проверка справочника"})

        c.section("Удаление")
        for service_id in created_ids:
            r = await admin.delete(f"{BASE}/services/{service_id}")
            c.check(f"услуга {service_id} удалена", r.status_code == 200, r.status_code)
        r = await admin.post(f"{BASE}/services", json={"name": name, "price": 100000})
        c.check("имя удалённой услуги снова свободно", r.status_code == 201, r.status_code)
        if r.status_code == 201:
            await admin.delete(f"{BASE}/services/{r.json()['id']}")
    finally:
        await admin.aclose()
        await manager.aclose()
    return c.finish()


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
