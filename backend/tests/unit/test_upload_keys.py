"""Ключ загрузки принимается только в том виде, в каком его выдала CRM.

Проверка по началу строки («uploads/…») пропускала «uploads/../incoming/…» —
чужой файл, если хранилище схлопнет «..» в пути.
"""

from app.services.file_service import is_upload_key

UUID = "0b9a3f0e-8a1b-4c2d-9e3f-1a2b3c4d5e6f"


def test_keys_issued_by_upload_pass() -> None:
    assert is_upload_key(f"uploads/2026/09/{UUID}.jpg")
    assert is_upload_key(f"voice/2026/09/{UUID}.ogg")


def test_prefix_can_be_narrowed() -> None:
    assert not is_upload_key(f"voice/2026/09/{UUID}.ogg", prefixes=("uploads",))
    assert is_upload_key(f"uploads/2026/09/{UUID}.pdf", prefixes=("uploads",))


def test_forged_and_foreign_keys_are_rejected() -> None:
    for key in (
        "uploads/../incoming/2026/01/foreign.jpg",
        f"uploads/2026/09/../../incoming/{UUID}.jpg",
        f"uploads/2026/09/{UUID}.jpg/../x.jpg",
        f"incoming/2026/09/{UUID}.jpg",
        "derived/m4a/12.m4a",
        f"/uploads/2026/09/{UUID}.jpg",
        f"uploads/2026/09/{UUID}",
        "",
        None,
        42,
    ):
        assert not is_upload_key(key), key
