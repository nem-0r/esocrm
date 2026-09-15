"""Проверка приёма оплат через Робокассу: ссылка, подпись, сумма, повтор.

Живого магазина Робокассы нет — тестовые пароли из .env настоящие только для
нашего кода, у Робокассы такого магазина не существует. Поэтому здесь не
дергаем auth.robokassa.ru, а играем за Робокассу сами: подписываем уведомления
Паролем #2 точно по формуле из документации (OutSum:InvId:Пароль#2[:Shp_...] →
алгоритм магазина, сейчас SHA256) и шлём их на свой же ResultURL — так же, как
это в проде сделает бот-посредник (магазин esoterra-pay общий с ботом Богдана,
см. docs/11-payments-architecture.md, разд. 8: InvId у нас со сдвигом и не равен
id сделки, настоящий адрес сделки — Shp_deal_id).
Подпись считаем заново, отдельно от app.services.robokassa: если бы в модуле
была ошибка в формуле, использование того же кода для проверки её бы не поймало.

Скрипт меняет демо-данные (создаёт сделки). После него положено выполнить `make seed`.

Запуск: docker compose exec -T api python -m tests.verify_robokassa
"""

import asyncio
import hashlib
import io
import sys
from urllib.parse import parse_qs, urlparse

import httpx
from sqlalchemy import text

from app.core.config import settings
from app.core.db import SessionLocal
from app.scheduler.jobs import expire_overdue_deals

BASE = "http://localhost:8000/api/v1"
ADMIN = {"email": "elena@astra.ru", "password": "demo1234"}

# Минимальный валидный PNG 1×1 — реальный файл, не заглушка с произвольными байтами.
RECEIPT_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000100000001080600000"
    "01f15c4890000000a4944415478da6360000002000155ff2ba00000000049454e44ae426082"
)

ok: list[str] = []
bad: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> bool:
    if condition:
        ok.append(name)
        print(f"  ✓ {name}")
    else:
        bad.append(f"{name} — {detail}")
        print(f"  ✗ {name} — {detail}")
    return condition


def result_signature(
    out_sum: str, inv_id: int, password2: str, *, shp_params: dict[str, str] | None = None
) -> str:
    """Формула ResultURL из документации Робокассы, посчитана независимо
    от app.services.robokassa.sign_result — намеренное дублирование."""
    raw = f"{out_sum}:{inv_id}:{password2}"
    if shp_params:
        ordered = sorted(shp_params.items(), key=lambda kv: kv[0].lower())
        raw += "".join(f":{k}={v}" for k, v in ordered)
    return hashlib.new(settings.robokassa_hash_alg, raw.encode("utf-8")).hexdigest()


def inv_id_and_shp_from_url(url: str) -> tuple[int, dict[str, str]]:
    """Достаём InvId и Shp_* из уже сгенерированной ссылки — так тест сверяется
    с тем, что реально отправил наш код, а не с формулой сдвига отдельно."""
    params = parse_qs(urlparse(url).query)
    inv_id = int(params["InvId"][0])
    shp = {k: v[0] for k, v in params.items() if k.lower().startswith("shp_")}
    return inv_id, shp


async def result_notify(
    client: httpx.AsyncClient, out_sum: str, inv_id: int, signature: str, *, shp_params: dict[str, str] | None = None
):
    data = {"OutSum": out_sum, "InvId": str(inv_id), "SignatureValue": signature}
    if shp_params:
        data.update(shp_params)
    return await client.post(f"{BASE}/payments/robokassa/result", data=data)


