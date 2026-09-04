"""Управление сотрудниками. Все функции вызываются только для руководителя —
проверка роли выполнена зависимостью `AdminUser` в роутере.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, distinct, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import crypto, redis_bus
from app.core.errors import Conflict, Invalid, NotFound
from app.models import (
    AccountManager,
    AuthSession,
    Conversation,
    Deal,
    DealStatus,
    Direction,
    Message,
    TelegramAccount,
    User,
)
from app.schemas.common import CursorPage, decode_cursor, encode_cursor
from app.schemas.user import (
    ResendInviteOut,
    StaffAccountOut,
    UserCreateIn,
    UserCreateOut,
    UserOut,
    UserUpdateIn,
    WorkScheduleOut,
)
from app.services import audit, schedule, settings_service
from app.services.auth_service import normalize_email

INVITE_TTL_DAYS = 7


def _month_bounds() -> tuple[datetime, datetime]:
    now = datetime.now(UTC)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if start.month == 12:
        end = start.replace(year=start.year + 1, month=1)
    else:
        end = start.replace(month=start.month + 1)
    return start, end


async def _get_or_404(db: AsyncSession, user_id: int) -> User:
    user = await db.scalar(select(User).where(User.id == user_id, User.deleted_at.is_(None)))
    if user is None:
        raise NotFound("Сотрудник не найден")
    return user


async def _check_accounts_exist(db: AsyncSession, account_ids: list[int]) -> None:
    if not account_ids:
        return
    found = await db.scalars(
        select(TelegramAccount.id).where(
            TelegramAccount.id.in_(account_ids), TelegramAccount.deleted_at.is_(None)
        )
    )
    if set(found.all()) != set(account_ids):
        raise Invalid("Один или несколько аккаунтов не найдены")


async def _load_accounts(db: AsyncSession, user_ids: list[int]) -> dict[int, list[StaffAccountOut]]:
    if not user_ids:
        return {}
    cols = (
        AccountManager.user_id,
        TelegramAccount.id,
        TelegramAccount.title,
        TelegramAccount.funnel_stage,
    )
    rows = await db.execute(
        select(*cols)
        .join(TelegramAccount, TelegramAccount.id == AccountManager.account_id)
        .where(AccountManager.user_id.in_(user_ids), TelegramAccount.deleted_at.is_(None))
    )
    result: dict[int, list[StaffAccountOut]] = {uid: [] for uid in user_ids}
    for user_id, acc_id, title, stage in rows.all():
        result[user_id].append(StaffAccountOut(id=acc_id, title=title, funnel_stage=stage))
    return result


async def _load_month_stats(
    db: AsyncSession, user_ids: list[int]
) -> tuple[dict[int, int], dict[int, int]]:
    """Диалоги и продажи за месяц: один сгруппированный запрос на метрику для всего списка."""
    if not user_ids:
        return {}, {}
    start, end = _month_bounds()

    conv_rows = await db.execute(
        select(Message.author_id, func.count(distinct(Message.conversation_id)))
        .join(Conversation, Conversation.id == Message.conversation_id)
        .where(
            Message.author_id.in_(user_ids),
            Message.direction == Direction.OUT,
            Conversation.responsible_id == Message.author_id,
            Message.created_at >= start,
            Message.created_at < end,
        )
        .group_by(Message.author_id)
    )
    conversations = dict(conv_rows.all())

    sales_rows = await db.execute(
        select(Deal.sold_by_id, func.coalesce(func.sum(Deal.total_amount), 0))
        .where(
            Deal.sold_by_id.in_(user_ids),
            Deal.status == DealStatus.PAID,
            Deal.paid_at >= start,
            Deal.paid_at < end,
        )
        .group_by(Deal.sold_by_id)
    )
    sales = {uid: int(amount) for uid, amount in sales_rows.all()}

    return conversations, sales


def schedule_out(user: User, tz_name: str | None) -> WorkScheduleOut:
    """График сотрудника для ответа API. `on_shift` считаем здесь, а не на клиенте:
    браузер менеджера может стоять в другом поясе, а смена — в поясе организации."""
    days = list(user.work_days or [])
    return WorkScheduleOut(
        enabled=user.schedule_enabled,
        days=days,
        start=user.work_start.strftime("%H:%M") if user.work_start else None,
        end=user.work_end.strftime("%H:%M") if user.work_end else None,
        summary=schedule.describe(days, user.work_start, user.work_end),
        on_shift=user.schedule_enabled
        and schedule.is_on_shift(days, user.work_start, user.work_end, tz_name),
    )


async def _build_rows(
    db: AsyncSession, users: list[User], online_ids: set[int] | None = None
) -> list[UserOut]:
    ids = [u.id for u in users]
    tz_name = await settings_service.get_value(db, "timezone")
    if online_ids is None:
        online_ids = await redis_bus.online_user_ids(ids)
    accounts = await _load_accounts(db, ids)
    conversations, sales = await _load_month_stats(db, ids)
    return [
        UserOut(
            id=u.id,
            full_name=u.full_name,
            email=u.email,
            phone=u.phone,
            role=u.role,
            is_active=u.is_active,
            avatar_color=u.avatar_color,
            online=u.id in online_ids,
            last_seen_at=u.last_seen_at,
            invite_pending=u.invite_pending,
            accounts=accounts.get(u.id, []),
            month_conversations=conversations.get(u.id, 0),
            month_sales_amount=sales.get(u.id, 0),
            schedule=schedule_out(u, tz_name),
        )
        for u in users
    ]


async def list_users(
    db: AsyncSession,
    *,
    q: str | None,
    status: str | None,
    no_account: bool,
    cursor: str | None,
    limit: int,
) -> CursorPage[UserOut]:
    stmt = select(User).where(User.deleted_at.is_(None))
    if q:
        pattern = f"%{q.strip()}%"
        stmt = stmt.where(or_(User.full_name.ilike(pattern), User.email.ilike(pattern)))
    if no_account:
        has_account = (
            select(AccountManager.user_id).where(AccountManager.user_id == User.id).exists()
        )
        stmt = stmt.where(~has_account)
    stmt = stmt.order_by(User.id)

    matched = list((await db.execute(stmt)).scalars().all())

    # Один пакетный запрос в Redis на весь отфильтрованный список — не по одному на строку.
    online_ids = await redis_bus.online_user_ids([u.id for u in matched])
    if status in ("online", "offline"):
        want_online = status == "online"
        matched = [u for u in matched if (u.id in online_ids) == want_online]

    total = len(matched)
    after_id = decode_cursor(cursor)
    if after_id is not None:
        matched = [u for u in matched if u.id > after_id]

    page = matched[:limit]
    rows = await _build_rows(db, page, online_ids=online_ids)
    next_cursor = encode_cursor(page[-1].id) if len(matched) > limit else None

    return CursorPage[UserOut](items=rows, next_cursor=next_cursor, total=total)


async def get_user(db: AsyncSession, user_id: int) -> UserOut:
    user = await _get_or_404(db, user_id)
    return (await _build_rows(db, [user]))[0]


async def create_user(db: AsyncSession, admin: User, data: UserCreateIn) -> UserCreateOut:
    normalized = normalize_email(data.email)
    existing = await db.scalar(
        select(User.id).where(User.email == normalized, User.deleted_at.is_(None))
    )
    if existing is not None:
        raise Conflict("Сотрудник с такой почтой уже заведён")

    account_ids = sorted(set(data.account_ids or []))
    await _check_accounts_exist(db, account_ids)

    raw_token = crypto.new_token()
    now = datetime.now(UTC)
    user = User(
        full_name=data.full_name,
        email=normalized,
        phone=data.phone,
        role=data.role,
        invite_token_hash=crypto.token_hash(raw_token),
        invite_expires_at=now + timedelta(days=INVITE_TTL_DAYS),
        invite_sent_at=now,
        created_by_id=admin.id,
    )
    db.add(user)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise Conflict("Сотрудник с такой почтой уже заведён") from exc

    for account_id in account_ids:
        db.add(AccountManager(account_id=account_id, user_id=user.id, assigned_by_id=admin.id))

    await audit.log_event(
        db, action="user.invited", entity_type="user", entity_id=user.id, actor=admin,
        after={"email": normalized, "role": data.role.value, "account_ids": account_ids},
    )
    await db.commit()

    row = (await _build_rows(db, [user]))[0]
    return UserCreateOut(**row.model_dump(), invite_url=f"/invite/{raw_token}")


async def update_user(db: AsyncSession, admin: User, user_id: int, data: UserUpdateIn) -> UserOut:
    user = await _get_or_404(db, user_id)
    demoting_self = user.id == admin.id and data.role is not None and data.role != user.role
    if user.id == admin.id and (data.is_active is False or demoting_self):
        raise Invalid("Нельзя отключить собственную учётную запись")

    before: dict[str, object] = {}
    after: dict[str, object] = {}
    deactivated = False

    if data.full_name is not None and data.full_name != user.full_name:
        before["full_name"], user.full_name = user.full_name, data.full_name
        after["full_name"] = user.full_name

    if data.phone is not None and data.phone != user.phone:
        before["phone"], user.phone = user.phone, data.phone
        after["phone"] = user.phone

    if data.role is not None and data.role != user.role:
        before["role"] = user.role.value
        user.role = data.role
        after["role"] = user.role.value

    if data.is_active is not None and data.is_active != user.is_active:
        before["is_active"] = user.is_active
        user.is_active = data.is_active
        after["is_active"] = user.is_active
        deactivated = not data.is_active

    if data.schedule is not None:
        want = data.schedule
        start = schedule.parse_time(want.start) if want.start else None
        end = schedule.parse_time(want.end) if want.end else None
        days = want.days or None
        current = (user.schedule_enabled, user.work_days or None, user.work_start, user.work_end)
        if current != (want.enabled, days, start, end):
            before["schedule"] = {
                "enabled": user.schedule_enabled,
                "days": user.work_days,
                "start": user.work_start.strftime("%H:%M") if user.work_start else None,
                "end": user.work_end.strftime("%H:%M") if user.work_end else None,
            }
            user.schedule_enabled = want.enabled
            user.work_days = days
            user.work_start = start
            user.work_end = end
            after["schedule"] = {
                "enabled": want.enabled,
                "days": days,
                "start": want.start,
                "end": want.end,
            }

    if data.account_ids is not None:
        account_ids = sorted(set(data.account_ids))
        await _check_accounts_exist(db, account_ids)
        await db.execute(delete(AccountManager).where(AccountManager.user_id == user.id))
        for account_id in account_ids:
            db.add(AccountManager(account_id=account_id, user_id=user.id, assigned_by_id=admin.id))
        after["account_ids"] = account_ids

    if not before and not after:
        return (await _build_rows(db, [user]))[0]

    await audit.log_event(
        db, action="user.updated", entity_type="user", entity_id=user.id,
        actor=admin, before=before or None, after=after or None,
    )

    if deactivated:
        await db.execute(
            update(AuthSession)
            .where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None))
            .values(revoked_at=datetime.now(UTC))
        )
        await audit.log_event(
            db, action="user.deactivated", entity_type="user", entity_id=user.id, actor=admin,
        )

    await db.commit()
    return (await _build_rows(db, [user]))[0]


async def resend_invite(db: AsyncSession, admin: User, user_id: int) -> ResendInviteOut:
    user = await _get_or_404(db, user_id)
    if user.accepted_at is not None:
        raise Invalid("Приглашение уже принято, повторная отправка не нужна")

    raw_token = crypto.new_token()
    now = datetime.now(UTC)
    user.invite_token_hash = crypto.token_hash(raw_token)
    user.invite_expires_at = now + timedelta(days=INVITE_TTL_DAYS)
    user.invite_sent_at = now

    await audit.log_event(
        db, action="user.invited", entity_type="user", entity_id=user.id,
        actor=admin, after={"resent": True},
    )
    await db.commit()
    return ResendInviteOut(invite_url=f"/invite/{raw_token}")
