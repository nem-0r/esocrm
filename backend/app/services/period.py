"""Проверка периода «с … по …» в запросах.

Граница 1900–2100 — здравый смысл: год 0001 или 9999 в запросе — опечатка, а в расчётах
границ дней он ещё и падал с «date value out of range» (500). Те же пределы стоят в
статистике (`api/v1/stats.py`) и в форме периода на экране (`shared/lib/period.ts`).
"""

from datetime import date

from app.core.errors import Invalid

MIN_DATE = date(1900, 1, 1)
MAX_DATE = date(2100, 12, 31)


def check_period(date_from: date | None, date_to: date | None) -> None:
    """422 с понятной причиной, если период нельзя читать как период.

    Пустая сторона допустима: недостающую подставляет сервис (по умолчанию — год назад
    и сегодня). Проверяются только заданные даты: «с» позже «по» и выход за границы.
    """
    for value in (date_from, date_to):
        if value is not None and not MIN_DATE <= value <= MAX_DATE:
            raise Invalid("Период должен лежать между 1900 и 2100 годами")
    if date_from is not None and date_to is not None and date_from > date_to:
        raise Invalid("Начало периода позже конца")
