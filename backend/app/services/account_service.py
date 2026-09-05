"""Telegram-аккаунты: список, подключение, вход, назначение менеджеров.

Права: чтение видит любой вошедший (менеджер — только свои аккаунты через
`visible_account_ids`), запись — только руководитель, проверено `AdminUser`
в роутере. Список аккаунтов собирается пакетно: один сгруппированный запрос
на счётчики диалогов, один — на менеджеров, один пакетный вызов Redis на
онлайн-статус — вместо запроса на каждую строку.
"""

import asyncio
import contextlib
import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import command_bus, crypto, redis_bus
from app.core.config import settings
from app.core.deps import visible_account_ids
from app.core.errors import Conflict, Invalid, NotFound
from app.gateway.provider import get_provider
from app.models import (
    AccountManager,
    AccountStatus,
    Conversation,
    Notification,
    NotificationKind,
    TelegramAccount,
    User,
    UserRole,
)
from app.realtime.events import emit, emit_to_account
from app.schemas.account import (
    AccountCreate,
    AccountRow,
    AccountSummary,
    AccountUpdate,
    ConfirmCodeIn,
    ConfirmCodeOut,
    ManagersIn,
    SendCodeOut,
)
from app.services.audit import log_event

_PHONE_DIGITS_RE = re.compile(r"\D+")
DEMO_HINT = "Демо-режим: введите любой код из 5 цифр"


def _normalize_phone(raw: str) -> str:
    """К формату +7XXXXXXXXXX. Ведущая 7 или 8 — код страны, дальше 10 цифр номера."""
    digits = _PHONE_DIGITS_RE.sub("", raw or "")
    if len(digits) == 11 and digits[0] in "78":
        digits = "7" + digits[1:]
    elif len(digits) == 10:
        digits = "7" + digits
    if len(digits) != 11 or digits[0] != "7":
        raise Invalid("Некорректный номер телефона")
    return f"+{digits}"


async def _get(db: AsyncSession, account_id: int) -> TelegramAccount:
    account = await db.scalar(
        select(TelegramAccount).where(
            TelegramAccount.id == account_id, TelegramAccount.deleted_at.is_(None)
        )
    )
    if account is None:
        raise NotFound("Аккаунт не найден")
    return account


async def _check_users_exist(db: AsyncSession, user_ids: list[int]) -> None:
    if not user_ids:
        return
    found = await db.execute(
        select(User.id).where(User.id.in_(user_ids), User.deleted_at.is_(None))
    )
    if set(found.scalars().all()) != set(user_ids):
        raise Invalid("Один или несколько сотрудников не найдены")


async def _fetch_rows(
    db: AsyncSession, account_ids: list[int] | None, *, only_attention: bool = False
) -> list[dict[str, Any]]:
    """Общая сборка строк: используется и списком, и сводкой, и ответом после записи."""
    stmt = select(TelegramAccount).where(TelegramAccount.deleted_at.is_(None))
    if account_ids is not None:
        if not account_ids:
            return []
        stmt = stmt.where(TelegramAccount.id.in_(account_ids))
    accounts = list((await db.execute(stmt.order_by(TelegramAccount.id))).scalars().all())
    if not accounts:
        return []
    ids = [a.id for a in accounts]

    counts_rows = await db.execute(
        select(Conversation.account_id, func.count())
        .where(Conversation.account_id.in_(ids))
        .group_by(Conversation.account_id)
    )
    counts = dict(counts_rows.all())

    manager_rows = await db.execute(
        select(AccountManager.account_id, User.id, User.full_name, User.avatar_color)
        .join(User, User.id == AccountManager.user_id)
        .where(AccountManager.account_id.in_(ids), User.deleted_at.is_(None))
        .order_by(User.full_name)
    )
    managers_by_account: dict[int, list[tuple[int, str, str]]] = {i: [] for i in ids}
    all_manager_ids: list[int] = []
    for account_id, user_id, full_name, avatar_color in manager_rows.all():
        managers_by_account[account_id].append((user_id, full_name, avatar_color))
        all_manager_ids.append(user_id)

    # Один пакетный вызов Redis на весь список — не по одному на строку.
    online_ids = await redis_bus.online_user_ids(sorted(set(all_manager_ids)))

    rows: list[dict[str, Any]] = []
    for account in accounts:
        managers = managers_by_account.get(account.id, [])
        needs_attention = (
            account.status in (AccountStatus.ERROR, AccountStatus.PENDING) or not managers
        )
        if only_attention and not needs_attention:
            continue
        rows.append(
            {
                "id": account.id,
                "title": account.title,
                "phone": account.phone,
                "funnel_stage": account.funnel_stage,
                "status": account.status,
                "status_reason": account.status_reason,
                "tg_username": account.tg_username,
                "last_activity_at": account.last_activity_at,
                "is_active": account.is_active,
                "needs_attention": needs_attention,
                "managers": [
                    {
                        "id": uid,
                        "full_name": name,
                        "avatar_color": color,
                        "online": uid in online_ids,
                    }
                    for uid, name, color in managers
                ],
                "conversations_count": int(counts.get(account.id, 0)),
            }
        )
    return rows


