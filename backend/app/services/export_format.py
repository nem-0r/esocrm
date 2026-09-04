"""Единый формат выгрузок: CSV, который открывается в Excel без бубна.

Правила здесь одни на все отчёты, потому что цена ошибки — файл, который
человек уже отправил бухгалтеру, и только там выяснилось, что он нечитаемый:

* **UTF-8 с BOM.** Без трёх байт `EF BB BF` Excel на Windows читает файл в
  однобайтовой кодировке и вместо «Оплачено» показывает «Ð�Ð¿Ð»Ð°Ñ‡ÐµÐ½Ð¾».
* **Разделитель `;`.** В русской локали Excel запятая — разделитель дробной
  части, а не колонок. С запятой весь ряд слипается в одну ячейку.
* **Дробная часть через запятую.** По той же причине: `4500.00` в русском
  Excel остаётся текстом, `4500,00` становится числом.
* **Экранирование формул.** Ячейка, начинающаяся с `=`, `+`, `-`, `@`, для
  Excel — формула, а не текст. Через `=HYPERLINK` или DDE это исполняемый код
  на машине того, кто открыл отчёт.
* **Имя файла по RFC 5987.** Заголовок HTTP обязан быть latin-1, поэтому
  русское имя едет в `filename*=UTF-8''`, а в `filename=` кладётся запасное
  латинское — для старых клиентов.
"""

import csv
import io
from collections.abc import Iterable
from datetime import datetime
from typing import Any
from urllib.parse import quote

#: Excel опознаёт кодировку по этим трём байтам в начале файла.
BOM = "﻿".encode()

DELIMITER = ";"
LINE_TERMINATOR = "\r\n"

#: С этих символов Excel начинает разбирать ячейку как формулу.
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r", "\n")


def csv_safe(value: str | None) -> str:
    """Текст, который Excel гарантированно покажет как текст.

    Апостроф впереди — стандартная мера против подстановки формул: значение
    остаётся видимым целиком, но перестаёт быть выражением.
    """
    if not value:
        return ""
    return "'" + value if value[0] in FORMULA_PREFIXES else value


def rub(kopecks: Any) -> str:
    """Копейки → «4500,00». Копейки не теряются, экспоненты не появляется.

    Приведение к int обязательно: сумма приходит из SQL как Decimal, а формат
    `02d` такой тип не принимает и роняет выгрузку на середине файла.
    Знак выносится вперёд: `divmod(-1, 100)` даёт (-1, 99), то есть минус одна
    копейка превратилась бы в «-1,99».
    """
    value = int(kopecks or 0)
    sign = "-" if value < 0 else ""
    whole, kop = divmod(abs(value), 100)
    return f"{sign}{whole},{kop:02d}"


def dt(value: datetime | None) -> str:
    """Дата и время без миллисекунд и без «T» — Excel разбирает это как дату."""
    return value.strftime("%Y-%m-%d %H:%M") if value else ""


def _header_safe(name: str) -> str:
    """Кавычка или перевод строки в имени файла ломают заголовок ответа."""
    return name.replace('"', "").replace("\r", "").replace("\n", "").strip()


def content_disposition(
    name: str, *, ascii_name: str | None = None, disposition: str = "attachment"
) -> str:
    """Заголовок с именем файла, который переживает кириллицу.

    `filename=` — запасное латинское имя (заголовок обязан быть latin-1),
    `filename*=UTF-8''` — настоящее, его берут все современные браузеры.
    """
    name = _header_safe(name)
    fallback = _header_safe(ascii_name or name)
    fallback = fallback.encode("ascii", "ignore").decode("ascii").strip() or "export.csv"
    return f"{disposition}; filename=\"{fallback}\"; filename*=UTF-8''{quote(name)}"


class CsvBuffer:
    """Одна строка CSV → готовый кусок ответа.

    Буфер переиспользуется и очищается после каждой строки: файл уходит
    клиенту по мере чтения из базы, а не собирается в памяти целиком.
    """

    def __init__(self) -> None:
        self._buffer = io.StringIO()
        self._writer = csv.writer(
            self._buffer, delimiter=DELIMITER, lineterminator=LINE_TERMINATOR
        )

    def row(self, values: Iterable[Any]) -> bytes:
        self._writer.writerow(list(values))
        chunk = self._buffer.getvalue()
        self._buffer.seek(0)
        self._buffer.truncate(0)
        return chunk.encode()
