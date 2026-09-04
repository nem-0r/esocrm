"""Деньги — целые копейки. Дробных типов под деньги нет."""

from decimal import Decimal, InvalidOperation

MAX_AMOUNT = 100_000_000_00  # сто миллионов рублей


class MoneyError(ValueError):
    pass


def validate_amount(amount: int) -> int:
    if amount <= 0:
        raise MoneyError("Сумма должна быть больше нуля")
    if amount > MAX_AMOUNT:
        raise MoneyError("Сумма слишком большая")
    return amount


def rubles_to_kopecks(value: float | int | str) -> int:
    """Рубли в копейки без плавающей точки.

    Через float 4500.50 превращалось в 450049.99999999994, и копейка терялась
    в зависимости от значения. Decimal разбирает строку как записано.
    """
    try:
        rubles = Decimal(str(value).replace(",", ".").replace(" ", "").replace(" ", ""))
    except InvalidOperation as exc:
        raise MoneyError("Не удалось разобрать сумму") from exc
    return int((rubles * 100).to_integral_value())


def format_rubles(kopecks: int) -> str:
    """«4 500 ₽» для круглых сумм и «4 500,50 ₽», когда есть копейки.

    Раньше копейки отбрасывались делением нацело: менеджер выставлял 4 500,50,
    а клиенту в счёт уходило «4 500 ₽» — оплата потом не сходилась по коду.
    """
    whole, kop = divmod(int(kopecks), 100)
    body = f"{whole:,}".replace(",", " ")
    return f"{body} ₽" if kop == 0 else f"{body},{kop:02d} ₽"