async def _row(db: AsyncSession, account_id: int) -> AccountRow:
    rows = await _fetch_rows(db, [account_id])
    if not rows:
        raise NotFound("Аккаунт не найден")
    return AccountRow(**rows[0])


async def list_accounts(
    db: AsyncSession, user: User, *, only_attention: bool = False
) -> list[AccountRow]:
    ids = await visible_account_ids(db, user)
    rows = await _fetch_rows(db, ids, only_attention=only_attention)
    return [AccountRow(**r) for r in rows]


async def summary(db: AsyncSession, user: User) -> AccountSummary:
    ids = await visible_account_ids(db, user)
    rows = await _fetch_rows(db, ids)
    return AccountSummary(
        total=len(rows),
        connected=sum(1 for r in rows if r["status"] == AccountStatus.CONNECTED),
        attention=sum(1 for r in rows if r["needs_attention"]),
        conversations_total=sum(r["conversations_count"] for r in rows),
    )


async def create_account(db: AsyncSession, admin: User, data: AccountCreate) -> AccountRow:
    title = data.title.strip()
    if not title:
        raise Invalid("Укажите название аккаунта")
    phone = _normalize_phone(data.phone)

    # Номер занят только живым аккаунтом: отключённый остаётся в базе ради
    # переписки и сделок, но подключить тот же номер заново должно быть можно.
    existing = await db.scalar(
        select(TelegramAccount.id).where(
            TelegramAccount.phone == phone, TelegramAccount.deleted_at.is_(None)
        )
    )
    if existing is not None:
        raise Conflict("Аккаунт с таким номером уже подключён")

    # Ключи приложения общие для всей системы. Если руководитель их не ввёл —
    # берём из окружения. В демо-режиме реальные ключи не нужны вовсе.
    api_id = data.api_id or settings.telegram_api_id
    api_hash = data.api_hash or settings.telegram_api_hash
    if not api_id or not api_hash:
        if settings.demo_mode:
            api_id, api_hash = 1, "demo"
        else:
            raise Invalid(
                "Не заданы ключи приложения Telegram. Их выдаёт my.telegram.org, "
                "и они прописываются в настройках сервера один раз для всех аккаунтов."
            )

    account = TelegramAccount(
        title=title,
        phone=phone,
        funnel_stage=data.funnel_stage,
        api_id=api_id,
        api_hash_enc=crypto.encrypt(api_hash),
        status=AccountStatus.PENDING,
    )
    db.add(account)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise Conflict("Аккаунт с таким номером уже подключён") from exc

    await log_event(
        db,
        action="account.create",
        entity_type="account",
        entity_id=account.id,
        actor=admin,
        after={"title": title, "phone": phone, "funnel_stage": data.funnel_stage.value},
    )
    await db.commit()
    await emit_to_account(
        db,
        account.id,
        "account.status",
        {"account_id": account.id, "status": account.status.value, "reason": None},
    )
    return await _row(db, account.id)


