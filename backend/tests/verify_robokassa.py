"""Проверка приёма оплат через Робокассу: счёт, подпись, сумма, повтор.

Магазин esoterra-pay общий с ботом Богдана (docs/11-payments-architecture.md,
разд. 8): InvId у нас со сдвигом и не равен id сделки, настоящий адрес сделки —
Shp_deal_id. Shp_deal_id детерминирован (это и есть id сделки), а InvId —
НЕТ: Робокасса требует уникальный номер НАВСЕГДА, повтор (пересборка счёта,
пересоздание демо-данных) она отклоняет как «Заказ с таким Id уже существует»
— поэтому InvId свежий на каждый вызов (deal_service.fresh_robokassa_inv_id),
и тест читает реально использованное значение из базы (provider_payment_id),
а не вычисляет и не вытаскивает из ссылки — начиная с перехода на Invoice API
ссылка (`auth.robokassa.ru/merchant/Invoice/<guid>`) параметров не несёт вовсе,
это просто редирект-адрес созданного на сервере счёта.

ВАЖНО — раньше этот скрипт не трогал сеть вообще («играл за Робокассу сам»).
Теперь каждый `new_link_deal()` — это НАСТОЯЩИЙ запрос к настоящей Робокассе
(созданию счёта нужен рабочий JWT, подписанный реальными паролями): нужны
настоящие ROBOKASSA_PASSWORD1/2 в .env, и на реальном магазине esoterra-pay
после каждого прогона остаются мелкие неоплаченные счета-«призраки» (сами
истекают, деньги не списываются). Это осознанный компромисс: магазин не
принимает чек по 54-ФЗ через старую офлайн-сборку ссылки (код ошибки 29,
проверено вживую), а без сети чек этому магазину не передать никак.
Проверку самого ResultURL (подпись, Shp_-параметры) это не касается — она
по-прежнему играет за Робокассу сама, никуда не стучится.

Скрипт меняет демо-данные (создаёт сделки). После него положено выполнить `make seed`.

Запуск: docker compose exec -T api python -m tests.verify_robokassa
"""

import asyncio
import hashlib
import io
import sys

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


