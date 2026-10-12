"""Общие проверки вводимых данных: телефон, дата рождения, короткий текст.

Одно место на клиентов и сотрудников (замечания тестировщика 11.10.2026, L1):
телефон клиента и телефон сотрудника обязаны проверяться одинаково, иначе
в одной карточке `++--123` отклоняется, а в другой сохраняется.

Функции бросают `ValueError` с готовым русским текстом. Pydantic превращает его
в 422, а общий обработчик (`core/errors.py`) отдаёт текст и имя поля интерфейсу —
он показывает ошибку под тем полем, где она случилась.
"""

import re
from datetime import UTC, date, datetime, timedelta

# Длина колонок: clients.phone / users.phone — String(32), clients.birth_city —
# String(255), clients.source — String(120). Для имени и города держим меньше
# колонки: настоящее имя или город длиннее 120 символов не бывает, а длинная
# строка — это вставленный по ошибке абзац.
PHONE_MAX_LEN = 32
PHONE_MIN_DIGITS = 7
PHONE_MAX_DIGITS = 15
NAME_MAX_LEN = 120
CITY_MAX_LEN = 120
SOURCE_MAX_LEN = 120

# Раньше этой даты клиент родиться не мог. Нижняя граница отсекает опечатки
# вроде 0199 или 1066 вместо 1966.
BIRTH_DATE_MIN = date(1900, 1, 1)

PHONE_MESSAGE = (
    "Проверьте формат телефона: цифры, плюс только в начале, пробелы и дефисы; "
    f"от {PHONE_MIN_DIGITS} до {PHONE_MAX_DIGITS} цифр"
)

# Плюс — только первым и только один; начинается и заканчивается цифрой, внутри
# цифры, пробелы и дефисы. Скобки не принимаем осознанно: подсказка у поля
# говорит «цифры, плюс, пробелы и дефисы» (решение прошлых версий не менялось).
_PHONE_FORMAT = re.compile(r"\+?[0-9](?:[0-9 \-]*[0-9])?")
# Управляющие символы, кроме перевода строки и табуляции: их в имени не бывает, а
# перенос строки сервер не трогает — выгрузка в CSV экранирует его сама (verify_exports).
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")

# Запас в сутки на разницу часовых поясов: менеджер на UTC+12 уже живёт в
# «завтрашнем» дне по UTC. Точную проверку по поясу организации повторяет сервис.
_TZ_SLACK_DAYS = 1


def normalize_phone(raw: str) -> str:
    """Привести телефон к аккуратному виду или объяснить, что с ним не так.

    Повторные пробелы и дефисы схлопываются, края обрезаются. `++--1234567`
    не чинится, а отклоняется: что именно хотел написать человек, не угадать.
    """
    text = re.sub(r"-{2,}", "-", re.sub(r"\s+", " ", raw.strip()))
    if len(text) > PHONE_MAX_LEN:
        raise ValueError(f"Телефон — не длиннее {PHONE_MAX_LEN} символов")
    if not _PHONE_FORMAT.fullmatch(text):
        raise ValueError(PHONE_MESSAGE)
    digits = sum(ch.isdigit() for ch in text)
    if not PHONE_MIN_DIGITS <= digits <= PHONE_MAX_DIGITS:
        raise ValueError(PHONE_MESSAGE)
    return text


def optional_phone(value: str | None) -> str | None:
    """Необязательный телефон: пустое и из одних пробелов — значит «нет телефона»."""
    if value is None or not value.strip():
        return None
    return normalize_phone(value)


def clean_text(value: str | None, *, max_len: int, label: str) -> str | None:
    """Короткий текст: пробелы по краям срезаны, длина ограничена.

    Пустое и «из одних пробелов» становится None — иначе в базу попадает имя
    из пробелов, которое на экране выглядит как пустое место. Середину строки
    не переписываем: введённое человеком сохраняется как есть.
    """
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    if _CONTROL.search(text):
        raise ValueError("В тексте есть недопустимые символы")
    if len(text) > max_len:
        raise ValueError(f"{label} — не длиннее {max_len} символов")
    return text


def parse_birth_date(value: object) -> date | None:
    """Дата рождения из тела запроса: ISO-строка, дата или пусто."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return date.fromisoformat(text)
        except ValueError:
            pass
    raise ValueError("Дата рождения указана неверно — нужен формат ГГГГ-ММ-ДД")


def check_birth_date(
    value: date, *, today: date | None = None, slack_days: int = _TZ_SLACK_DAYS
) -> date:
    """Дата рождения — от 1 января 1900 года и не позже сегодняшнего дня."""
    if value < BIRTH_DATE_MIN:
        raise ValueError(f"Дата рождения не может быть раньше {BIRTH_DATE_MIN.year} года")
    base = today or datetime.now(UTC).date()
    if value > base + timedelta(days=slack_days):
        raise ValueError("Дата рождения не может быть в будущем")
    return value
