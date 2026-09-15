"""Приём уведомлений платёжного провайдера (Робокасса).

Публичный, без авторизации — сам провайдер не входит в CRM. Единственная защита
здесь — проверка подписи на каждый запрос, а не токен и не IP-фильтр (Робокасса
не публикует фиксированный список адресов).

Устройство разобрано в docs/11-payments-architecture.md, разд. 4 и 8. Коротко: сумма
и статус решаются только своей базой, из запроса берутся лишь Shp_deal_id (или
InvId — для ссылок, отправленных до перехода на общий магазин) и подпись;
повтор уведомления — норма, а не ошибка, и должен приводить к тому же ответу,
что и первая обработка.
"""

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy import exists, select, update
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.core.db import SessionLocal
from app.models import Deal, PaymentEvent
from app.services import deal_service, robokassa

router = APIRouter()
log = logging.getLogger("astra.payments")


@router.post(
    "/robokassa/result",
    summary="ResultURL Робокассы — подтверждение оплаты",
    description=(
        "Вызывает сама Робокасса, не человек. Тело ответа — служебный формат "
        "«OK<InvId>», который ждёт провайдер, а не JSON для интерфейса."
    ),
)
async def robokassa_result(request: Request) -> PlainTextResponse:
    # Робокасса шлёт form-urlencoded; на всякий случай подстраховываемся
    # query-параметрами — по документации бывают оба варианта в разных кабинетах.
    form = await request.form()
    params: dict[str, str] = {**request.query_params, **{k: str(v) for k, v in form.items()}}

    out_sum = params.get("OutSum", "")
    inv_id_raw = params.get("InvId", "")
    signature = params.get("SignatureValue", "")

    try:
        inv_id = int(inv_id_raw)
    except ValueError:
        log.warning("Робокасса: нечитаемый InvId %r", inv_id_raw)
        return PlainTextResponse("bad request", status_code=400)

    # Магазин esoterra-pay общий с ботом Богдана — InvId у нас со сдвигом
    # (deal_service.robokassa_inv_id) и не равен id сделки напрямую. Настоящий
    # адрес сделки — Shp_deal_id, который мы сами кладём в ссылку и который
    # Робокасса возвращает нетронутым. Пусто — старая ссылка без общего магазина
    # (сделана до этого перехода), там InvId и был id сделки: не отбиваем её.
    shp_params = robokassa.extract_shp_params(params)
    shp_deal_id_raw = next((v for k, v in shp_params.items() if k.lower() == "shp_deal_id"), "")
    try:
        deal_id = int(shp_deal_id_raw) if shp_deal_id_raw else inv_id
    except ValueError:
        log.warning("Робокасса: нечитаемый Shp_deal_id %r (InvId=%s)", shp_deal_id_raw, inv_id)
        return PlainTextResponse("bad request", status_code=400)

    valid = robokassa.verify_result_signature(
        out_sum=out_sum,
        inv_id=inv_id,
        provided_signature=signature,
        password2=settings.robokassa_active_password2,
        shp_params=shp_params,
        hash_alg=settings.robokassa_hash_alg,
    )

    # Пишем сырое уведомление ДО всякой логики, отдельной транзакцией — даже
    # подделку: иначе разбирать инцидент будет нечем, и её нельзя потерять из-за
    # отказа в бизнес-логике ниже. Ключ включает подпись: одинаковые повторные
    # доставки схлопываются в одну строку, а РАЗНЫЕ попытки по одному и тому же
    # InvId (например, кто-то подбирает подпись) получают каждая свою — это и
    # есть повод для тревоги из разд. 7, а не то, что можно потерять молча.
    event_id = f"robokassa:{inv_id}:{signature.strip().lower()}"
    async with SessionLocal() as db:
        # Верить deal_id нельзя, не проверив, что такая сделка вообще существует:
        # иначе вставка упадёт на внешнем ключе, и запись о самом уведомлении
        # (в том числе о подделке) будет потеряна.
        deal_exists = await db.scalar(select(exists().where(Deal.id == deal_id)))
        db.add(
            PaymentEvent(
                deal_id=deal_id if deal_exists else None,
                provider="robokassa",
                provider_event_id=event_id,
                event_type="result",
                payload=params,
                signature_valid=valid,
            )
        )
        try:
            await db.commit()
        except IntegrityError:
            # Уже видели ровно эту доставку раньше — не страшно, это и есть
            # повторная отправка, пока Робокасса не получит «OK».
            await db.rollback()

    if not valid:
        log.warning("Робокасса: неверная подпись для InvId=%s (deal_id=%s)", inv_id, deal_id)
        return PlainTextResponse("bad sign", status_code=403)

    expected_kopecks = robokassa.parse_out_sum_kopecks(out_sum)
    if expected_kopecks is None:
        return PlainTextResponse("bad request", status_code=400)

    async with SessionLocal() as db:
        result = await deal_service.confirm_paid_by_provider(
            db, deal_id, expected_kopecks, provider_payment_id=str(inv_id)
        )
        await db.execute(
            update(PaymentEvent)
            .where(PaymentEvent.provider_event_id == event_id)
            .values(processed_at=datetime.now(UTC))
        )
        await db.commit()

    if result.outcome == "amount_mismatch":
        log.error("Робокасса: сумма не сошлась для deal_id=%s (InvId=%s, OutSum=%s)", deal_id, inv_id, out_sum)
        return PlainTextResponse("sum mismatch", status_code=409)
    if result.outcome == "wrong_status":
        log.warning("Робокасса: сделка deal_id=%s (InvId=%s) не в статусе, допускающем оплату", deal_id, inv_id)
        return PlainTextResponse("wrong status", status_code=409)

    # "paid" и "already_paid" отвечают одинаково: провайдеру не нужно знать,
    # обработали мы сейчас или сделка уже была оплачена раньше — важен факт «ОК».
    return PlainTextResponse(f"OK{inv_id}")
