"""Проверка вводимых данных (пункт L1 замечаний тестировщика, 11.10.2026).

Телефон, дата рождения и текстовые поля карточки клиента, телефон сотрудника.
Сервер — последняя линия: что не отклонила форма, не должно попасть в базу
(`++--1234567`, год 2999, имя из одних пробелов, строка длиннее колонки).
"""

from datetime import date, timedelta

import pytest
from pydantic import ValidationError

from app.schemas.auth import MeUpdateIn
from app.schemas.client import ClientUpdate
from app.schemas.user import UserCreateIn, UserUpdateIn
from app.schemas.validators import (
    BIRTH_DATE_MIN,
    NAME_MAX_LEN,
    PHONE_MAX_LEN,
    check_birth_date,
    clean_text,
    normalize_phone,
)

TODAY = date(2026, 10, 11)


# ------------------------------------------------------------------ телефон


@pytest.mark.parametrize(
    "raw",
    [
        "+7 916 200-10-01",
        "+79162001001",
        "8 916 200 10 01",
        "916-200-10-01",
        "1234567",  # ровно 7 цифр — нижняя граница
        "123456789012345",  # 15 цифр — верхняя
        "+380 44 123 45 67",
    ],
)
def test_normal_phones_pass_unchanged(raw: str) -> None:
    assert normalize_phone(raw) == raw


def test_repeated_separators_are_collapsed() -> None:
    assert normalize_phone("  +7   916  200--10-01  ") == "+7 916 200-10-01"
    assert normalize_phone("8 916\t200 10 01") == "8 916 200 10 01"  # nbsp, табуляция


@pytest.mark.parametrize(
    "raw",
    [
        "abc",
        "++--1234567",  # пример из замечаний
        "+-1234567",
        "--1234567",
        "++79162001001",
        "+7 916 +200 10 01",  # плюс не в начале
        "1234567-",  # висячий дефис
        " - 1234567",
        "+",
        "-",
        "123456",  # 6 цифр — мало
        "1234567890123456",  # 16 цифр — много
        "+7 (916) 200-10-01",  # скобки: осознанно не принимаем, подсказка у поля это говорит
        "7916200100a",
        "٧٩١٦٢٠٠١٠٠١",  # арабо-индийские цифры: [0-9] только ASCII
    ],
)
def test_bad_phones_are_rejected(raw: str) -> None:
    with pytest.raises(ValueError):
        normalize_phone(raw)


def test_phone_longer_than_column_is_rejected_with_its_own_message() -> None:
    # 15 цифр, но с разделителями длиннее колонки String(32)
    raw = "1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7"
    assert len(raw) > PHONE_MAX_LEN
    with pytest.raises(ValueError, match="не длиннее 32"):
        normalize_phone(raw)


def test_phone_error_text_is_in_russian_and_actionable() -> None:
    with pytest.raises(ValueError) as caught:
        normalize_phone("++--1234567")
    text = str(caught.value)
    assert "телефон" in text.lower()
    assert "7" in text and "15" in text  # границы количества цифр названы


# --------------------------------------------------------------- дата рождения


def test_birth_date_bounds() -> None:
    assert check_birth_date(BIRTH_DATE_MIN, today=TODAY) == date(1900, 1, 1)
    assert check_birth_date(TODAY, today=TODAY) == TODAY
    with pytest.raises(ValueError, match="1900"):
        check_birth_date(date(1899, 12, 31), today=TODAY)
    with pytest.raises(ValueError, match="будущем"):
        check_birth_date(date(2999, 1, 1), today=TODAY)


def test_birth_date_tolerates_timezone_difference_but_not_more() -> None:
    # Менеджер на UTC+12 уже живёт в завтрашнем дне по UTC: «сегодня» у него
    # на сутки впереди. Сервер на этом не режет, но послезавтра — уже нельзя.
    assert check_birth_date(TODAY + timedelta(days=1), today=TODAY) == TODAY + timedelta(days=1)
    with pytest.raises(ValueError):
        check_birth_date(TODAY + timedelta(days=2), today=TODAY)


def test_birth_date_strict_mode_for_org_timezone() -> None:
    # Вторая проверка в сервисе идёт по часовому поясу организации без запаса.
    with pytest.raises(ValueError):
        check_birth_date(TODAY + timedelta(days=1), today=TODAY, slack_days=0)
    assert check_birth_date(TODAY, today=TODAY, slack_days=0) == TODAY


def test_client_update_rejects_year_2999_and_ancient_dates() -> None:
    with pytest.raises(ValidationError) as caught:
        ClientUpdate(birth_date="2999-01-01")
    assert "будущем" in str(caught.value)
    with pytest.raises(ValidationError):
        ClientUpdate(birth_date="1066-10-14")


def test_client_update_unparseable_date_has_russian_message() -> None:
    for raw in ("2000-13-45", "вчера", "0000-00-00", "31.12.1990"):
        with pytest.raises(ValidationError) as caught:
            ClientUpdate(birth_date=raw)
        assert "Дата рождения" in str(caught.value), raw


