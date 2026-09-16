"""Робокасса: создание счёта, проверка входящих уведомлений.

Формулы сверены с docs/11-payments-architecture.md и официальной документацией
Робокассы. Проверка входящих уведомлений (`sign_result`/`verify_result_signature`)
— чистые функции, принимают пароль параметром, а не читают `settings` сами: так
модуль проверяется тестами без сети. Создание счёта (`create_invoice`) чистым
быть не может по своей природе — magазин esoterra-pay не принимает чек по 54-ФЗ
через классическую ссылку `Merchant/Index.aspx` (код ошибки 29, проверено вживую
на реальном магазине 2026-09-16 — сама подпись и пароли при этом верны), поэтому
единственный рабочий способ — Invoice API: подписанный JWT-запрос на сервер
Робокассы, ответом от которого и является ссылка на оплату. Тем же способом
создаёт свои счета бот Богдана (docs/11, разд. 8).

Раз создание ссылки требует сети — тестовый прогон (`tests/verify_robokassa.py`)
теперь дёргает настоящую Робокассу настоящими паролями каждый раз, когда
проверяет отправку счёта, и оставляет на реальном магазине esoterra-pay мелкие
неоплаченные счета-«призраки» (по рублю, никогда не оплаченные, сами истекают).
Это осознанный компромисс, не случайная утечка: без реального ключа магазин не
провалидирует подпись, а без сети чек 54-ФЗ этому магазину не передать никак.
"""

import base64
import hashlib
import hmac
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import httpx
import orjson

INVOICE_API_URL = "https://services.robokassa.ru/InvoiceServiceWebApi/api/CreateInvoice"

# Магазин esoterra-pay общий с ботом Богдана (docs/11-payments-architecture.md,
# разд. 8) — Shp_source отмечает наши счета, чтобы бот форвардил их уведомления
# нам, а не пытался найти их в своей базе. Регистр и порядок сверены с уже
# рабочей формулой в его коде (там ошибка Робокассы №29 на нижнем `shp_`).
SHP_SOURCE_PARAM = "Shp_source"
SHP_DEAL_ID_PARAM = "Shp_deal_id"
SHP_SOURCE_VALUE = "esocrm"

# Алгоритмы, которые Робокасса реально предлагает выбрать в личном кабинете
# магазина (Настройки → Технические настройки → Алгоритм расчёта хэша).
SUPPORTED_HASH_ALGS = frozenset({"md5", "sha1", "sha256", "sha512"})


def _digest(raw: str, hash_alg: str) -> str:
    """Без дефолта нарочно: молчаливый откат на MD5 при забытом параметре
    выглядел бы как «неверная подпись» на реальном магазине esoterra-pay
    (он настроен на SHA256) — искать причину пришлось бы вслепую, вместо
    явной ошибки прямо здесь."""
    if hash_alg not in SUPPORTED_HASH_ALGS:
        raise ValueError(f"Робокасса не поддерживает алгоритм {hash_alg!r} (у неё есть: {sorted(SUPPORTED_HASH_ALGS)})")
    return hashlib.new(hash_alg, raw.encode("utf-8")).hexdigest()  # noqa: S324 — формат Робокассы, не наш выбор


