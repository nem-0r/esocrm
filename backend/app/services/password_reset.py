"""Сброс пароля через руководителя (D-38): правила ссылки без обращения к базе.

Почты в системе служебные, писем нет, поэтому «забыл пароль» решает человек:
руководитель выдаёт одноразовую ссылку и передаёт её лично (как приглашение).
Здесь — то, что можно проверить без базы: как выпускается токен, когда он
действует, кому его вообще можно выдать. Запись в базу и вход — в `user_service`
(выдача) и `auth_service` (использование).

Токен — 32 случайных байта; в базе лежит только SHA-256 от него, как у
приглашений. Ссылка живёт 24 часа, срабатывает один раз, новая выдача гасит
прежнюю.
"""

import hmac
from datetime import datetime, timedelta

from app.core import crypto
from app.core.errors import Invalid
from app.models import User

RESET_TTL_HOURS = 24

# Неверный токен — не повод для подбора: после стольких неудач с одного адреса
# за окно ссылки перестают проверяться. Токен 256-битный, перебор нереален, но
# молотить публичную ручку без предела тоже незачем.
THROTTLE_LIMIT = 20
THROTTLE_WINDOW_MINUTES = 15


def reset_path(raw_token: str) -> str:
    """Относительная ссылка `/reset#<токен>`: полный адрес интерфейс собирает по
    адресу сайта, на котором открыт (как у приглашений).

    Токен — во фрагменте после «#»: браузер не отправляет его на сервер, поэтому
    он не попадает ни в журналы nginx, ни в заголовок Referer. Страница читает
    его из адреса, сразу убирает оттуда и проверяет запросом с токеном в теле.
    Ссылки прежнего вида `/reset/<токен>` интерфейс тоже понимает.
    """
    return f"/reset#{raw_token}"


def throttle_key(ip: str | None) -> str:
    """Ключ счётчика неудач в `login_attempts.email`. Без «@», поэтому с почтой
    настоящего сотрудника не пересекается (почта без «@» не проходит проверку)."""
    return f"reset:{ip or 'unknown'}"


def issue_token(user: User, now: datetime) -> str:
    """Выпустить токен и записать его хеш; прежний токен этим перестаёт работать."""
    raw = crypto.new_token()
    user.password_reset_token_hash = crypto.token_hash(raw)
    user.password_reset_expires_at = now + timedelta(hours=RESET_TTL_HOURS)
    user.password_reset_issued_at = now
    return raw


def clear_token(user: User) -> None:
    """Погасить действующую ссылку. `issued_at` остаётся как след «сброс выдавали»."""
    user.password_reset_token_hash = None
    user.password_reset_expires_at = None


def token_matches(user: User, raw_token: str, now: datetime) -> bool:
    """Подходит ли токен сотруднику сейчас: есть ссылка, не истекла, хеш совпал.

    Хеши сравниваются за постоянное время — по времени ответа нельзя угадать,
    сколько первых символов хеша совпало.
    """
    stored = user.password_reset_token_hash
    expires = user.password_reset_expires_at
    if not stored or expires is None or not raw_token.strip():
        return False
    same = hmac.compare_digest(stored, crypto.token_hash(raw_token))
    return same and expires > now


def check_can_issue(user: User, *, actor_id: int | None) -> None:
    """Кому можно выдать ссылку: активному сотруднику, который уже принял приглашение.

    `actor_id=None` — команда на сервере (`python -m app.cli reset-password`):
    там можно и руководителю, забывшему собственный пароль.
    """
    if actor_id is not None and user.id == actor_id:
        raise Invalid("Свой пароль меняется в разделе «Профиль»", code="reset_self")
    if not user.is_active:
        raise Invalid(
            "Сотрудник отключён — сначала включите его, затем выдайте ссылку для нового пароля",
            code="reset_inactive",
        )
    if user.invite_pending:
        raise Invalid(
            "Сотрудник ещё не принял приглашение — отправьте ему приглашение повторно",
            code="reset_invite_pending",
        )
