"""Телефон сотрудника: мягкая проверка (ревью волны L1, 12.10.2026).

До L1 телефон сотрудника не проверялся вовсе, и в базе могут лежать номера
вроде `+7 (900) 123-45-67`. Прежняя сборка интерфейса присылает сохранённый
телефон при каждом сохранении карточки — строгая проверка ответила бы 422 на
правку графика или роли. Поэтому у сотрудника скобки и точки приводятся к
принятому виду, а не отклоняются. У клиента правило прежнее: скобки — отказ.
"""

import pytest
from pydantic import ValidationError

from app.schemas.auth import MeUpdateIn
from app.schemas.client import ClientUpdate
from app.schemas.user import UserCreateIn, UserUpdateIn
from app.schemas.validators import optional_staff_phone

NEW_MEMBER = {"full_name": "Анна", "email": "a@b.ru", "role": "manager"}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("+7 (900) 123-45-67", "+7 900 123-45-67"),
        ("+7(900)123-45-67", "+7 900 123-45-67"),
        ("8 (900) 123 45 67", "8 900 123 45 67"),
        ("8.900.123.45.67", "8 900 123 45 67"),
        ("(495) 123-45-67", "495 123-45-67"),
        # То, что принималось и раньше, не меняется
        ("+7 916 200-10-01", "+7 916 200-10-01"),
        ("  +7   916  200--10-01  ", "+7 916 200-10-01"),
    ],
)
def test_staff_phone_brackets_and_dots_are_normalized(raw: str, expected: str) -> None:
    assert optional_staff_phone(raw) == expected
    assert MeUpdateIn(phone=raw).phone == expected
    assert UserUpdateIn(phone=raw).phone == expected
    assert UserCreateIn(phone=raw, **NEW_MEMBER).phone == expected


@pytest.mark.parametrize(
    "raw", ["++--1234567", "abc", "12", "9" * 40, "+7 (900) 123-45-6a", "()", "(12)"]
)
def test_staff_phone_garbage_is_still_rejected(raw: str) -> None:
    with pytest.raises(ValueError):
        optional_staff_phone(raw)
    for model, extra in ((MeUpdateIn, {}), (UserUpdateIn, {}), (UserCreateIn, NEW_MEMBER)):
        with pytest.raises(ValidationError):
            model(phone=raw, **extra)


def test_staff_phone_empty_still_means_no_phone() -> None:
    assert optional_staff_phone(None) is None
    assert optional_staff_phone("   ") is None
    cleared = MeUpdateIn(phone="")
    assert cleared.phone is None
    assert "phone" in cleared.model_fields_set


def test_client_phone_keeps_the_strict_rule() -> None:
    # Решение для клиентов не меняется: подсказка у поля говорит «цифры, плюс,
    # пробелы и дефисы», скобки — отказ.
    with pytest.raises(ValidationError):
        ClientUpdate(phone="+7 (900) 123-45-67")
    with pytest.raises(ValidationError):
        ClientUpdate(phone="8.900.123.45.67")