def kopecks_to_robokassa(kopecks: int) -> str:
    """8500 копеек → «85.00». Только Decimal: float даёт 84.99999999999999."""
    rubles = (Decimal(kopecks) / 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"{rubles:.2f}"


@dataclass(slots=True)
class ReceiptItem:
    name: str
    amount_kopecks: int


def _append_shp(raw: str, shp_params: dict[str, str] | None) -> str:
    """`:Shp_key=value`, отсортированные по имени без учёта регистра — Робокасса
    требует именно такой порядок в подписи (docs.robokassa.ru/ru/pay-interface).
    Пусто/None — строка не меняется, это и есть обратная совместимость со
    ссылками без пользовательских параметров."""
    if not shp_params:
        return raw
    ordered = sorted(shp_params.items(), key=lambda kv: kv[0].lower())
    return raw + "".join(f":{key}={value}" for key, value in ordered)


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _jwt_token(payload: dict[str, Any], *, merchant_login: str, password1: str, hash_alg: str) -> str:
    """JWT для Invoice API — свой формат Робокассы, не стандартный RFC 7519:
    `alg` в заголовке — буквально название алгоритма («SHA256»), не «HS256»;
    секрет — `MerchantLogin:Пароль#1` целиком, не сам пароль (docs.robokassa.ru/
    ru/invoice-api). Не переиспользует `_digest`: тут HMAC, а не голый хеш."""
    if hash_alg not in SUPPORTED_HASH_ALGS:
        raise ValueError(f"Робокасса не поддерживает алгоритм {hash_alg!r} (у неё есть: {sorted(SUPPORTED_HASH_ALGS)})")
    header_part = _b64url(orjson.dumps({"typ": "JWT", "alg": hash_alg.upper()}))
    payload_part = _b64url(orjson.dumps(payload))
    signing_input = f"{header_part}.{payload_part}".encode("utf-8")
    secret = f"{merchant_login}:{password1}".encode("utf-8")
    signature = hmac.new(secret, signing_input, hash_alg).digest()
    return f"{header_part}.{payload_part}.{_b64url(signature)}"


class InvoiceApiError(RuntimeError):
    """Робокасса не создала счёт — сеть, таймаут или отказ с её стороны."""


async def create_invoice(
    *,
    merchant_login: str,
    password1: str,
    hash_alg: str,
    inv_id: int,
    out_sum_kopecks: int,
    description: str,
    receipt_items: list[ReceiptItem],
    tax: str,
    expires_at: datetime | None = None,
    shp_deal_id: int | None = None,
    timeout: float = 20.0,
) -> str:
    """Создаёт счёт через Invoice API, возвращает ссылку на оплату.

    `sno` (система налогообложения) сюда сознательно НЕ передаётся: по
    документации Робокассы, если поле не указано, используется значение из
    личного кабинета магазина — надёжнее, чем угадывать значение, которое
    никто в компании не может подтвердить (см. обсуждение в истории проекта).

    `expires_at`, если передан, обязан быть timezone-aware — Invoice API (в
    отличие от классической ссылки) требует смещение часового пояса в формате
    ISO 8601, например «2026-12-31T23:59:59+03:00».
    """
    payload: dict[str, Any] = {
        "MerchantLogin": merchant_login,
        "InvoiceType": "OneTime",
        "Culture": "ru",
        "InvId": inv_id,
        # Число, не строка: JSON-тело (в отличие от query-параметров классической
        # ссылки) различает типы — "1.00" строкой Робокасса отклоняет как
        # «Некорректно составлен запрос», хотя формат-то ровно тот же самый.
        # Проверено вживую 2026-09-16. kopecks_to_robokassa всё равно оставляем
        # для округления через Decimal, просто переводим результат в float.
        "OutSum": float(kopecks_to_robokassa(out_sum_kopecks)),
        "Description": description[:100],
        "InvoiceItems": [
            {
                "Name": item.name,
                "Quantity": 1,
                "Cost": float(kopecks_to_robokassa(item.amount_kopecks)),
                "Tax": tax,
                "PaymentMethod": "full_payment",
                "PaymentObject": "service",
            }
            for item in receipt_items
        ],
        "IsWithoutFreeSale": True,
    }
    if expires_at is not None:
        payload["ExpirationDate"] = expires_at.isoformat(timespec="seconds")
    if shp_deal_id is not None:
        payload["UserFields"] = {SHP_SOURCE_PARAM: SHP_SOURCE_VALUE, SHP_DEAL_ID_PARAM: str(shp_deal_id)}

    token = _jwt_token(payload, merchant_login=merchant_login, password1=password1, hash_alg=hash_alg)

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                INVOICE_API_URL,
                content=orjson.dumps(token),
                headers={"Content-Type": "application/json", "Accept": "application/json"},
            )
    except httpx.HTTPError as exc:
        raise InvoiceApiError(f"Робокасса недоступна: {exc}") from exc

    try:
        data = resp.json()
    except ValueError as exc:
        raise InvoiceApiError(f"Робокасса вернула не-JSON ответ [{resp.status_code}]: {resp.text[:300]}") from exc

    if not isinstance(data, dict) or not data.get("isSuccess"):
        raise InvoiceApiError(f"Робокасса отклонила счёт [{resp.status_code}]: {data}")

    url = data.get("url")
    if not url:
        raise InvoiceApiError(f"Робокасса не вернула ссылку на оплату: {data}")
    return str(url)


def sign_result(
    *,
    out_sum: str,
    inv_id: int,
    password2: str,
    hash_alg: str,
    shp_params: dict[str, str] | None = None,
) -> str:
    """`OutSum:InvId:Пароль#2[:Shp_...]`. Второй пароль, не первый — на ResultURL
    подписывает сама Робокасса, а не тот, кто собрал ссылку. `Shp_`-параметры,
    если магазин их использует, входят в подпись наравне с остальными полями —
    без них проверка отклонит подлинное уведомление с такими параметрами."""
    raw = _append_shp(f"{out_sum}:{inv_id}:{password2}", shp_params)
    return _digest(raw, hash_alg)


def verify_result_signature(
    *,
    out_sum: str,
    inv_id: int,
    provided_signature: str,
    password2: str,
    hash_alg: str,
    shp_params: dict[str, str] | None = None,
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
