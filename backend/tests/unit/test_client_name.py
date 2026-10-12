"""Имя клиента в выгрузках — то же, что в интерфейсе (замечание BUG-10).

Выгрузка оплат писала `display_name или telegram_id` и пропускала две ступени лестницы:
клиент без имени от менеджера, но с именем в Telegram или с одним @username, попадал в
файл числом. Лестница теперь одна на модель, список клиентов и обе выгрузки.
"""

import pytest

from app.models.client import Client, client_display_name


@pytest.mark.parametrize(
    ("display", "first", "last", "username", "expected"),
    [
        # Имя, вписанное менеджером, вытесняет всё остальное.
        ("Мария Петрова", "Masha", "P", "masha", "Мария Петрова"),
        # Нет имени от менеджера — имя и фамилия из Telegram.
        (None, "Ксения", "Орлова", "ksenia", "Ксения Орлова"),
        (None, "Ксения", None, "ksenia", "Ксения"),
        (None, None, "Орлова", None, "Орлова"),
        # Ни имени, ни фамилии — @username.
        (None, None, None, "ksenia", "@ksenia"),
        # Совсем ничего — публичный id.
        (None, None, None, None, "777000111"),
        # Пустые строки — то же, что их отсутствие.
        ("", "", "", "", "777000111"),
    ],
)
def test_ladder(
    display: str | None, first: str | None, last: str | None, username: str | None, expected: str
) -> None:
    assert client_display_name(display, first, last, username, 777000111) == expected


def test_model_property_uses_the_same_ladder() -> None:
    client = Client(
        telegram_id=777000111, tg_username="ksenia", tg_first_name=None, tg_last_name=None
    )
    assert client.name == "@ksenia"
    client.display_name = "Ксения Орлова"
    assert client.name == "Ксения Орлова"
    bare = Client(telegram_id=42)
    assert bare.name == "42"
