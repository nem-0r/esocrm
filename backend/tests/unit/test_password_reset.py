"""Сброс пароля через руководителя (D-38): логика токена без базы.

Ссылка живёт 24 часа, работает один раз, новая выдача гасит прежнюю, токен в
базе хранится только хешем. Поток целиком (выдача → пароль → вход) проверяет
`tests/verify_auth_reset.py` на живом стенде.
"""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.core import crypto
from app.core.errors import Invalid
from app.models import User, UserRole
from app.schemas.auth import ResetPasswordIn
from app.services import password_reset as pr

NOW = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)


def make_user(**fields) -> User:
    base = {
        "id": 7,
        "full_name": "Мария Тестова",
        "email": "maria@example.test",
        "role": UserRole.MANAGER,
        "is_active": True,
        "accepted_at": NOW - timedelta(days=30),
        "password_hash": "x",
    }
    return User(**{**base, **fields})


# ------------------------------------------------------------------ выдача


def test_issue_stores_only_the_hash_and_a_24h_expiry() -> None:
    user = make_user()
    raw = pr.issue_token(user, NOW)

    assert user.password_reset_token_hash == crypto.token_hash(raw)
    assert raw not in (user.password_reset_token_hash or "")
    assert user.password_reset_expires_at == NOW + timedelta(hours=24)
    assert user.password_reset_issued_at == NOW
    assert len(raw) >= 43  # 32 случайных байта


def test_ttl_is_24_hours() -> None:
    assert timedelta(hours=pr.RESET_TTL_HOURS) == timedelta(hours=24)


def test_tokens_are_unique() -> None:
    user = make_user()
    assert len({pr.issue_token(user, NOW) for _ in range(50)}) == 50


def test_reset_link_keeps_the_token_in_the_fragment() -> None:
    # Всё после «#» браузер не отправляет на сервер: токен не попадает ни в
    # журналы nginx, ни в заголовок Referer. Путь относительный, как у приглашений.
    link = pr.reset_path("abc_DEF-123")
    assert link == "/reset#abc_DEF-123"
    path, _, fragment = link.partition("#")
    assert path == "/reset"
    assert fragment == "abc_DEF-123"


def test_cli_prints_a_full_link_in_the_new_format() -> None:
    from app import cli

    assert cli.full_link("https://esocrm.com", "/reset#tok") == "https://esocrm.com/reset#tok"
    # Без адреса сайта — относительная ссылка, её дополнит человек
    assert cli.full_link(None, "/reset#tok") == "/reset#tok"
    assert cli._site_origin("https://esocrm.com/") == "https://esocrm.com"


# ------------------------------------------------------------------ проверка


def test_fresh_token_is_valid() -> None:
    user = make_user()
    raw = pr.issue_token(user, NOW)
    assert pr.token_matches(user, raw, NOW + timedelta(hours=1))
    assert pr.token_matches(user, raw, NOW + timedelta(hours=23, minutes=59))


def test_token_expires_after_24_hours() -> None:
    user = make_user()
    raw = pr.issue_token(user, NOW)
    assert not pr.token_matches(user, raw, NOW + timedelta(hours=24))  # граница — уже поздно
    assert not pr.token_matches(user, raw, NOW + timedelta(days=3))


def test_wrong_foreign_and_empty_tokens_are_rejected() -> None:
    user = make_user()
    other = make_user(id=8)
    raw = pr.issue_token(user, NOW)
    foreign = pr.issue_token(other, NOW)

    assert not pr.token_matches(user, foreign, NOW)  # токен чужого сотрудника
    assert not pr.token_matches(user, raw[:-1] + ("A" if raw[-1] != "A" else "B"), NOW)
    assert not pr.token_matches(user, raw.upper(), NOW)
    assert not pr.token_matches(user, "", NOW)
    assert not pr.token_matches(user, " ", NOW)


def test_user_without_issued_token_matches_nothing() -> None:
    user = make_user()
    assert not pr.token_matches(user, "anything", NOW)
    assert not pr.token_matches(user, "", NOW)
    # Хеш пуст, срок есть (или наоборот) — тоже не ссылка
    user.password_reset_expires_at = NOW + timedelta(hours=1)
    assert not pr.token_matches(user, "anything", NOW)
    half = make_user(password_reset_token_hash=crypto.token_hash("t"))
    assert not pr.token_matches(half, "t", NOW)  # без срока ссылка недействительна


def test_invite_token_is_not_a_reset_token() -> None:
    # Колонки разные: приглашение не открывает сброс и наоборот.
    user = make_user(
        invite_token_hash=crypto.token_hash("invite-token"),
        invite_expires_at=NOW + timedelta(days=7),
    )
    assert not pr.token_matches(user, "invite-token", NOW)


# --------------------------------------------------- одноразовость и замена


def test_token_is_single_use() -> None:
    user = make_user()
    raw = pr.issue_token(user, NOW)
    assert pr.token_matches(user, raw, NOW)

    pr.clear_token(user)

    assert user.password_reset_token_hash is None
    assert user.password_reset_expires_at is None
    assert not pr.token_matches(user, raw, NOW)
    # След «сброс выдавали» остаётся: это история, а не действующая ссылка
    assert user.password_reset_issued_at == NOW