async def run() -> int:
    print("Проверка приёма оплат через Робокассу\n")

    if not settings.robokassa_enabled:
        check("Робокасса настроена (ROBOKASSA_* в .env)", False, "robokassa_enabled=False")
        print(f"\nИтог: {len(ok)} выполнено, {len(bad)} не выполнено")
        return 1

    # Тот же пароль, что реально проверяет сервер сейчас: тестовый, если задан
    # отдельно и включён тестовый режим, иначе боевой (см. app/core/config.py).
    password2 = settings.robokassa_active_password2

    async with httpx.AsyncClient(timeout=30) as c:
        await c.post(f"{BASE}/auth/login", json=ADMIN)

        me = (await c.get(f"{BASE}/auth/me")).json()
        check("/auth/me сообщает, что Робокасса включена", me.get("robokassa_enabled") is True)

        conversation_id = (await c.get(f"{BASE}/conversations", params={"limit": 1})).json()[
            "items"
        ][0]["id"]

        async def new_link_deal(amount: int = 150000) -> dict:
            created = await c.post(
                f"{BASE}/deals",
                json={
                    "conversation_id": conversation_id,
                    "payment_method": "link",
                    "items": [{"name": "Проверка Робокассы", "amount": amount}],
                },
            )
            check("сделка по ссылке создаётся", created.status_code == 201, created.text[:120])
            sent = await c.post(f"{BASE}/deals/{created.json()['id']}/send")
            check("счёт со ссылкой отправляется", sent.status_code == 200, sent.text[:120])
            return sent.json()

        print("Ссылка на оплату")
        deal = await new_link_deal()
        url = deal.get("payment_url") or ""
        check(
            "ссылка на оплату сгенерирована", url.startswith("https://auth.robokassa.ru/"), url[:60]
        )
        inv_id, shp = inv_id_and_shp_from_url(url)
        check("InvId в ссылке смещён — не равен id сделки напрямую", inv_id != deal["id"], str(inv_id))
        check(
            f"Shp_deal_id в ссылке — id сделки ({deal['id']})",
            shp.get("Shp_deal_id") == str(deal["id"]),
            str(shp),
        )
        check("Shp_source в ссылке — esocrm", shp.get("Shp_source") == "esocrm", str(shp))
        check("OutSum в ссылке — 1500.00", "OutSum=1500.00" in url)
        check("в ссылке передан срок действия (ExpirationDate)", "ExpirationDate=" in url, url[:200])
        messages = (
            await c.get(f"{BASE}/conversations/{conversation_id}/messages", params={"limit": 5})
        ).json()
        invoice_text = next(
            (
                m.get("text") or ""
                for m in messages.get("items", [])
                if url in (m.get("text") or "")
            ),
            "",
        )
        check("ссылка ушла клиенту в чат", bool(invoice_text), invoice_text[:120])
        check(
            "код платежа не упоминается клиенту при оплате по ссылке — он ничего не значит",
            "код платежа" not in invoice_text.lower(),
            invoice_text[:120],
        )

        print("\nПодделка подписи")
        forged = await result_notify(c, "1500.00", inv_id, "0" * 32, shp_params=shp)
        check("неверная подпись отклоняется", forged.status_code == 403, forged.text[:80])
        card = (await c.get(f"{BASE}/deals/{deal['id']}")).json()
        check("сделка не тронута подделкой", card["status"] == "awaiting", card["status"])

        print("\nПодмена суммы")
        bad_sum = "1.00"
        sig_bad_sum = result_signature(bad_sum, inv_id, password2, shp_params=shp)
        tampered = await result_notify(c, bad_sum, inv_id, sig_bad_sum, shp_params=shp)
        check(
            "несовпадение суммы отклоняется, даже с верной подписью на неё",
            tampered.status_code == 409,
            tampered.text[:80],
        )
        card = (await c.get(f"{BASE}/deals/{deal['id']}")).json()
        check("сделка не тронута подменой суммы", card["status"] == "awaiting", card["status"])

        print("\nВерное уведомление (пересланное ботом-посредником, с Shp_)")
        sig_ok = result_signature("1500.00", inv_id, password2, shp_params=shp)
        paid = await result_notify(c, "1500.00", inv_id, sig_ok, shp_params=shp)
        check(
            "верная подпись и сумма подтверждают оплату",
            paid.status_code == 200 and paid.text == f"OK{inv_id}",
            f"{paid.status_code} {paid.text[:40]}",
        )
        card = (await c.get(f"{BASE}/deals/{deal['id']}")).json()
        check("сделка помечена оплаченной", card["status"] == "paid", card["status"])
        check("источник оплаты — провайдер, не менеджер", card.get("paid_source") == "provider")
        evt = next((e for e in card["events"] if e["kind"] == "paid"), None)
        check(
            "в журнале оплата записана без автора-человека",
            evt is not None and evt["actor"] is None,
            str(evt),
        )

        print("\nПовторная доставка того же уведомления")
        repeat = await result_notify(c, "1500.00", inv_id, sig_ok, shp_params=shp)
        check(
            "повтор отвечает так же, как первая обработка",
            repeat.status_code == 200 and repeat.text == f"OK{inv_id}",
            f"{repeat.status_code} {repeat.text[:40]}",
        )
        card_after_repeat = (await c.get(f"{BASE}/deals/{deal['id']}")).json()
        check(
            "повтор не переоформил оплату (время не изменилось)",
            card_after_repeat["paid_at"] == card["paid_at"],
            f"{card_after_repeat['paid_at']} vs {card['paid_at']}",
        )

        print("\nПодделка после успешной оплаты")
        forged_after = await result_notify(c, "1500.00", inv_id, "1" * 32, shp_params=shp)
        check(
            "подделка на уже оплаченную сделку тоже отклоняется",
            forged_after.status_code == 403,
            forged_after.text[:80],
        )

        print("\nСтарая ссылка без Shp_ (до перехода на общий магазин) — обратная совместимость")
        legacy_deal = await new_link_deal(amount=70000)
        legacy_inv_id, _legacy_shp = inv_id_and_shp_from_url(legacy_deal.get("payment_url") or "")
        # У «старой» схемы InvId и был id сделки — подписываем БЕЗ Shp_, как
        # реальная Робокасса подписала бы уведомление на такую ссылку.
        sig_legacy = result_signature("700.00", legacy_deal["id"], password2)
        legacy_paid = await result_notify(c, "700.00", legacy_deal["id"], sig_legacy)
        check(
            "уведомление без Shp_ находит сделку по InvId напрямую",
            legacy_paid.status_code == 200 and legacy_paid.text == f"OK{legacy_deal['id']}",
            f"{legacy_paid.status_code} {legacy_paid.text[:40]}",
        )
        legacy_card = (await c.get(f"{BASE}/deals/{legacy_deal['id']}")).json()
        check("старая сделка помечена оплаченной", legacy_card["status"] == "paid", legacy_card["status"])

        print("\nНесуществующая сделка")
        ghost_shp = {"Shp_source": "esocrm", "Shp_deal_id": "9999999"}
        sig_ghost = result_signature("100.00", 2_009_999_999, password2, shp_params=ghost_shp)
        ghost = await result_notify(c, "100.00", 2_009_999_999, sig_ghost, shp_params=ghost_shp)
        check(
            "уведомление на несуществующую сделку не роняет сервер",
            ghost.status_code == 409,
            f"{ghost.status_code} {ghost.text[:80]}",
        )

        print("\nНечитаемый InvId")
        broken = await c.post(
            f"{BASE}/payments/robokassa/result",
            data={"OutSum": "100.00", "InvId": "не-число", "SignatureValue": "x"},
        )
        check("нечисловой InvId — 400, не 500", broken.status_code == 400, str(broken.status_code))

        print("\nНечитаемый Shp_deal_id")
        broken_shp = await c.post(
            f"{BASE}/payments/robokassa/result",
            data={
                "OutSum": "100.00",
                "InvId": "123",
                "SignatureValue": "x",
                "Shp_source": "esocrm",
                "Shp_deal_id": "не-число",
            },
        )
        check(
            "нечисловой Shp_deal_id — 400, не 500", broken_shp.status_code == 400, str(broken_shp.status_code)
        )

        print("\nИстёкшая сделка: ручное подтверждение закрыто, провайдер — нет")
        expired_deal = await new_link_deal(amount=90000)
        expired_inv_id, expired_shp = inv_id_and_shp_from_url(expired_deal.get("payment_url") or "")
        async with SessionLocal() as db:
            await db.execute(
                text("update deals set expires_at = now() - interval '1 day' where id = :i"),
                {"i": expired_deal["id"]},
            )
            await db.commit()
        moved = await expire_overdue_deals()
        check("сделка переведена в «истекла»", moved >= 1, str(moved))
        card = (await c.get(f"{BASE}/deals/{expired_deal['id']}")).json()
        check("статус — истекла", card["status"] == "expired", card["status"])

        uploaded = (
            await c.post(
                f"{BASE}/files/upload",
                files={"file": ("чек.png", io.BytesIO(RECEIPT_PNG), "image/png")},
            )
        ).json()
        manual = await c.post(
            f"{BASE}/deals/{expired_deal['id']}/pay",
            json={
                "receipt_upload_key": uploaded["upload_key"],
                "receipt_file_name": uploaded["file_name"],
                "receipt_mime_type": uploaded["mime_type"],
                "receipt_size_bytes": uploaded["size_bytes"],
            },
        )
        check(
            "ручное подтверждение истёкшей сделки закрыто и для ссылки",
            manual.status_code == 409,
            manual.text[:100],
        )

        sig_expired = result_signature("900.00", expired_inv_id, password2, shp_params=expired_shp)
        provider_paid = await result_notify(c, "900.00", expired_inv_id, sig_expired, shp_params=expired_shp)
        check(
            "провайдер подтверждает оплату истёкшей сделки — деньги важнее срока",
            provider_paid.status_code == 200 and provider_paid.text == f"OK{expired_inv_id}",
            f"{provider_paid.status_code} {provider_paid.text[:40]}",
        )
        card = (await c.get(f"{BASE}/deals/{expired_deal['id']}")).json()
        check("сделка оплачена несмотря на истёкший срок", card["status"] == "paid", card["status"])
        evt = next((e for e in card["events"] if e["kind"] == "paid"), None)
        check(
            "в комментарии видно, что оплата пришла после истечения",
            evt is not None and "просрочена" in (evt.get("comment") or ""),
            str(evt),
        )

    print(f"\nИтог: {len(ok)} выполнено, {len(bad)} не выполнено")
    for line in bad:
        print(f"  — {line}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
