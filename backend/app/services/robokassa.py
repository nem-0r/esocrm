"""Робокасса: сборка ссылки на оплату и проверка уведомлений.

Формулы сверены с docs/11-payments-architecture.md и официальной документацией
Робокассы. Функции здесь чистые — принимают логин и пароли параметрами, а не
читают `settings` сами. Так модуль проверяется тестами без поднятого приложения
и без прогона через HTTP, а вызывающий код (`deal_service.py`, `api/v1/payments.py`)
остаётся единственным местом, которое решает, откуда брать секреты.

Ключевая ловушка, из-за которой стоит держать всё в одном месте: `Receipt` входит
в строку подписи уже URL-кодированным, ровно в том виде, в котором уйдёт в запросе.
Закодировать после подписи — значит получить «неверная подпись» и полдня искать
причину. `build_payment_url` и `sign_link` используют один и тот же кодированный
JSON, чтобы разъехаться было негде.
"""

import hashlib
import hmac
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from urllib.parse import quote, urlencode

import orjson

PAYMENT_PAGE = "https://auth.robokassa.ru/Merchant/Index.aspx"

# Магазин esoterra-pay общий с ботом Богдана (docs/11-payments-architecture.md,
# разд. 8) — Shp_source отмечает наши ссылки, чтобы бот форвардил их уведомления
# нам, а не пытался найти их в своей базе. Регистр и порядок сверены с уже
# рабочей формулой в его коде (там ошибка Робокассы №29 на нижнем `shp_`).
SHP_SOURCE_PARAM = "Shp_source"
SHP_DEAL_ID_PARAM = "Shp_deal_id"
SHP_SOURCE_VALUE = "esocrm"


