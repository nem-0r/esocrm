"""Аутентификация: вход, приглашения, сессии, присутствие.

Хеширование пароля (bcrypt) уносится в пул потоков: в асинхронном приложении
оно блокирует цикл событий, и один вход подвешивает весь сервер. Модуля
`app.core.security` в репозитории нет, поэтому хеширование и нормализация
почты реализованы прямо здесь, а не вынесены в core — это единственное
отступление от буквы задания, продиктованное отсутствием файла.
"""

import asyncio
import contextlib
from datetime import UTC, datetime, timedelta

import bcrypt
from fastapi import Request
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import crypto, redis_bus
from app.core.config import settings
from app.core.deps import visible_account_ids
from app.core.errors import AppError, Forbidden, Invalid, NotFound, Unauthorized
from app.models import (
    AccountManager,
    AccountStatus,
    AuthSession,
    LoginAttempt,
    TelegramAccount,
    User,
)
from app.schemas.auth import ResetPasswordPreviewOut
from app.schemas.user import PASSWORD_MAX_BYTES, PW_TOO_LONG_MESSAGE, MeOut
from app.services import audit, password_reset


def normalize_email(email: str) -> str:
    """Логин регистронезависим — сравнение только по нормализованному виду."""
    return email.strip().lower()


async def hash_password(password: str) -> str:
    # Схемы уже не пропускают пароль длиннее 72 байт; это вторая линия на случай
    # пути в обход схемы — bcrypt на таком пароле падает, и вышла бы ошибка 500.
    if len(password.encode("utf-8")) > PASSWORD_MAX_BYTES:
        raise Invalid(PW_TOO_LONG_MESSAGE, code="password_too_long", field="password")
    return await asyncio.to_thread(_hash_password_sync, password)


async def verify_password(password: str, password_hash: str) -> bool:
    return await asyncio.to_thread(_verify_password_sync, password, password_hash)


