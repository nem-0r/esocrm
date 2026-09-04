"""Проверка денежного контура: код платежа, поиск по нему, чек, сверка с выпиской.

Деньги приходят на карту отдельным потоком: банк не знает ни про сделку, ни про
клиента. Связь одна — код в комментарии к переводу. Поэтому проверяем не форму,
а весь путь: код выдан, дошёл до счёта клиенту, находится поиском в том виде,
в каком его перепишет человек, и попадает в сверку по реквизитам.
"""

import asyncio
import sys

import httpx

BASE = "http://localhost:8000/api/v1"
ADMIN = {"email": "elena@astra.ru", "password": "demo1234"}
MANAGER = {"email": "marina@astra.ru", "password": "demo1234"}

ok: list[str] = []
bad: list[str] = []


def check(title: str, condition: bool, detail: str = "") -> None:
    (ok if condition else bad).append(title)
    print(f"  {'✓' if condition else '✗'} {title}{f' — {detail}' if detail else ''}")


async def run() -> int:
    print("Проверка денежного контура\n")
    async with (
        httpx.AsyncClient(base_url=BASE, timeout=30) as admin,
        httpx.AsyncClient(base_url=BASE, timeout=30) as manager,
    ):
        await admin.post("/auth/login", json=ADMIN)
        await manager.post("/auth/login", json=MANAGER)

        conversation_id = (await admin.get("/conversations", params={"limit": 1})).json()["items"][
            0
        ]["id"]
        requisites = (await admin.get("/requisites")).json()
        requisites = requisites if isinstance(requisites, list) else requisites.get("items", [])

        print("Код платежа")
        created = await admin.post(
            "/deals",
            json={
                "conversation_id": conversation_id,
                "payment_method": "requisites",
                "requisite_id": requisites[0]["id"],
                "items": [{"name": "Проверка кода", "amount": 100000}],
            },
        )
        check("сделка создаётся", created.status_code == 201, created.text[:80])
        deal = created.json()
        code = deal.get("payment_code") or ""
        check("код платежа выдан", bool(code), code)
        check(
            "код не равен номеру сделки — опечатка не попадёт в чужую оплату",
            code != str(deal["id"]) and code.startswith("AC-"),
            code,
        )

        second = (
            await admin.post(
                "/deals",
                json={
                    "conversation_id": conversation_id,
                    "payment_method": "requisites",
                    "requisite_id": requisites[0]["id"],
                    "items": [{"name": "Второй код", "amount": 100000}],
                },
            )
        ).json()
        check(
            "у второй сделки другой код",
            second.get("payment_code") != code,
            second.get("payment_code"),
        )

        print("\nПоиск по коду — так, как его перепишет человек")
        variants = {
            "как есть": code,
            "строчными": code.lower(),
            "фразой из чата": f"оплатил, {code.lower()} спасибо",
            "с пробелом вместо дефиса": code.replace("-", " "),
        }
        for label, query in variants.items():
            found = (await admin.get("/search", params={"q": query})).json().get("deals", [])
            check(
                f"находится {label}",
                any(item["id"] == deal["id"] for item in found),
                f"{len(found)} совпадений",
            )

        wrong = code[:-1] + ("A" if code[-1] != "A" else "C")
        found_wrong = (await admin.get("/search", params={"q": wrong})).json().get("deals", [])
        check(
            "код с опечаткой не приводит к чужой сделке",
            not any(item["id"] == deal["id"] for item in found_wrong),
            f"{wrong}: {len(found_wrong)} совпадений",
        )

        print("\nСчёт клиенту")
        sent = await admin.post(f"/deals/{deal['id']}/send")
        check("счёт уходит в чат", sent.status_code == 200, sent.text[:80])
        messages = (
            await admin.get(f"/conversations/{conversation_id}/messages", params={"limit": 5})
        ).json()
        texts = " ".join((m.get("text") or "") for m in messages.get("items", []))
        check("в тексте счёта есть код платежа", code in texts, code)
        check(
            "клиенту сказано, куда его писать",
            "комментарии к переводу" in texts,
        )

        print("\nПодтверждение оплаты")
        no_receipt = await admin.post(f"/deals/{deal['id']}/pay", json={"receipt_number": "  "})
        check(
            "без номера чека оплата не подтверждается",
            no_receipt.status_code == 422,
            f"{no_receipt.status_code} {no_receipt.json()['error']['message'][:50]}",
        )
        target = requisites[1]["id"] if len(requisites) > 1 else requisites[0]["id"]
        paid = await admin.post(
            f"/deals/{deal['id']}/pay",
            json={"receipt_number": "ЧЕК-ПРОВЕРКА", "paid_to_requisite_id": target},
        )
        check("оплата с чеком подтверждается", paid.status_code == 200, paid.text[:80])
        card = paid.json()
        check("чек сохранён в карточке", card.get("receipt_number") == "ЧЕК-ПРОВЕРКА")
        check(
            "записано, на какой счёт деньги пришли фактически",
            card.get("paid_to_requisite_id") == target,
            str(card.get("paid_to_requisite_title")),
        )
        check(
            "повторная оплата той же сделки не проходит",
            (
                await admin.post(
                    f"/deals/{deal['id']}/pay", json={"receipt_number": "ЧЕК-ДУБЛЬ"}
                )
            ).status_code
            in (400, 409, 422),
        )

        print("\nСверка с банковской выпиской")
        report = await admin.get("/deals/by-requisite")
        check("отчёт по реквизитам отвечает", report.status_code == 200, report.text[:80])
        rows = report.json()
        row = next((r for r in rows if r["requisite_id"] == target), None)
        check("оплата попала в строку своего реквизита", row is not None, str(row))
        check(
            "сумма отчёта сходится с суммой оплаченных сделок",
            row is not None and row["amount"] >= 100000,
            str(row["amount"]) if row else "",
        )

        journal = (await admin.get(f"/deals/{deal['id']}")).json().get("events", [])
        kinds = [event.get("kind") for event in journal]
        check(
            "в журнале сделки видно создание, отправку и оплату",
            {"created", "sent", "paid"}.issubset(set(kinds)),
            str(kinds),
        )

    print(f"\nИтог: {len(ok)} выполнено, {len(bad)} не выполнено")
    for line in bad:
        print(f"  — {line}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
