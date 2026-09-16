"""Приём уведомлений платёжного провайдера (Робокасса).

Публичный, без авторизации — сам провайдер не входит в CRM. Единственная защита
здесь — проверка подписи на каждый запрос, а не токен и не IP-фильтр (Робокасса
не публикует фиксированный список адресов). Именно поэтому всё, что приходит
в запросе, — вражеский ввод: валидируем диапазоны/формат ДО обращения к базе,
чтобы поддельный запрос падал на понятной проверке, а не на исключении СУБД
(из-за которого запись о самой подделке потерялась бы, см. ниже).

Устройство разобрано в docs/11-payments-architecture.md, разд. 8. Коротко: сумма
и статус решаются только своей базой, из запроса берётся лишь Shp_deal_id и подпись;
повтор уведомления — норма, а не ошибка, и должен приводить к тому же ответу,
что и первая обработка.
"""

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy import exists, select, update
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.core.config import settings
from app.core.db import SessionLocal
from app.models import Deal, PaymentEvent
from app.services import deal_service, robokassa

router = APIRouter()
log = logging.getLogger("astra.payments")

# InvId у нас — Robokassa-int (asyncpg отдаёт BigInteger, максимум int64).
# Значения вне диапазона не долетят до базы валидной сделкой ни при каком раскладе —
# отбиваем на входе, а не на исключении СУБД.
_MAX_INT64 = 2**63 - 1
# Обычный hex-дайджест (SHA256 — 64 символа, SHA512 — 128) плюс запас на будущее.
_MAX_SIGNATURE_LEN = 128
_HEX_CHARS = frozenset("0123456789abcdefABCDEF")
_ASCII_DIGITS = frozenset("0123456789")


def _get_ci(params: dict[str, str], name: str) -> str:
    """Регистронезависимый поиск — Робокасса не гарантирует один и тот же
    регистр имени параметра во всех кабинетах (у бота-посредника код отдельно
    подстраховывается на этот счёт под именем InvId/InvID)."""
    lname = name.lower()
    return next((v for k, v in params.items() if k.lower() == lname), "")


def _parse_ascii_id(raw: str) -> int | None:
    """Строго ASCII-цифры, без `+`/`_`/юникод-цифр, которые молча принимает
    обычный `int()` (`int("١٢٣")==123`, `int("1_000")==1000`) — Робокасса
    подписывает уведомление ИМЕННО той строкой, что прислала, а бот-посредник
    сверяет подпись по сырой строке (не канонизирует), не по нашему `int()`.
    Разница в допустимости форматов — это разные проверки на двух концах
    одной цепочки, а значит потенциальное расхождение "бот принял — мы нет"
    или наоборот. Дешевле сузить приём до того, что Робокасса реально шлёт."""
    if not raw or not set(raw) <= _ASCII_DIGITS:
        return None
    value = int(raw)
    return value if 0 < value <= _MAX_INT64 else None


def _sanitize_for_jsonb(params: dict[str, str]) -> dict[str, str]:
    """NUL-байт ломает вставку в JSONB (Postgres 22P05) — вырезаем его из
    ключей И значений, а не отклоняем весь запрос: это поле только для аудита,
    а не для логики ниже. Чистить только значения (как было раньше) было
    ошибкой: NUL в ИМЕНИ параметра ломает INSERT ровно так же, просто на
    другой части строки — и раньше это тихо проглатывалось общим except."""
    return {k.replace("\x00", ""): v.replace("\x00", "") for k, v in params.items()}