def _hash_password_sync(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def _verify_password_sync(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _record_attempt(
    db: AsyncSession, email: str, request: Request, *, succeeded: bool, reason: str | None = None
) -> None:
    db.add(
        LoginAttempt(
            email=email, ip_address=_client_ip(request), succeeded=succeeded, reason=reason
        )
    )


def _new_session(db: AsyncSession, user: User, request: Request) -> tuple[str, AuthSession]:
    raw_token = crypto.new_token()
    session = AuthSession(
        user_id=user.id,
        token_hash=crypto.token_hash(raw_token),
        user_agent=(request.headers.get("user-agent") or "")[:500] or None,
        ip_address=_client_ip(request),
        expires_at=datetime.now(UTC) + timedelta(days=settings.session_ttl_days),
    )
    db.add(session)
    return raw_token, session


async def _is_locked_out(db: AsyncSession, email: str) -> bool:
    since = datetime.now(UTC) - timedelta(minutes=settings.login_lockout_minutes)
    count = await db.scalar(
        select(func.count())
        .select_from(LoginAttempt)
        .where(
            LoginAttempt.email == email,
            LoginAttempt.succeeded.is_(False),
            LoginAttempt.created_at >= since,
        )
    )
    return (count or 0) >= settings.login_max_attempts


async def _authenticate(db: AsyncSession, request: Request, email: str, password: str) -> User:
    normalized = normalize_email(email)

    if await _is_locked_out(db, normalized):
        _record_attempt(db, normalized, request, succeeded=False, reason="locked_out")
        await db.commit()
        raise Forbidden(
            "Слишком много неудачных попыток. Попробуйте через 30 минут "
            "или попросите руководителя выдать ссылку для нового пароля"
        )

    user = await db.scalar(
        select(User).where(User.email == normalized, User.deleted_at.is_(None))
    )
    if user is None or not user.password_hash:
        password_ok = False
    else:
        password_ok = await verify_password(password, user.password_hash)

    if not password_ok:
        _record_attempt(db, normalized, request, succeeded=False, reason="bad_credentials")
        await audit.log_event(
            db, action="user.login_failed", entity_type="user",
            entity_id=user.id if user else None, actor=user,
            after={"email": normalized}, ip=_client_ip(request),
        )
        await db.commit()
        raise Unauthorized("Неверная почта или пароль")

    if not user.is_active:
        _record_attempt(db, normalized, request, succeeded=False, reason="inactive")
        await audit.log_event(
            db, action="user.login_failed", entity_type="user", entity_id=user.id,
            actor=user, after={"reason": "inactive"}, ip=_client_ip(request),
        )
        await db.commit()
        raise Unauthorized("Учётная запись отключена")

    _record_attempt(db, normalized, request, succeeded=True)
    return user


async def login(db: AsyncSession, request: Request, email: str, password: str) -> tuple[User, str]:
    user = await _authenticate(db, request, email, password)
    # Вошёл по паролю — значит, его помнит: выданная ссылка сброса больше не нужна
    # и не должна остаться рабочей (как и после смены пароля в профиле).
    password_reset.clear_token(user)
    raw_token, _session = _new_session(db, user, request)
    await audit.log_event(
        db, action="user.logged_in", entity_type="user", entity_id=user.id,
        actor=user, ip=_client_ip(request),
    )
    await db.commit()
    return user, raw_token


async def logout(db: AsyncSession, request: Request, user: User, token: str) -> None:
    session = await db.scalar(
        select(AuthSession).where(
            AuthSession.token_hash == crypto.token_hash(token), AuthSession.revoked_at.is_(None)
        )
    )
    if session is None:
        return
    session.revoked_at = datetime.now(UTC)
    await audit.log_event(
        db, action="user.logged_out", entity_type="user", entity_id=user.id,
        actor=user, ip=_client_ip(request),
    )
    await db.commit()


async def accept_invite(
    db: AsyncSession, request: Request, token: str, password: str
) -> tuple[User, str]:
    if not token:
        raise Invalid("Приглашение недействительно или истекло")

    user = await db.scalar(
        select(User).where(
            User.invite_token_hash == crypto.token_hash(token), User.deleted_at.is_(None)
        )
    )
    now = datetime.now(UTC)
    if user is None or user.invite_expires_at is None or user.invite_expires_at < now:
        raise Invalid("Приглашение недействительно или истекло")
    # Ссылка ещё не протухла, но сотрудника уже отключили — тот же признак,
    # что закрывает обычный вход (см. _authenticate). Токен не трогаем: если
    # руководитель включит его обратно, эта же ссылка должна снова заработать,
    # а не требовать повторной отправки приглашения.
    if not user.is_active:
        raise Invalid("Учётная запись отключена — обратитесь к руководителю")

    user.password_hash = await hash_password(password)
    user.accepted_at = now
    user.invite_token_hash = None
    user.invite_expires_at = None

    await audit.log_event(
        db, action="user.invite_accepted", entity_type="user", entity_id=user.id,
        actor=user, after={"accepted_at": now.isoformat()}, ip=_client_ip(request),
    )
    raw_token, _session = _new_session(db, user, request)
    await audit.log_event(
        db, action="user.logged_in", entity_type="user", entity_id=user.id,
        actor=user, ip=_client_ip(request),
    )
    await db.commit()
    return user, raw_token


# ------------------------------------------------------------ сброс пароля (D-38)

RESET_LINK_INVALID = "Ссылка недействительна или истекла. Попросите руководителя выдать новую"


async def _check_reset_throttle(db: AsyncSession, key: str) -> None:
    """Слишком много неверных ссылок с одного адреса — публичная ручка закрывается
    на окно. Счётчик в базе (`login_attempts`), как у входа: Redis не нужен,
    его потеря ничего не отключает."""
    since = datetime.now(UTC) - timedelta(minutes=password_reset.THROTTLE_WINDOW_MINUTES)
    failures = await db.scalar(
        select(func.count())
        .select_from(LoginAttempt)
        .where(
            LoginAttempt.email == key,
            LoginAttempt.succeeded.is_(False),
            LoginAttempt.created_at >= since,
        )
    )
    if (failures or 0) >= password_reset.THROTTLE_LIMIT:
        raise AppError(
            429,
            "rate_limited",
            "Слишком много неверных попыток. Попробуйте через "
            f"{password_reset.THROTTLE_WINDOW_MINUTES} минут",
        )


async def _user_by_reset_token(
    db: AsyncSession, request: Request, token: str, *, lock: bool = False
) -> User:
    """Сотрудник, которому выдана эта ссылка. Любая причина отказа — неизвестный токен,
    истёкший, погашенный, отключённый сотрудник — даёт один и тот же нейтральный
    ответ: по нему нельзя узнать, существует ли такая ссылка."""
    ip = _client_ip(request)
    key = password_reset.throttle_key(ip)
    await _check_reset_throttle(db, key)

    now = datetime.now(UTC)
    user: User | None = None
    if token.strip():
        stmt = select(User).where(
            User.password_reset_token_hash == crypto.token_hash(token),
            User.deleted_at.is_(None),
        )
        if lock:
            # Две вкладки с одной ссылкой: вторая дождётся первой, перечитает строку
            # уже без токена и получит отказ. Одноразовость держится на этой блокировке.
            stmt = stmt.with_for_update()
        user = await db.scalar(stmt)

    if user is None or not user.is_active or not password_reset.token_matches(user, token, now):
        db.add(
            LoginAttempt(email=key, ip_address=ip, succeeded=False, reason="reset_bad_token")
        )
        await db.commit()
        raise NotFound(RESET_LINK_INVALID, code="reset_link_invalid")
    return user


async def reset_password_preview(
    db: AsyncSession, request: Request, token: str
) -> ResetPasswordPreviewOut:
    """Страница нового пароля спрашивает, жива ли ссылка, и показывает имя в приветствии."""
    user = await _user_by_reset_token(db, request, token)
    assert user.password_reset_expires_at is not None  # token_matches без срока не пускает
    return ResetPasswordPreviewOut(
        full_name=user.full_name, expires_at=user.password_reset_expires_at
    )


async def complete_password_reset(
    db: AsyncSession, request: Request, token: str, password: str
) -> tuple[User, str]:
    """Задать новый пароль по ссылке и сразу войти.

    Ссылка гасится в той же транзакции, что и смена пароля. Все прежние сессии
    сотрудника отзываются, блокировка входа снимается (человек, которого заперли
    пятью неверными паролями, этой ссылкой возвращается в работу). `accepted_at`
    не трогаем: «с какого момента работает» — это дата первого входа.
    """
    user = await _user_by_reset_token(db, request, token, lock=True)
    now = datetime.now(UTC)

    user.password_hash = await hash_password(password)
    password_reset.clear_token(user)

    await db.execute(
        update(AuthSession)
        .where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    await db.execute(
        delete(LoginAttempt).where(
            LoginAttempt.email == user.email, LoginAttempt.succeeded.is_(False)
        )
    )

    await audit.log_event(
        db, action="user.password_reset_done", entity_type="user", entity_id=user.id,
        actor=user, ip=_client_ip(request),
    )
    raw_token, _session = _new_session(db, user, request)
    await audit.log_event(
        db, action="user.logged_in", entity_type="user", entity_id=user.id,
        actor=user, ip=_client_ip(request),
    )
    await db.commit()
    return user, raw_token


async def update_me(
    db: AsyncSession,
    request: Request,
    user: User,
    current_token: str,
    *,
    accepting_leads: bool | None,
    full_name: str | None,
    phone: str | None,
    current_password: str | None,
    new_password: str | None,
    phone_provided: bool = False,
) -> User:
    before: dict[str, object] = {}
    after: dict[str, object] = {}

    if accepting_leads is not None and accepting_leads != user.accepting_leads:
        before["accepting_leads"] = user.accepting_leads
        user.accepting_leads = accepting_leads
        after["accepting_leads"] = accepting_leads

    if full_name is not None and full_name != user.full_name:
        before["full_name"], user.full_name = user.full_name, full_name
        after["full_name"] = user.full_name

    # «Передали пустой» (после проверки это None) очищает телефон; «не передали»
    # его не трогает. Раньше пустое значение молча игнорировалось.
    if (phone_provided or phone is not None) and phone != user.phone:
        before["phone"], user.phone = user.phone, phone
        after["phone"] = user.phone

    password_changed = False
    if new_password:
        if not current_password:
            raise Invalid("Укажите текущий пароль, чтобы задать новый")
        if not user.password_hash:
            raise Invalid("Неверный текущий пароль")
        if not await verify_password(current_password, user.password_hash):
            raise Invalid("Неверный текущий пароль")
        user.password_hash = await hash_password(new_password)
        password_changed = True
        # Человек вспомнил пароль сам: выданная ему ссылка сброса больше не нужна
        # и не должна остаться рабочей.
        password_reset.clear_token(user)

    if not before and not after and not password_changed:
        return user

    if before or after:
        await audit.log_event(
            db, action="user.updated", entity_type="user", entity_id=user.id,
            actor=user, before=before or None, after=after or None, ip=_client_ip(request),
        )

    if password_changed:
        await db.execute(
            update(AuthSession)
            .where(
                AuthSession.user_id == user.id,
                AuthSession.revoked_at.is_(None),
                AuthSession.token_hash != crypto.token_hash(current_token),
            )
            .values(revoked_at=datetime.now(UTC))
        )
        await audit.log_event(
            db, action="user.password_changed", entity_type="user", entity_id=user.id,
            actor=user, ip=_client_ip(request),
        )

    await db.commit()
    return user


async def heartbeat(db: AsyncSession, user: User) -> None:
    """Онлайн-статус живёт в Redis; last_seen_at в базе — только «был в сети» офлайн.

    Потеря Redis не должна ничего ломать — heartbeat в этом случае просто
    обновит last_seen_at и молча продолжит работу.
    """
    user.last_seen_at = datetime.now(UTC)
    await db.commit()
    with contextlib.suppress(Exception):
        await redis_bus.touch_presence(user.id)


async def build_me(db: AsyncSession, user: User) -> MeOut:
    account_ids = await visible_account_ids(db, user)

    # ТЗ Б.2 — диалоги, продажи и среднее время ответа за текущий месяц.
    # Считаем той же функцией, что и список сотрудников: две цифры об одном
    # человеке на разных экранах обязаны совпадать.
    from app.services import settings_service, user_service
    from app.services.stats_service import personal_avg_response_seconds
    from app.services.user_service import _load_month_stats

    conversations, sales = await _load_month_stats(db, [user.id])
    avg_response = await personal_avg_response_seconds(db, user.id)

    # ТЗ Б.5 — «требуют внимания» показываем и менеджеру, по его аккаунтам.
    attention_stmt = select(func.count()).select_from(TelegramAccount).where(
        TelegramAccount.deleted_at.is_(None),
        TelegramAccount.is_active.is_(True),
        or_(
            TelegramAccount.status.in_([AccountStatus.ERROR, AccountStatus.PENDING]),
            ~select(AccountManager.user_id)
            .where(AccountManager.account_id == TelegramAccount.id)
            .exists(),
        ),
    )
    if account_ids is not None:
        attention_stmt = attention_stmt.where(TelegramAccount.id.in_(account_ids or [-1]))
    attention = (await db.execute(attention_stmt)).scalar_one()

    return MeOut(
        id=user.id,
        full_name=user.full_name,
        email=user.email,
        phone=user.phone,
        role=user.role,
        is_active=user.is_active,
        accepting_leads=user.accepting_leads,
        avatar_color=user.avatar_color,
        last_seen_at=user.last_seen_at,
        is_admin=user.is_admin,
        account_ids=account_ids or [],
        sees_all_accounts=account_ids is None,
        works_since=user.accepted_at or user.created_at,
        month_conversations=conversations.get(user.id, 0),
        month_sales_amount=sales.get(user.id, 0),
        avg_response_seconds=avg_response,
        accounts_attention=int(attention),
        # ТЗ Б.3: сотрудник видит свой график и то, идёт ли смена прямо сейчас.
        demo_mode=settings.demo_mode,
        robokassa_enabled=settings.robokassa_enabled,
        schedule=user_service.schedule_out(
            user, await settings_service.get_value(db, "timezone")
        ),
    )
