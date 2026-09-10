"""Проверка денежного контура: счёт клиенту, чек-файл, сверка с выпиской.

Деньги приходят на карту отдельным потоком: банк не знает ни про сделку, ни про
клиента. Раньше связь была кодом в комментарии к переводу — клиенты его
проставляли ненадёжно, поэтому сверка теперь идёт по чеку, который менеджер
прикладывает файлом при подтверждении оплаты. Проверяем весь путь: без чека
оплату не подтвердить, файл реально загружается через общий /files/upload
(тот же путь, что и вложения в чате), чек виден и скачивается в карточке
сделки, а сумма попадает в сверку по реквизитам.
"""

import asyncio
import sys

import httpx

ROOT = "http://localhost:8000"
BASE = f"{ROOT}/api/v1"
ADMIN = {"email": "elena@astra.ru", "password": "demo1234"}
MANAGER = {"email": "marina@astra.ru", "password": "demo1234"}

# Минимальный валидный PNG 1×1 — реальный файл, не заглушка с произвольными байтами.
RECEIPT_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000100000001080600000"
    "01f15c4890000000a4944415478da6360000002000155ff2ba00000000049454e44ae426082"
)


async def upload_receipt(client: httpx.AsyncClient) -> dict:
    files = {"file": ("chek.png", RECEIPT_PNG, "image/png")}
    uploaded = await client.post("/files/upload", files=files)
    return uploaded.json()


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

        print("Счёт клиенту")
        created = await admin.post(
            "/deals",
            json={
                "conversation_id": conversation_id,
                "payment_method": "requisites",
                "requisite_id": requisites[0]["id"],
                "items": [{"name": "Проверка чека", "amount": 100000}],
            },
        )
        check("сделка создаётся", created.status_code == 201, created.text[:80])
        deal = created.json()

        sent = await admin.post(f"/deals/{deal['id']}/send")
        check("счёт уходит в чат", sent.status_code == 200, sent.text[:80])
        messages = (
            await admin.get(f"/conversations/{conversation_id}/messages", params={"limit": 5})
        ).json()
        texts = " ".join((m.get("text") or "") for m in messages.get("items", []))
        check("в тексте счёта есть сумма", "Сумма к оплате" in texts)

        print("\nПодтверждение оплаты чеком")
        no_receipt = await admin.post(f"/deals/{deal['id']}/pay", json={})
        check(
            "без чека оплата не подтверждается",
            no_receipt.status_code == 422,
            f"{no_receipt.status_code} {no_receipt.json()['error']['message'][:50]}",
        )

        upload = await upload_receipt(admin)
        check("чек загружается через /files/upload", bool(upload.get("upload_key")), str(upload))

        paid = await admin.post(
            f"/deals/{deal['id']}/pay",
            json={
                "receipt_upload_key": upload["upload_key"],
                "receipt_file_name": upload["file_name"],
                "receipt_mime_type": upload["mime_type"],
                "receipt_size_bytes": upload["size_bytes"],
            },
        )
        check("оплата с чеком подтверждается", paid.status_code == 200, paid.text[:80])
        card = paid.json()
        check("чек сохранён в карточке", card.get("receipt_file_name") == "chek.png")
        check("ссылка на скачивание чека отдана", bool(card.get("receipt_url")))
        check(
            # Менеджер выбирает счёт один раз, при создании — повторно при
            # оплате его больше не спрашивают (поле убрали из формы).
            "записано, что деньги пришли на счёт из счёта клиенту",
            card.get("paid_to_requisite_id") == requisites[0]["id"],
            str(card.get("paid_to_requisite_title")),
        )

        # receipt_url — абсолютный путь от корня сервера (как и url файлов в поиске),
        # поэтому качаем его клиентом без префикса /api/v1, а не через admin.get().
        async with httpx.AsyncClient(base_url=ROOT, cookies=admin.cookies, timeout=30) as root:
            download = await root.get(card["receipt_url"])
        check(
            "чек реально скачивается тем же файлом",
            download.status_code == 200 and download.content == RECEIPT_PNG,
            f"HTTP {download.status_code}, {len(download.content)} байт",
        )

        second_upload = await upload_receipt(admin)
        check(
            "повторная оплата той же сделки не проходит",
            (
                await admin.post(
                    f"/deals/{deal['id']}/pay",
                    json={
                        "receipt_upload_key": second_upload["upload_key"],
                        "receipt_file_name": second_upload["file_name"],
                        "receipt_mime_type": second_upload["mime_type"],
                        "receipt_size_bytes": second_upload["size_bytes"],
                    },
                )
            ).status_code
            in (400, 409, 422),
        )

        print("\nСверка с банковской выпиской")
        report = await admin.get("/deals/by-requisite")
        check("отчёт по реквизитам отвечает", report.status_code == 200, report.text[:80])
        rows = report.json()
        row = next((r for r in rows if r["requisite_id"] == requisites[0]["id"]), None)
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