def test_client_update_accepts_valid_and_empty_birth_date() -> None:
    assert ClientUpdate(birth_date="1990-05-17").birth_date == date(1990, 5, 17)
    assert ClientUpdate(birth_date=None).birth_date is None
    assert ClientUpdate(birth_date="").birth_date is None  # поле очищено в форме
    # «Не передали» и «очистили» по-прежнему различимы
    assert "birth_date" not in ClientUpdate().model_fields_set
    assert "birth_date" in ClientUpdate(birth_date=None).model_fields_set


# ------------------------------------------------------------ текстовые поля


def test_clean_text_trims_and_nulls_blanks_but_keeps_the_middle() -> None:
    assert clean_text("  Иван   Петров  ", max_len=NAME_MAX_LEN, label="Имя") == "Иван   Петров"
    assert clean_text("   ", max_len=NAME_MAX_LEN, label="Имя") is None
    assert clean_text("", max_len=NAME_MAX_LEN, label="Имя") is None
    assert clean_text("\n\t ", max_len=NAME_MAX_LEN, label="Имя") is None
    assert clean_text(None, max_len=NAME_MAX_LEN, label="Имя") is None


def test_clean_text_length_limit_counts_after_trimming() -> None:
    assert clean_text("я" * NAME_MAX_LEN, max_len=NAME_MAX_LEN, label="Имя")
    with pytest.raises(ValueError, match=f"не длиннее {NAME_MAX_LEN}"):
        clean_text("я" * (NAME_MAX_LEN + 1), max_len=NAME_MAX_LEN, label="Имя")
    # Пробелы по краям длину не раздувают
    padded = "  " + "я" * NAME_MAX_LEN + "  "
    assert clean_text(padded, max_len=NAME_MAX_LEN, label="Имя") == "я" * NAME_MAX_LEN


def test_clean_text_rejects_control_characters() -> None:
    for raw in ("Иван\x00Петров", "Иван\x1bПетров", "a\x07b"):
        with pytest.raises(ValueError, match="недопустимые символы"):
            clean_text(raw, max_len=NAME_MAX_LEN, label="Имя")


def test_clean_text_does_not_rewrite_newlines() -> None:
    # verify_exports кладёт в имя перенос строки и ждёт его в выгрузке нетронутым:
    # экранирует его выгрузка, а не проверка ввода.
    value = '=HYPERLINK("http://evil";"клик");Ч"К\nвторая строка'
    assert clean_text(value, max_len=NAME_MAX_LEN, label="Имя") == value


def test_clean_text_leaves_markup_alone() -> None:
    # Экранирует интерфейс (React), а не сервер: «<script>» — просто имя.
    assert clean_text("<script>alert(1)</script>", max_len=50, label="Имя") == (
        "<script>alert(1)</script>"
    )


def test_client_update_text_fields() -> None:
    patch = ClientUpdate(display_name="  Анна  ", birth_city="  ", source="")
    assert patch.display_name == "Анна"
    assert patch.birth_city is None
    assert patch.source is None
    # Пустое имя не выпадает из model_fields_set: сервер должен его очистить
    assert {"display_name", "birth_city", "source"} <= patch.model_fields_set
    assert ClientUpdate(display_name="   ").display_name is None


def test_client_update_name_and_city_limits() -> None:
    with pytest.raises(ValidationError) as caught:
        ClientUpdate(display_name="я" * (NAME_MAX_LEN + 1))
    assert "Имя" in str(caught.value)
    with pytest.raises(ValidationError) as caught:
        ClientUpdate(birth_city="г" * 121)
    assert "Город" in str(caught.value)
    with pytest.raises(ValidationError):
        ClientUpdate(source="s" * 121)  # колонка String(120)


def test_client_update_phone() -> None:
    assert ClientUpdate(phone="  +7  916 200-10-01 ").phone == "+7 916 200-10-01"
    assert ClientUpdate(phone="").phone is None
    assert ClientUpdate(phone="   ").phone is None
    for bad in ("++--1234567", "abc", "123"):
        with pytest.raises(ValidationError):
            ClientUpdate(phone=bad)


# ----------------------------------------------------------- телефон сотрудника


def test_staff_schemas_share_the_phone_rule() -> None:
    assert MeUpdateIn(phone="+7  916 200-10-01").phone == "+7 916 200-10-01"
    assert UserUpdateIn(phone="8 916 200 10 01").phone == "8 916 200 10 01"
    created = UserCreateIn(full_name="Анна", email="a@b.ru", role="manager", phone=" 1234567 ")
    assert created.phone == "1234567"

    for model, extra in (
        (MeUpdateIn, {}),
        (UserUpdateIn, {}),
        (UserCreateIn, {"full_name": "Анна", "email": "a@b.ru", "role": "manager"}),
    ):
        for bad in ("++--1234567", "abc", "12", "9" * 40):
            with pytest.raises(ValidationError):
                model(phone=bad, **extra)


def test_staff_phone_is_optional_and_can_be_cleared() -> None:
    assert MeUpdateIn().phone is None
    assert "phone" not in MeUpdateIn().model_fields_set
    cleared = MeUpdateIn(phone="  ")
    assert cleared.phone is None
    assert "phone" in cleared.model_fields_set  # «очистили», а не «не передали»
    assert UserUpdateIn(phone="").phone is None
    assert UserCreateIn(full_name="Анна", email="a@b.ru", role="manager").phone is None