async def inv_id_and_shp_for_deal(deal_id: int) -> tuple[int, dict[str, str]]:
    """InvId — свежий на каждый вызов (не функция от id), читаем реально
    сохранённый provider_payment_id из базы. Shp_deal_id — просто id сделки,
    его вычислять не нужно."""
    async with SessionLocal() as db:
        row = (
            await db.execute(
                text("select provider_payment_id from deals where id = :i"), {"i": deal_id}
            )
        ).first()
    return int(row[0]), {"Shp_source": "esocrm", "Shp_deal_id": str(deal_id)}


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

        print("Счёт на оплату (Invoice API, реальный запрос к Робокассе)")
        deal = await new_link_deal()
        url = deal.get("payment_url") or ""
        check(
            "ссылка на оплату — настоящий счёт Invoice API",
            url.startswith("https://auth.robokassa.ru/merchant/Invoice/"),
            url[:80],
        )
        inv_id, shp = await inv_id_and_shp_for_deal(deal["id"])
        check("InvId смещён — не равен id сделки напрямую", inv_id != deal["id"], str(inv_id))
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

        print("\nВторое, ОТЛИЧНОЕ несовпадение суммы по той же сделке — тоже должно попасть в журнал")
        bad_sum_2 = "2.00"
        sig_bad_sum_2 = result_signature(bad_sum_2, inv_id, password2, shp_params=shp)
        tampered_2 = await result_notify(c, bad_sum_2, inv_id, sig_bad_sum_2, shp_params=shp)
        check(
            "второе несовпадение тоже отклоняется",
            tampered_2.status_code == 409,
            tampered_2.text[:80],
        )
        card_after_second_mismatch = (await c.get(f"{BASE}/deals/{deal['id']}")).json()
        mismatch_events = [e for e in card_after_second_mismatch["events"] if e["kind"] == "edited"]
        check(
            "оба несовпадения (разные суммы) записаны в журнал сделки, не только первое",
            len(mismatch_events) >= 2,
            f"событий edited: {len(mismatch_events)}",
        )

        print("\nNUL-байт в ИМЕНИ параметра — запись в журнал не должна теряться")
        nul_key_deal = await new_link_deal(amount=40000)
        nul_inv_id, nul_shp = await inv_id_and_shp_for_deal(nul_key_deal["id"])
        sig_nul = "0" * 64  # заведомо неверная, но по формату — не важно, что случится дальше
        event_id_nul = f"robokassa:{nul_inv_id}:{sig_nul.lower()}"
        nul_resp = await c.post(
            f"{BASE}/payments/robokassa/result",
            data={
                "OutSum": "400.00",
                "InvId": str(nul_inv_id),
                "SignatureValue": sig_nul,
                "Shp_source": "esocrm",
                "Shp_deal_id": str(nul_shp.get("Shp_deal_id")),
                "junk\x00key": "value\x00with\x00nul",
            },
        )
        check(
            "запрос с NUL-байтом в имени параметра отклоняется как неверная подпись, не 500",
            nul_resp.status_code == 403,
            f"{nul_resp.status_code} {nul_resp.text[:80]}",
        )
        async with SessionLocal() as db:
            row = (
                await db.execute(
                    text("select id from payment_events where provider_event_id = :e"),
                    {"e": event_id_nul},
                )
            ).first()
        check(
            "запись об уведомлении с NUL в имени параметра всё равно попала в журнал",
            row is not None,
            "строка не найдена в payment_events — аудит потерян",
        )

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

        print("\nУведомление без Shp_deal_id — отклоняется, не «находит по InvId»")
        # Магазин общий с ботом Богдана существует только в этой схеме — «старых»
        # ссылок без Shp_deal_id в реальности никогда не было, отсутствие этого
        # параметра теперь однозначно подделка/ошибка, а не легитимный legacy-путь.
        no_shp_deal = await new_link_deal(amount=70000)
        no_shp_inv_id, _ = await inv_id_and_shp_for_deal(no_shp_deal["id"])
        sig_no_shp = result_signature("700.00", no_shp_inv_id, password2)
        no_shp_resp = await result_notify(c, "700.00", no_shp_inv_id, sig_no_shp)
        check(
            "без Shp_deal_id — 400, сделка НЕ находится по InvId напрямую",
            no_shp_resp.status_code == 400,
            f"{no_shp_resp.status_code} {no_shp_resp.text[:80]}",
        )
        no_shp_card = (await c.get(f"{BASE}/deals/{no_shp_deal['id']}")).json()
        check("сделка без Shp_deal_id не тронута", no_shp_card["status"] == "awaiting", no_shp_card["status"])

        print("\nПодмена набора Shp_-параметров через «:»/«=» в имени — отклоняется")
        inj_deal = await new_link_deal(amount=55000)
        inj_inv_id, inj_shp = await inv_id_and_shp_for_deal(inj_deal["id"])
        # То же самое итоговое множество пар после сортировки, что и настоящее
        # (Shp_deal_id=<id>, Shp_source=esocrm), но с двоеточием в имени ключа —
        # раньше это давало байт-в-байт ту же строку для подписи на другой набор.
        sneaky_shp = {f"Shp_deal_id={inj_shp.get('Shp_deal_id')}:Shp_source": "esocrm"}
        sig_sneaky = result_signature("550.00", inj_inv_id, password2, shp_params=sneaky_shp)
        sneaky_resp = await result_notify(c, "550.00", inj_inv_id, sig_sneaky, shp_params=sneaky_shp)
        check(
            "Shp-параметр с «:» в имени отклоняется, а не разбирается",
            sneaky_resp.status_code == 400,
            f"{sneaky_resp.status_code} {sneaky_resp.text[:80]}",
        )
        inj_card = (await c.get(f"{BASE}/deals/{inj_deal['id']}")).json()
        check("сделка с «инъекцией» в Shp_ не тронута", inj_card["status"] == "awaiting", inj_card["status"])

        print("\nДва Shp_deal_id в разном регистре имени — отклоняется, не берём первый попавшийся")
        dup_deal = await new_link_deal(amount=45000)
        dup_inv_id, dup_shp = await inv_id_and_shp_for_deal(dup_deal["id"])
        dup_shp_bad = {**dup_shp, "SHP_DEAL_ID": "9999999"}
        sig_dup = result_signature("450.00", dup_inv_id, password2, shp_params=dup_shp_bad)
        dup_resp = await result_notify(c, "450.00", dup_inv_id, sig_dup, shp_params=dup_shp_bad)
        check(
            "два разных Shp_deal_id одновременно — 400, а не оплата первого попавшегося",
            dup_resp.status_code == 400,
            f"{dup_resp.status_code} {dup_resp.text[:80]}",
        )
        dup_card = (await c.get(f"{BASE}/deals/{dup_deal['id']}")).json()
        check("сделка с двойным Shp_deal_id не тронута", dup_card["status"] == "awaiting", dup_card["status"])

        print("\nСделка по реквизитам — провайдер её подтвердить не может, даже с верной подписью")
        requisites_raw = (await c.get(f"{BASE}/requisites")).json()
        requisites = requisites_raw if isinstance(requisites_raw, list) else requisites_raw.get("items", [])
        if requisites:
            req_deal_resp = await c.post(
                f"{BASE}/deals",
                json={
                    "conversation_id": conversation_id,
                    "payment_method": "requisites",
                    "requisite_id": requisites[0]["id"],
                    "items": [{"name": "Проверка Робокассы — по реквизитам", "amount": 30000}],
                },
            )
            req_deal = req_deal_resp.json()
            await c.post(f"{BASE}/deals/{req_deal['id']}/send")
            # У сделки по реквизитам никогда не было ссылки Робокассы — подписываем
            # уведомление так, будто её InvId/Shp_deal_id — этот id (подобрать
            # такую подпись без Password2 нельзя, но сам факт совпадения id и
            # суммы не должен ничего решать за payment_method).
            fake_inv_id = 2_000_000_000 + req_deal["id"]
            fake_shp = {"Shp_source": "esocrm", "Shp_deal_id": str(req_deal["id"])}
            sig_req = result_signature("300.00", fake_inv_id, password2, shp_params=fake_shp)
            req_resp = await result_notify(c, "300.00", fake_inv_id, sig_req, shp_params=fake_shp)
            check(
                "верная подпись, но сделка не по ссылке — 409, не оплата",
                req_resp.status_code == 409,
                f"{req_resp.status_code} {req_resp.text[:80]}",
            )
            req_card = (await c.get(f"{BASE}/deals/{req_deal['id']}")).json()
            check(
                "сделка по реквизитам не помечена оплаченной провайдером",
                req_card["status"] == "awaiting",
                req_card["status"],
            )
        else:
            check("сделка по реквизитам — провайдер её подтвердить не может", False, "нет реквизитов для теста")

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
                # Формально валидная по формату подпись (hex, разумная длина) —
                # проверяем, что запрос падает именно на разборе Shp_deal_id,
                # а не раньше на формате самой подписи.
                "SignatureValue": "0" * 64,
                "Shp_source": "esocrm",
                "Shp_deal_id": "не-число",
            },
        )
        check(
            "нечисловой Shp_deal_id — 400, не 500", broken_shp.status_code == 400, str(broken_shp.status_code)
        )

        print("\nПодпись не похожа на подпись (не hex / слишком длинная)")
        weird_sig = await c.post(
            f"{BASE}/payments/robokassa/result",
            data={"OutSum": "100.00", "InvId": "123", "SignatureValue": "not-a-hex-signature"},
        )
        check(
            "нестандартная подпись отклоняется на входе, не доходит до сравнения",
            weird_sig.status_code == 400,
            str(weird_sig.status_code),
        )
        too_long_sig = await c.post(
            f"{BASE}/payments/robokassa/result",
            data={"OutSum": "100.00", "InvId": "123", "SignatureValue": "a" * 300},
        )
        check(
            "слишком длинная подпись отклоняется, не долетает до колонки БД",
            too_long_sig.status_code == 400,
            str(too_long_sig.status_code),
        )

        print("\nИстёкшая сделка: ручное подтверждение закрыто, провайдер — нет")
        expired_deal = await new_link_deal(amount=90000)
        expired_inv_id, expired_shp = await inv_id_and_shp_for_deal(expired_deal["id"])
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