async def _wait_for_lease(
    db: AsyncSession, account_id: int, timeout: float  # noqa: ASYNC109 — не отмена, а предел ожидания
) -> None:
    """Дождаться, пока шлюз реально возьмёт аккаунт в аренду.

    Цикл аренды подхватывает свободные аккаунты по таймеру
    (`gateway_heartbeat_seconds`), а не мгновенно при создании. Канал команд —
    Redis pub/sub, не очередь: команда, отправленная до того, как аккаунт
    достался кому-то в аренду, никем не принимается и не переспрашивается —
    просто теряется, и `command_bus.call` честно откатывается по таймауту
    только через свои полные 45 секунд. Без этого ожидания самая первая
    попытка получить код на свежесозданном аккаунте почти гарантированно
    ловит «шлюз не ответил», хотя шлюз в полном порядке — просто чуть не успел.
    """
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        worker_id = await db.scalar(
            select(TelegramAccount.worker_id).where(TelegramAccount.id == account_id)
        )
        if worker_id is not None:
            return
        await asyncio.sleep(0.3)


async def send_code(db: AsyncSession, admin: User, account_id: int) -> SendCodeOut:
    account = await _get(db, account_id)
    if settings.demo_mode:
        result = await get_provider().send_code(account)
        phone_code_hash, sent_to = result.phone_code_hash, result.sent_to
    else:
        # Вход держит процесс шлюза: код подтверждения принадлежит соединению,
        # которое его запросило, и подтвердить его должен тот же процесс.
        await _wait_for_lease(db, account.id, timeout=settings.gateway_heartbeat_seconds + 3)
        answer = await command_bus.call(account.id, "send_code")
        phone_code_hash, sent_to = answer["phone_code_hash"], answer["sent_to"]

    await log_event(
        db,
        action="account.send_code",
        entity_type="account",
        entity_id=account.id,
        actor=admin,
        after={"sent_to": sent_to},
    )
    await db.commit()

    return SendCodeOut(
        phone_code_hash=phone_code_hash,
        sent_to=sent_to,
        demo=settings.demo_mode,
        hint=DEMO_HINT if settings.demo_mode else None,
    )


async def confirm_code(
    db: AsyncSession, admin: User, account_id: int, data: ConfirmCodeIn
) -> ConfirmCodeOut:
    account = await _get(db, account_id)
    if settings.demo_mode:
        result = await get_provider().confirm_code(
            account, data.code, data.phone_code_hash, data.password
        )
        needs_password = result.needs_password
        session_string = result.session_string
        tg_user_id, tg_username = result.tg_user_id, result.tg_username
    else:
        answer = await command_bus.call(
            account.id,
            "confirm_code",
            {
                "code": data.code,
                "phone_code_hash": data.phone_code_hash,
                "password": data.password,
            },
            timeout=90,
        )
        needs_password = bool(answer.get("needs_password"))
        # Строка сессии не путешествует по Redis: шлюз зашифровал и сохранил её сам.
        session_string = None
        tg_user_id, tg_username = answer.get("tg_user_id"), answer.get("tg_username")
        if not needs_password:
            await db.refresh(account)

    if needs_password:
        await log_event(
            db,
            action="account.needs_password",
            entity_type="account",
            entity_id=account.id,
            actor=admin,
        )
        await db.commit()
        return ConfirmCodeOut(needs_password=True)

    before = {"status": account.status.value}
    if session_string:
        account.session_enc = crypto.encrypt(session_string)
    account.status = AccountStatus.CONNECTED
    account.status_reason = None
    account.tg_user_id = tg_user_id
    account.tg_username = tg_username
    account.last_activity_at = datetime.now(UTC)

    await log_event(
        db,
        action="account.connected",
        entity_type="account",
        entity_id=account.id,
        actor=admin,
        before=before,
        after={"status": account.status.value, "tg_username": account.tg_username},
    )
    await db.commit()
    await emit_to_account(
        db,
        account.id,
        "account.status",
        {"account_id": account.id, "status": account.status.value, "reason": None},
    )
    # Сразу после подключения тянем переписку: пустой список чатов у только что
    # подключённого аккаунта выглядит как поломка, а не как «ещё не загрузилось».
    if not settings.demo_mode:
        with contextlib.suppress(command_bus.GatewayUnavailable, command_bus.GatewayError):
            await command_bus.call(account.id, "sync_history", timeout=15)
    return ConfirmCodeOut(needs_password=False, account=await _row(db, account.id))


