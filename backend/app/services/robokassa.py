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
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from urllib.parse import quote, urlencode

import orjson

PAYMENT_PAGE = "https://auth.robokassa.ru/Merchant/Index.aspx"


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


def sign_link(
    *, merchant_login: str, out_sum: str, inv_id: int, receipt_encoded: str, password1: str
) -> str:
    """`MerchantLogin:OutSum:InvId:Receipt:Пароль#1` → MD5, нижним регистром.

    Робокасса принимает подпись в любом регистре, но сравнение и логи читаются
    ровнее, если у нас всегда один и тот же регистр на выходе.
    """
    raw = f"{merchant_login}:{out_sum}:{inv_id}:{receipt_encoded}:{password1}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()  # noqa: S324 — это формат Робокассы, не наш выбор


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
) -> str:
    """Собирается на нашей стороне, редиректа через сервер не требует.

    `description` — это поле формата запроса к Робокассе (её лимиты, её страница
    оплаты), не сообщение клиенту в чате: то сообщение собирает
    `deal_service.link_invoice_text()` и начинается с `deal.intro_text` (ТЗ п. 4.5).
    """
    out_sum = kopecks_to_robokassa(out_sum_kopecks)
    receipt = build_receipt(receipt_items, sno, tax)
    receipt_encoded = _receipt_json_encoded(receipt)
    signature = sign_link(
        merchant_login=merchant_login,
        out_sum=out_sum,
        inv_id=inv_id,
        receipt_encoded=receipt_encoded,
        password1=password1,
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
    # Receipt кодируем сами и подставляем как готовую строку: urlencode закодировал
    # бы уже закодированное значение повторно и сломал бы JSON.
    query = urlencode(params) + f"&Receipt={receipt_encoded}"
    return f"{PAYMENT_PAGE}?{query}"


def sign_result(*, out_sum: str, inv_id: int, password2: str) -> str:
    """`OutSum:InvId:Пароль#2` → MD5. Второй пароль, не первый — на ResultURL
    подписывает сама Робокасса, а не тот, кто собрал ссылку."""
    raw = f"{out_sum}:{inv_id}:{password2}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()  # noqa: S324 — формат Робокассы


def verify_result_signature(
    *, out_sum: str, inv_id: int, provided_signature: str, password2: str
) -> bool:
    """Сравнение без учёта регистра: Робокасса в проде шлёт заглавными буквами,
    в тестовом режиме встречается написание вперемешку."""
    if not provided_signature:
        return False
    expected = sign_result(out_sum=out_sum, inv_id=inv_id, password2=password2)
    return expected.lower() == provided_signature.strip().lower()


def out_sum_matches(out_sum_from_provider: str, expected_kopecks: int) -> bool:
    """Сумму сравниваем в копейках, не строками: «85.00» и «85.0» — одно и то же
    число, но разные строки. Верить строке из запроса нельзя, поэтому парсим
    защищённо: любое, что не разбирается как число, — несовпадение, не исключение."""
    try:
        provided = (Decimal(out_sum_from_provider) * 100).to_integral_value(
            rounding=ROUND_HALF_UP
        )
    except (ValueError, ArithmeticError):
        return False
    return int(provided) == expected_kopecks