def kopecks_to_robokassa(kopecks: int) -> str:
    """8500 копеек → «85.00». Только Decimal: float даёт 84.99999999999999."""
    rubles = (Decimal(kopecks) / 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"{rubles:.2f}"


@dataclass(slots=True)
class ReceiptItem:
    name: str
    amount_kopecks: int


def build_receipt(items: list[ReceiptItem], sno: str, tax: str) -> dict[str, Any]:
    """Чек по 54-ФЗ. `sno`/`tax` — из настроек (вопрос к бухгалтеру, не константа)."""
    return {
        "sno": sno,
        "items": [
            {
                "name": item.name,
                "quantity": 1,
                "sum": float(kopecks_to_robokassa(item.amount_kopecks)),
                "payment_method": "full_payment",
                "payment_object": "service",
                "tax": tax,
            }
            for item in items
        ],
    }


def _receipt_json_encoded(receipt: dict[str, Any]) -> str:
    """JSON без пробелов, потом URL-кодирование — именно в этом виде он входит
    и в строку подписи, и в саму ссылку. Ключи не сортируем: Робокасса не требует
    определённого порядка, а сортировка чужого требования не отменяет."""
    return quote(orjson.dumps(receipt).decode("utf-8"), safe="")


def _append_shp(raw: str, shp_params: dict[str, str] | None) -> str:
    """`:Shp_key=value`, отсортированные по имени без учёта регистра — Робокасса
    требует именно такой порядок в подписи (docs.robokassa.ru/ru/pay-interface).
    Пусто/None — строка не меняется, это и есть обратная совместимость со
    ссылками без пользовательских параметров."""
    if not shp_params:
        return raw
    ordered = sorted(shp_params.items(), key=lambda kv: kv[0].lower())
    return raw + "".join(f":{key}={value}" for key, value in ordered)


def sign_link(
    *,
    merchant_login: str,
    out_sum: str,
    inv_id: int,
    receipt_encoded: str,
    password1: str,
    shp_params: dict[str, str] | None = None,
    hash_alg: str = "md5",
) -> str:
    """`MerchantLogin:OutSum:InvId:Receipt:Пароль#1[:Shp_...]` → нижним регистром.

    Алгоритм — параметром, а не константой: у разных магазинов Робокассы он
    настраивается в личном кабинете (esoterra-pay использует SHA256, не MD5).
    Робокасса принимает подпись в любом регистре, но сравнение и логи читаются
    ровнее, если у нас всегда один и тот же регистр на выходе.
    """
    raw = _append_shp(f"{merchant_login}:{out_sum}:{inv_id}:{receipt_encoded}:{password1}", shp_params)
    return hashlib.new(hash_alg, raw.encode("utf-8")).hexdigest()  # noqa: S324 — формат Робокассы, не наш выбор


def build_payment_url(
    *,
    merchant_login: str,
    password1: str,
    inv_id: int,
    out_sum_kopecks: int,
    description: str,
    receipt_items: list[ReceiptItem],
    sno: str,
    tax: str,
    is_test: bool,
    expires_at: datetime | None = None,
    hash_alg: str = "md5",
    shp_deal_id: int | None = None,
) -> str:
    """Собирается на нашей стороне, редиректа через сервер не требует.

    `description` — это поле формата запроса к Робокассе (её лимиты, её страница
    оплаты), не сообщение клиенту в чате: то сообщение собирает
    `deal_service.link_invoice_text()` и начинается с `deal.intro_text` (ТЗ п. 4.5).

    `expires_at`, если передан, обязан быть уже в местном времени организации
    (aware или naive — используется только `strftime`) и НЕ входит в подпись
    (docs.robokassa.ru/ru/pay-interface — ExpirationDate не участвует в формуле
    SignatureValue, в отличие от Receipt).

    `shp_deal_id`, если передан, помечает ссылку как нашу (`Shp_source=esocrm`)
    для общего магазина esoterra-pay — по нему бот-посредник поймёт, что уведомление
    нужно переслать нам, а не искать в своей базе (docs/11, разд. 8).
    """
    out_sum = kopecks_to_robokassa(out_sum_kopecks)
    receipt = build_receipt(receipt_items, sno, tax)
    receipt_encoded = _receipt_json_encoded(receipt)
    shp_params = (
        {SHP_SOURCE_PARAM: SHP_SOURCE_VALUE, SHP_DEAL_ID_PARAM: str(shp_deal_id)}
        if shp_deal_id is not None
        else None
    )
    signature = sign_link(
        merchant_login=merchant_login,
        out_sum=out_sum,
        inv_id=inv_id,
        receipt_encoded=receipt_encoded,
        password1=password1,
        shp_params=shp_params,
        hash_alg=hash_alg,
    )
    # Description ограничена Робокассой; обрезаем с запасом, чтобы не поймать
    # отказ на длинном названии услуги.
    params = {
        "MerchantLogin": merchant_login,
        "OutSum": out_sum,
        "InvId": inv_id,
        "Description": description[:100],
        "SignatureValue": signature,
    }
    if is_test:
        params["IsTest"] = "1"
    if expires_at is not None:
        params["ExpirationDate"] = expires_at.strftime("%Y-%m-%dT%H:%M")
    if shp_params:
        params.update(shp_params)
    # Receipt кодируем сами и подставляем как готовую строку: urlencode закодировал
    # бы уже закодированное значение повторно и сломал бы JSON.
    query = urlencode(params) + f"&Receipt={receipt_encoded}"
    return f"{PAYMENT_PAGE}?{query}"


def sign_result(
    *,
    out_sum: str,
    inv_id: int,
    password2: str,
    shp_params: dict[str, str] | None = None,
    hash_alg: str = "md5",
) -> str:
    """`OutSum:InvId:Пароль#2[:Shp_...]`. Второй пароль, не первый — на ResultURL
    подписывает сама Робокасса, а не тот, кто собрал ссылку. `Shp_`-параметры,
    если магазин их использует, входят в подпись наравне с остальными полями —
    без них проверка отклонит подлинное уведомление с такими параметрами."""
    raw = _append_shp(f"{out_sum}:{inv_id}:{password2}", shp_params)
    return hashlib.new(hash_alg, raw.encode("utf-8")).hexdigest()  # noqa: S324 — формат Робокассы


def verify_result_signature(
    *,
    out_sum: str,
    inv_id: int,
    provided_signature: str,
    password2: str,
    shp_params: dict[str, str] | None = None,
    hash_alg: str = "md5",
) -> bool:
    """Сравнение без учёта регистра (Робокасса в проде шлёт заглавными буквами,
    в тестовом режиме встречается написание вперемешку) и за постоянное время —
    обе строки после `.lower()` гарантированно ASCII-hex, `hmac.compare_digest`
    не упадёт (в отличие от сравнения произвольных non-ASCII строк)."""
    if not provided_signature:
        return False
    expected = sign_result(
        out_sum=out_sum, inv_id=inv_id, password2=password2, shp_params=shp_params, hash_alg=hash_alg
    )
    return hmac.compare_digest(expected.lower(), provided_signature.strip().lower())


class InvalidShpParams(ValueError):
    """Shp-параметр содержит `:`/`=` — символы-разделители самой строки подписи."""


def extract_shp_params(params: dict[str, str]) -> dict[str, str]:
    """Пользовательские `Shp_*` из входящего уведомления, регистр ключей как
    пришёл (важно: подпись зависит от регистра имени ключа, не только от
    сравнения в нижнем регистре при поиске `startswith`).

    `:`/`=` в имени или значении — это разделители самой строки подписи
    (`_append_shp`), они не экранируются. Без этой проверки один параметр вида
    `Shp_x=a:Shp_y` мог бы дать байт-в-байт ту же подписываемую строку, что и
    два настоящих параметра `Shp_x=a` и `Shp_y=...` — то есть валидную подпись
    можно получить на НЕ тот набор пар ключ-значение, который в итоге разберут.
    Отклоняем целиком, а не отфильтровываем один параметр: раз подделка возможна
    в принципе, доверять остальным параметрам того же запроса тоже нельзя."""
    result: dict[str, str] = {}
    for key, value in params.items():
        if not key.lower().startswith("shp_"):
            continue
        if ":" in key or "=" in key or ":" in value or "=" in value:
            raise InvalidShpParams(key)
        result[key] = value
    return result


def parse_out_sum_kopecks(out_sum: str) -> int | None:
    """OutSum Робокассы → копейки, только через Decimal: float даёт
    84.99999999999999 там, где должно быть ровно 85.00. Не разобралось — None,
    не исключение: строка целиком приходит от внешнего запроса, доверять ей нельзя."""
    try:
        return int((Decimal(out_sum) * 100).to_integral_value(rounding=ROUND_HALF_UP))
    except (ValueError, ArithmeticError, TypeError):
        return None


def out_sum_matches(out_sum_from_provider: str, expected_kopecks: int) -> bool:
    """Сумму сравниваем в копейках, не строками: «85.00» и «85.0» — одно и то же
    число, но разные строки."""
    provided = parse_out_sum_kopecks(out_sum_from_provider)
    return provided is not None and provided == expected_kopecks