async def sync_history(db: AsyncSession, admin: User, account_id: int) -> dict[str, object]:
    """Подтянуть переписку заново. Идёт фоном на шлюзе: на большом аккаунте это минуты."""
    account = await _get(db, account_id)
    if account.status != AccountStatus.CONNECTED:
        raise Invalid("Аккаунт не подключён — сначала выполните вход")
    if settings.demo_mode:
        raise Invalid("Демо-режим: переписка уже загружена демонстрационными данными")
    answer = await command_bus.call(account.id, "sync_history", timeout=20)
    await log_event(
        db,
        action="account.history_sync_requested",
        entity_type="account",
        entity_id=account.id,
        actor=admin,
        after=answer,
    )
    await db.commit()
    return answer


async def _notify_managers_changed(
    db: AsyncSession, account: TelegramAccount, added: list[int], removed: list[int]
) -> None:
    """Уведомления о назначении/снятии не отключаются в профиле — идут всегда.

    Получают затронутых менеджеров и всех руководителей. Сервис уведомлений
    пишет другой агент параллельно, поэтому импорт отложенный: пока модуля
    нет, вставляем строки уведомлений напрямую и публикуем событие сами.
    """
    admin_rows = await db.execute(
        select(User.id).where(User.role == UserRole.ADMIN, User.is_active.is_(True))
    )
    admin_ids = sorted(admin_rows.scalars().all())

    groups: list[tuple[NotificationKind, list[int]]] = []
    if added:
        groups.append((NotificationKind.ACCOUNT_ASSIGNED, sorted(set(added) | set(admin_ids))))
    if removed:
        groups.append((NotificationKind.ACCOUNT_UNASSIGNED, sorted(set(removed) | set(admin_ids))))
    if not groups:
        return

    try:
        from app.services.notification_service import notify
    except ImportError:
        notify = None

    for kind, user_ids in groups:
        payload = {"account_id": account.id, "account_title": account.title, "kind": kind.value}
        if notify is not None:
            await notify(
                db,
                user_ids=user_ids,
                kind=kind,
                entity_type="account",
                entity_id=account.id,
                payload=payload,
            )
        else:
            for user_id in user_ids:
                db.add(
                    Notification(
                        user_id=user_id,
                        kind=kind,
                        entity_type="account",
                        entity_id=account.id,
                        payload=payload,
                    )
                )
            await emit("notification.new", {"notification": payload}, user_ids)


async def set_managers(
    db: AsyncSession, admin: User, account_id: int, data: ManagersIn
) -> AccountRow:
    account = await _get(db, account_id)
    new_ids = sorted(set(data.user_ids))
    await _check_users_exist(db, new_ids)

    current_rows = await db.execute(
        select(AccountManager.user_id).where(AccountManager.account_id == account.id)
    )
    before_ids = sorted(current_rows.scalars().all())
    added = sorted(set(new_ids) - set(before_ids))
    removed = sorted(set(before_ids) - set(new_ids))

    await db.execute(delete(AccountManager).where(AccountManager.account_id == account.id))
    for user_id in new_ids:
        db.add(AccountManager(account_id=account.id, user_id=user_id, assigned_by_id=admin.id))

    await log_event(
        db,
        action="account.managers_set",
        entity_type="account",
        entity_id=account.id,
        actor=admin,
        before={"user_ids": before_ids},
        after={"user_ids": new_ids},
    )

    # Диалоги, закреплённые за снятым с аккаунта менеджером, иначе повисают
    # невидимыми никому: сам он аккаунт больше не видит вообще, а оставшимся
    # диалог не «свой» и не «ничейный» — не подходит ни под одно условие
    # видимости (D-26). Освобождаем: дальше их разбирают из очереди, либо их
    # подхватит следующий блок, если менеджер на аккаунте остался один.
    released = 0
    if removed:
        result = await db.execute(
            update(Conversation)
            .where(
                Conversation.account_id == account.id,
                Conversation.responsible_id.in_(removed),
            )
            .values(responsible_id=None, responsible_since=None)
        )
        released = result.rowcount or 0
        if released:
            await log_event(
                db,
                action="account.conversations_released",
                entity_type="account",
                entity_id=account.id,
                actor=admin,
                before={"user_ids": removed},
                after={"conversations": released},
            )

    # На аккаунте остался ровно один менеджер — значит номер его, и все ничейные
    # диалоги закрепляются за ним. Иначе после подтяжки истории человек видел бы
    # сотни «свободных» чатов в общей очереди вместо своей переписки.
    # Чужие закреплённые диалоги не трогаем: их передаёт руководитель вручную.
    adopted = 0
    if len(new_ids) == 1:
        result = await db.execute(
            update(Conversation)
            .where(
                Conversation.account_id == account.id,
                Conversation.responsible_id.is_(None),
            )
            .values(responsible_id=new_ids[0], responsible_since=datetime.now(UTC))
        )
        adopted = result.rowcount or 0
        if adopted:
            await log_event(
                db,
                action="account.conversations_adopted",
                entity_type="account",
                entity_id=account.id,
                actor=admin,
                after={"user_id": new_ids[0], "conversations": adopted},
            )

    if data.notify and (added or removed):
        await _notify_managers_changed(db, account, added, removed)

    await db.commit()
    await emit_to_account(
        db,
        account.id,
        "account.status",
        {"account_id": account.id, "status": account.status.value, "reason": account.status_reason},
    )
    return await _row(db, account.id)


