"""Предел суммы: сервер и интерфейс согласованы, ошибка говорит человеческим языком.

Раньше форма принимала до 10 млрд ₽, а сервер отказывал уже после 100 млн ₽ со словами
«Сумма слишком большая» — менеджер видел отказ там, где форма молчала (замечание L3).
"""

import pytest

from app.services.money import MAX_AMOUNT, MoneyError, format_rubles, validate_amount


def test_limit_is_one_hundred_million_rubles() -> None:
    # То же число закреплено на стороне интерфейса: frontend/src/shared/lib/format.test.ts
    # («равен серверному»), константа MAX_AMOUNT_KOPECKS. Меняете одно — меняйте и другое.
    assert MAX_AMOUNT == 10_000_000_000
    # Разряды разделены неразрывным пробелом: сумма не переносится по строке.
    assert format_rubles(MAX_AMOUNT) == "100\u00a0000\u00a0000 ₽"


def test_amount_on_the_limit_is_accepted() -> None:
    assert validate_amount(MAX_AMOUNT) == MAX_AMOUNT
    assert validate_amount(1) == 1


def test_one_kopeck_over_the_limit_is_rejected_with_the_limit_in_the_text() -> None:
    with pytest.raises(MoneyError) as caught:
        validate_amount(MAX_AMOUNT + 1)
    assert str(caught.value) == "Сумма не больше 100\u00a0000\u00a0000 ₽"


@pytest.mark.parametrize("amount", [0, -1, -MAX_AMOUNT])
def test_zero_and_negative_are_rejected(amount: int) -> None:
    with pytest.raises(MoneyError) as caught:
        validate_amount(amount)
    assert str(caught.value) == "Сумма должна быть больше нуля"
