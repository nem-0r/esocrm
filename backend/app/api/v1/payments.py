"""Приём уведомлений платёжного провайдера (Робокасса).

Публичный, без авторизации — сам провайдер не входит в CRM. Единственная защита
здесь — проверка подписи на каждый запрос, а не токен и не IP-фильтр (Робокасса
не публикует фиксированный список адресов).

Устройство разобрано в docs/11-payments-architecture.md, разд. 4. Коротко: сумма
и статус решаются только своей базой, из запроса берутся лишь InvId и подпись;
повтор уведомления — норма, а не ошибка, и должен приводить к тому же ответу,
что и первая обработка.
"""

import logging

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.core.db import SessionLocal
from app.models import PaymentEvent
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

    valid = robokassa.verify_result_signature(
        out_sum=out_sum,
        inv_id=inv_id,
        provided_signature=signature,
        password2=settings.robokassa_password2,
    )

    # Пишем сырое уведомление ДО всякой логики, отдельной транзакцией — даже
    # подделку: иначе разбирать инцидент будет нечем, и её нельзя потерять из-за
    # отказа в бизнес-логике ниже. Ключ включает подпись: одинаковые повторные
    # доставки схлопываются в одну строку, а РАЗНЫЕ попытки по одному и тому же
    # InvId (например, кто-то подбирает подпись) получают каждая свою — это и
    # есть повод для тревоги из разд. 7, а не то, что можно потерять молча.
    event_id = f"robokassa:{inv_id}:{signature.strip().lower()}"
    async with SessionLocal() as db:
        db.add(
            PaymentEvent(
                deal_id=None,
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
        log.warning("Робокасса: неверная подпись для InvId=%s", inv_id)
        return PlainTextResponse("bad sign", status_code=403)

    try:
        expected_kopecks = int(round(float(out_sum) * 100))
    except (ValueError, TypeError):
        return PlainTextResponse("bad request", status_code=400)

    async with SessionLocal() as db:
        result = await deal_service.confirm_paid_by_provider(
            db, inv_id, expected_kopecks, provider_payment_id=str(inv_id)
        )

    if result.outcome == "amount_mismatch":
        log.error("Робокасса: сумма не сошлась для InvId=%s (OutSum=%s)", inv_id, out_sum)
        return PlainTextResponse("sum mismatch", status_code=409)
    if result.outcome == "wrong_status":
        log.warning("Робокасса: сделка InvId=%s не в статусе, допускающем оплату", inv_id)
        return PlainTextResponse("wrong status", status_code=409)

    # "paid" и "already_paid" отвечают одинаково: провайдеру не нужно знать,
    # обработали мы сейчас или сделка уже была оплачена раньше — важен факт «ОК».
    return PlainTextResponse(f"OK{inv_id}")