async def update_account(
    db: AsyncSession, admin: User, account_id: int, data: AccountUpdate
) -> AccountRow:
    account = await _get(db, account_id)
    before: dict[str, Any] = {}
    after: dict[str, Any] = {}

    if data.title is not None:
        title = data.title.strip()
        if not title:
            raise Invalid("Укажите название аккаунта")
        if title != account.title:
            before["title"], after["title"] = account.title, title
            account.title = title

    if data.funnel_stage is not None and data.funnel_stage != account.funnel_stage:
        before["funnel_stage"] = account.funnel_stage.value
        account.funnel_stage = data.funnel_stage
        after["funnel_stage"] = data.funnel_stage.value

    if data.is_active is not None and data.is_active != account.is_active:
        before["is_active"] = account.is_active
        account.is_active = data.is_active
        after["is_active"] = data.is_active
        if not data.is_active:
            # Иначе "отключённый" в интерфейсе аккаунт продолжает молча жить
            # в шлюзе — уже поднятая сессия не привязана к этому флагу и сама
            # не остановится. Сессию не отзываем (в отличие от полного
            # удаления) — включат обратно, и шлюз поднимет её заново сам.
            with contextlib.suppress(command_bus.GatewayUnavailable, command_bus.GatewayError):
                await command_bus.call(account.id, "disconnect", timeout=15)

    if before or after:
        await log_event(
            db,
            action="account.update",
            entity_type="account",
            entity_id=account.id,
            actor=admin,
            before=before,
            after=after,
        )
        await db.commit()
    return await _row(db, account.id)


async def delete_account(db: AsyncSession, admin: User, account_id: int) -> None:
    """Мягкое удаление: диалоги остаются и доступны для чтения.

    Сессию Telegram отзываем по-настоящему («Выход» на стороне Telegram), а не
    просто перестаём её опрашивать — иначе владелец номера продолжает видеть
    в списке устройств чужой сеанс, который никто не закрыл (см. `logout` в
    `gateway/mtproto_provider.py`). Недоступный шлюз не должен блокировать
    удаление — сессия просто останется висеть до следующего успешного выхода.
    """
    account = await _get(db, account_id)
    with contextlib.suppress(command_bus.GatewayUnavailable, command_bus.GatewayError):
        await command_bus.call(account.id, "logout", timeout=15)
    before = {"is_active": account.is_active, "status": account.status.value}
    account.deleted_at = datetime.now(UTC)
    account.is_active = False
    account.status = AccountStatus.DISCONNECTED
    account.worker_id = None
    account.lease_until = None

    await log_event(
        db,
        action="account.delete",
        entity_type="account",
        entity_id=account.id,
        actor=admin,
        before=before,
        after={"is_active": False, "status": AccountStatus.DISCONNECTED.value},
    )
    await db.commit()
    await emit_to_account(
        db,
        account.id,
        "account.status",
        {"account_id": account.id, "status": AccountStatus.DISCONNECTED.value, "reason": None},
    )
