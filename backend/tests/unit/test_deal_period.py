"""Проверка периода в запросах раздела «Оплаты» (замечание L2).

Раньше «с» позже «по» давало пустой список, и на экране писалось «Оплат за период нет».
Теперь сервер отвечает 422 с понятной причиной, как и статистика.
"""

from datetime import date

import pytest

from app.core.errors import Invalid
from app.services.period import MAX_DATE, MIN_DATE, check_period


def test_normal_periods_pass() -> None:
    check_period(date(2026, 10, 1), date(2026, 10, 7))
    check_period(date(2026, 10, 7), date(2026, 10, 7))  # один день
    check_period(None, None)  # период по умолчанию подставит сервис
    check_period(date(2026, 10, 1), None)
    check_period(None, date(2026, 10, 7))


def test_boundaries_are_inclusive() -> None:
    check_period(MIN_DATE, MAX_DATE)
    assert date(1900, 1, 1) == MIN_DATE
    assert date(2100, 12, 31) == MAX_DATE


def test_start_after_end_is_rejected() -> None:
    with pytest.raises(Invalid) as caught:
        check_period(date(2026, 10, 8), date(2026, 10, 7))
    assert caught.value.status_code == 422
    assert caught.value.message == "Начало периода позже конца"


@pytest.mark.parametrize(
    ("date_from", "date_to"),
    [
        (date(1899, 12, 31), None),
        (None, date(2101, 1, 1)),
        (date(1899, 12, 31), date(2026, 1, 1)),
        (date(2026, 1, 1), date(2101, 1, 1)),
        # Обе даты вне границ: «с» позже «по» не должно заслонять настоящую причину.
        (date(2101, 1, 1), date(1899, 1, 1)),
        (date(1, 1, 1), date(1, 1, 2)),
    ],
)
def test_dates_outside_1900_2100_are_rejected(date_from: date | None, date_to: date | None) -> None:
    with pytest.raises(Invalid) as caught:
        check_period(date_from, date_to)
    assert caught.value.status_code == 422
    assert caught.value.message == "Период должен лежать между 1900 и 2100 годами"