def test_new_issue_invalidates_the_previous_link() -> None:
    user = make_user()
    first = pr.issue_token(user, NOW)
    later = NOW + timedelta(hours=2)
    second = pr.issue_token(user, later)

    assert first != second
    assert not pr.token_matches(user, first, later)
    assert pr.token_matches(user, second, later)
    # Срок считается от новой выдачи, а не от первой
    assert user.password_reset_expires_at == later + timedelta(hours=24)
    assert user.password_reset_issued_at == later


def test_clear_is_safe_when_nothing_was_issued() -> None:
    user = make_user()
    pr.clear_token(user)
    assert user.password_reset_token_hash is None


# ------------------------------------------------------------ кому можно выдать


def test_active_accepted_employee_can_get_a_reset() -> None:
    pr.check_can_issue(make_user(), actor_id=1)  # не бросает


def test_admin_cannot_reset_own_password_here() -> None:
    with pytest.raises(Invalid) as caught:
        pr.check_can_issue(make_user(id=1), actor_id=1)
    assert "Профиль" in caught.value.message


def test_cli_may_reset_anyone_including_the_admin_who_forgot_password() -> None:
    pr.check_can_issue(make_user(role=UserRole.ADMIN), actor_id=None)


def test_pending_invite_gets_a_hint_to_resend_the_invite() -> None:
    pending = make_user(
        accepted_at=None,
        password_hash=None,
        invite_token_hash=crypto.token_hash("t"),
        invite_expires_at=NOW + timedelta(days=7),
    )
    with pytest.raises(Invalid) as caught:
        pr.check_can_issue(pending, actor_id=1)
    assert "повторно" in caught.value.message
    assert "приглашени" in caught.value.message


def test_deactivated_employee_cannot_get_a_reset() -> None:
    with pytest.raises(Invalid) as caught:
        pr.check_can_issue(make_user(is_active=False), actor_id=1)
    assert "отключ" in caught.value.message


# ------------------------------------------------------------------- пароль


def test_new_password_follows_the_same_policy_as_invites() -> None:
    good = {"token": "t", "password": "abcdefgh"}
    short = {"token": "t", "password": "short"}
    assert ResetPasswordIn(**good).password == good["password"]
    with pytest.raises(ValidationError) as caught:
        ResetPasswordIn(**short)
    assert "не короче 8" in str(caught.value)


# Длинная русская парольная фраза: 47 символов, но 88 байт в UTF-8. bcrypt
# принимает не больше 72 байт и на большем падал ошибкой 500.
LONG_RU = "Очень длинная парольная фраза для проверки сети"


def test_long_password_is_measured_in_utf8_bytes() -> None:
    assert len(LONG_RU) < 72 < len(LONG_RU.encode("utf-8"))


def test_password_longer_than_72_bytes_is_rejected_everywhere() -> None:
    from app.schemas.auth import AcceptInviteIn, MeUpdateIn
    from app.schemas.user import validate_password_strength

    for model, fields in (
        (ResetPasswordIn, {"token": "t", "password": LONG_RU}),
        (AcceptInviteIn, {"token": "t", "password": LONG_RU}),
        (MeUpdateIn, {"current_password": "old-pass-1", "new_password": LONG_RU}),
    ):
        with pytest.raises(ValidationError) as caught:
            model(**fields)
        assert "Пароль слишком длинный" in str(caught.value), model.__name__
    with pytest.raises(ValueError, match="слишком длинный"):
        validate_password_strength("a" * 73)


def test_password_up_to_72_bytes_is_accepted() -> None:
    from app.schemas.user import validate_password_strength

    assert validate_password_strength("a" * 72) == "a" * 72
    assert validate_password_strength("я" * 36) == "я" * 36  # 36 букв = 72 байта
    with pytest.raises(ValueError):
        validate_password_strength("я" * 37)  # 74 байта


async def test_hashing_a_too_long_password_is_a_clear_422_not_a_crash() -> None:
    # Вторая линия: если длинный пароль дойдёт до хеширования в обход схемы
    # (команда на сервере, будущий код), ответ — понятный отказ, а не 500.
    from app.services.auth_service import hash_password

    with pytest.raises(Invalid) as caught:
        await hash_password(LONG_RU)
    assert "слишком длинный" in caught.value.message
    assert (await hash_password("я" * 36)).startswith("$2")


def test_link_check_takes_the_token_in_the_body() -> None:
    from app.schemas.auth import ResetPasswordCheckIn

    body = {"token": "abc"}
    assert ResetPasswordCheckIn(**body).token == body["token"]
    with pytest.raises(ValidationError):
        ResetPasswordCheckIn()  # type: ignore[call-arg]


# ----------------------------------------------------------- ограничение частоты


def test_failure_throttle_key_is_per_address_and_never_a_real_login() -> None:
    key = pr.throttle_key("203.0.113.5")
    assert key != pr.throttle_key("203.0.113.6")
    assert "@" not in key  # почта-логин содержит «@» — счётчики не пересекаются
    assert pr.throttle_key(None) == pr.throttle_key(None)
    assert pr.THROTTLE_LIMIT >= 5
    assert pr.THROTTLE_WINDOW_MINUTES >= 5