@router.post(
    "/robokassa/result",
    summary="ResultURL Робокассы — подтверждение оплаты",
    description=(
        "Вызывает сама Робокасса, не человек. Тело ответа — служебный формат "
        "«OK<InvId>», который ждёт провайдер, а не JSON для интерфейса."
    ),
)
async def robokassa_result(request: Request) -> PlainTextResponse:
    if not settings.robokassa_enabled:
        # Без активных паролей verify_result_signature подписывала бы пустой
        # строкой — «неверной подписи» тогда не бывает вообще, любой запрос
        # с правильно посчитанным (без пароля!) хешем прошёл бы проверку.
        # Раз оплата по ссылке не настроена — эндпоинт не должен принимать
        # решения по сделкам вообще, а не полагаться на пустой пароль как на отказ.
        log.warning("Робокасса: уведомление получено, но приём по ссылке не настроен")
        return PlainTextResponse("robokassa disabled", status_code=503)

    # Робокасса шлёт только application/x-www-form-urlencoded. Принимать ещё и
    # multipart незачем — это спуллинг во временные файлы на непроверенном
    # публичном вводе, а UploadFile в JSONB-аудите превращается в бесполезную
    # строку вида "UploadFile(filename=...)". Отбиваем раньше, чем FastAPI
    # начнёт разбирать тело.
    content_type = (request.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
    if content_type and content_type not in ("application/x-www-form-urlencoded", ""):
        log.warning("Робокасса: неожиданный Content-Type %r", content_type[:60])
        return PlainTextResponse("bad request", status_code=400)

    # На всякий случай подстраховываемся query-параметрами — по документации
    # бывают оба варианта в разных кабинетах.
    form = await request.form()
    params: dict[str, str] = {**request.query_params, **{k: str(v) for k, v in form.items()}}

    out_sum = _get_ci(params, "OutSum")
    inv_id_raw = _get_ci(params, "InvId")
    signature = _get_ci(params, "SignatureValue").strip()

    inv_id = _parse_ascii_id(inv_id_raw)
    if inv_id is None:
        log.warning("Робокасса: нечитаемый InvId %r", inv_id_raw[:50])
        return PlainTextResponse("bad request", status_code=400)

    if not signature or len(signature) > _MAX_SIGNATURE_LEN or not set(signature) <= _HEX_CHARS:
        # Настоящая подпись Робокассы — это всегда hex-дайджест разумной длины.
        # Проверяем ДО похода в базу: длинная строка иначе переполнила бы
        # provider_event_id (String(255)) и упала бы не тем исключением, что мы
        # ловим ниже, а произвольный не-ASCII символ уронил бы hmac.compare_digest.
        log.warning("Робокасса: подпись не похожа на подпись (len=%s)", len(signature))
        return PlainTextResponse("bad request", status_code=400)

    # Магазин esoterra-pay общий с ботом Богдана — InvId у нас со сдвигом
    # (deal_service.fresh_robokassa_inv_id) и не равен id сделки напрямую. Настоящий
    # адрес сделки — Shp_deal_id, который мы сами кладём в ссылку и который
    # Робокасса возвращает нетронутым. Робокасса никогда не работала с этим
    # магазином до перехода на общую схему — «старых» ссылок без Shp_deal_id
    # в реальности нет, поэтому его отсутствие — это подделка/ошибка, а не
    # легитимный legacy-случай, и разбирать его как валидный больше не нужно.
    try:
        shp_params = robokassa.extract_shp_params(params)
    except robokassa.InvalidShpParams:
        log.warning("Робокасса: недопустимый символ в Shp-параметре (InvId=%s)", inv_id)
        return PlainTextResponse("bad request", status_code=400)

    shp_deal_id_values = {v for k, v in shp_params.items() if k.lower() == "shp_deal_id"}
    if len(shp_deal_id_values) != 1:
        # Ровно один, не «хотя бы один»: два РАЗНЫХ значения под одним именем
        # в разном регистре означали бы, что кто-то целится в конкретную
        # сделку, спрятав дубль — надёжнее отказать, чем гадать, какое верное.
        # Одинаковые значения под разным регистром имени (Shp_deal_id и
        # SHP_DEAL_ID с одним и тем же числом) сюда не попадают — множество
        # схлопывает равные значения, это не тот случай, что выше.
        log.warning("Робокасса: не ровно один Shp_deal_id (InvId=%s, получено %s)", inv_id, len(shp_deal_id_values))
        return PlainTextResponse("bad request", status_code=400)
    deal_id = _parse_ascii_id(next(iter(shp_deal_id_values)))
    if deal_id is None:
        log.warning("Робокасса: нечитаемый Shp_deal_id (InvId=%s)", inv_id)
        return PlainTextResponse("bad request", status_code=400)

    valid = robokassa.verify_result_signature(
        out_sum=out_sum,
        inv_id=inv_id,
        provided_signature=signature,
        password2=settings.robokassa_active_password2,
        shp_params=shp_params,
        hash_alg=settings.robokassa_hash_alg,
    )

    # Пишем сырое уведомление ДО бизнес-логики, отдельной транзакцией — даже
    # подделку с неверной подписью: иначе разбирать попытку подбора будет нечем.
    # Но только правдоподобную по форме (числа в диапазоне, подпись похожа на
    # подпись, Shp_deal_id ровно один) — совсем случайный мусор отбивается ещё
    # раньше и до базы не доходит вообще: осознанная защита от заполнения
    # таблицы кем попало, а не потеря аудита.
    event_id = f"robokassa:{inv_id}:{signature.lower()}"
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
                payload=_sanitize_for_jsonb(params),
                signature_valid=valid,
            )
        )
        try:
            await db.commit()
        except IntegrityError:
            # Уже видели ровно эту доставку раньше — не страшно, это и есть
            # повторная отправка, пока Робокасса не получит «OK».
            await db.rollback()
        except DBAPIError:
            # Значение прошло валидацию выше, но СУБД всё равно отказала —
            # неожиданно, и молчать нельзя: раньше этот случай ловился вместе
            # с IntegrityError и терялся без единой строки в логе, хотя именно
            # тут аудит-запись о попытке не создалась.
            log.exception("Робокасса: не удалось записать PaymentEvent (InvId=%s, deal_id=%s)", inv_id, deal_id)
            await db.rollback()

    if not valid:
        log.warning("Робокасса: неверная подпись для InvId=%s (deal_id=%s)", inv_id, deal_id)
        return PlainTextResponse("bad sign", status_code=403)

    received_kopecks = robokassa.parse_out_sum_kopecks(out_sum)
    if received_kopecks is None:
        return PlainTextResponse("bad request", status_code=400)

    async with SessionLocal() as db:
        result = await deal_service.confirm_paid_by_provider(
            db, deal_id, received_kopecks, provider_payment_id=str(inv_id)
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
