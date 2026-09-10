"""Что шлюз умеет делать по просьбе API и что он делает с полученным из Telegram.

Здесь сходятся две стороны: команды сверху (`command_bus`) и события снизу
(провайдер). Обе кончаются записью в PostgreSQL — Redis используется только
как провод, ничего важного в нём не задерживается.

Отдельное правило про секреты: строка сессии, полученная при входе, **не уходит
обратно в API**. Шлюз шифрует её и кладёт в базу сам, а наверх отдаёт только имя
и идентификатор аккаунта. Так полный доступ к чужому Telegram не путешествует
по Redis и не оседает в логах.
"""

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import select

from app.core import crypto
from app.core.db import SessionLocal
from app.models import AccountStatus, ActorKind, Client, TelegramAccount
from app.models.client import zodiac_for
from app.realtime.events import emit_to_account
from app.services import inbound_service, settings_service
from app.services.audit import log_event

log = logging.getLogger("astra.gateway.commands")

# Сколько диалогов подтягиваем за один заход синхронизации истории.
HISTORY_BATCH_COMMIT = 200


async def _account(db, account_id: int) -> TelegramAccount:
    account = await db.get(TelegramAccount, account_id)
    if account is None or account.deleted_at is not None:
        raise ValueError("Аккаунт не найден")
    return account


# ------------------------------------------------------------------- приём


async def write_inbound(account_id: int, event: inbound_service.InboundMessage) -> None:
    """Сообщение из Telegram → база. Вызывается провайдером на каждое событие."""
    async with SessionLocal() as db:
        try:
            account = await _account(db, account_id)
            on_new = _birthday_backfill_callback(account_id, event.peer.tg_user_id)
            await inbound_service.ingest(db, account, event, on_new_client=on_new)
            await db.commit()
        except Exception:
            await db.rollback()
            log.exception("Не записал входящее сообщение аккаунта %s", account_id)


def _birthday_backfill_callback(account_id: int, tg_user_id: int) -> Callable[[Client], None]:
    """Клиент завёлся впервые — в отдельной задаче, не задерживая приём
    сообщения, спрашиваем у Telegram дату рождения. Тихая осечка (приватность,
    провайдер не умеет, любая ошибка) никак не отражается на переписке."""

    def schedule(client: Client) -> None:
        asyncio.create_task(_backfill_birthday(account_id, tg_user_id, client.id))

    return schedule


async def _backfill_birthday(account_id: int, tg_user_id: int, client_id: int) -> None:
    from app.gateway.provider import get_provider

    try:
        async with SessionLocal() as db:
            account = await _account(db, account_id)
        # Запрос к Telegram может занять секунды (сеть, флуд-контроль) — базу
        # на это время не держим открытой, иначе под нагрузкой быстро съедим
        # пул соединений. Заодно к моменту записи транзакция, создавшая
        # клиента, уже точно закоммичена — сеть всегда медленнее одного commit.
        birthday = await get_provider().fetch_birthday(account, tg_user_id)
        if birthday is None:
            return
        day, month, year = birthday
        # Без года дата рождения была бы выдуманной — лучше пусто, чем
        # неверно. Не трогаем, если менеджер уже успел вписать её вручную.
        if year is None:
            return
        async with SessionLocal() as db:
            client = await db.get(Client, client_id)
            if client is not None and client.birth_date is None:
                client.birth_date = date(year, month, day)
                client.zodiac_sign = zodiac_for(client.birth_date)
                await db.commit()
    except Exception:
        log.exception("Не подтянул дату рождения из Telegram для клиента %s", client_id)


async def write_read_receipt(account_id: int, chat_id: int, max_id: int) -> None:
    async with SessionLocal() as db:
        try:
            account = await _account(db, account_id)
            changed = await inbound_service.mark_outgoing_read(db, account, chat_id, max_id)
            if changed:
                await db.commit()
        except Exception:
            await db.rollback()
            log.exception("Не отметил прочтение в аккаунте %s", account_id)


async def write_status(account_id: int, status: str, reason: str | None) -> None:
    """Сессия отвалилась — состояние аккаунта должно измениться в интерфейсе сразу,
    а не тогда, когда менеджер не сможет отправить сообщение."""
    async with SessionLocal() as db:
        try:
            account = await _account(db, account_id)
            account.status = AccountStatus(status)
            account.status_reason = reason
            await log_event(
                db,
                action="account.status_changed",
                entity_type="account",
                entity_id=account.id,
                actor_kind=ActorKind.GATEWAY,
                after={"status": status, "reason": reason},
            )
            await db.commit()
            await emit_to_account(
                db,
                account.id,
                "account.status",
                {"account_id": account.id, "status": status, "reason": reason},
            )
        except Exception:
            await db.rollback()
            log.exception("Не сохранил состояние аккаунта %s", account_id)


async def write_qr_session(account_id: int, result: Any) -> None:
    """Вход по QR состоялся — сохранить сессию ровно так же, как после кода.

    Вызывается провайдером сразу по факту сканирования, а не по запросу
    интерфейса: человек может закрыть окно, не дождавшись ответа, а сеанс в
    Telegram уже выдан — не сохранить его значит оставить висящий чужой вход.
    """
    async with SessionLocal() as db:
        try:
            account = await _account(db, account_id)
            before = {"status": account.status.value}
            # Секрет дальше шлюза не идёт: шифруем и сохраняем здесь же.
            if result.session_string:
                account.session_enc = crypto.encrypt(result.session_string)
            account.tg_user_id = result.tg_user_id
            account.tg_username = result.tg_username
            account.status = AccountStatus.CONNECTED
            account.status_reason = None
            account.last_activity_at = datetime.now(UTC)
            await log_event(
                db,
                action="account.connected",
                entity_type="account",
                entity_id=account.id,
                actor_kind=ActorKind.GATEWAY,
                before=before,
                after={
                    "status": AccountStatus.CONNECTED.value,
                    "tg_username": result.tg_username,
                    "via": "qr",
                },
            )
            await db.commit()
            await emit_to_account(
                db,
                account.id,
                "account.status",
                {
                    "account_id": account.id,
                    "status": AccountStatus.CONNECTED.value,
                    "reason": None,
                },
            )
            # Пустой список чатов у только что подключённого аккаунта выглядит
            # как поломка, а не как «ещё не загрузилось».
            since = await _history_since(db, None)
        except Exception:
            await db.rollback()
            log.exception("Не сохранил сессию входа по QR для аккаунта %s", account_id)
            return

    task = asyncio.create_task(_sync_history(account_id, since))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


# ----------------------------------------------------------------- команды


def _mtproto(provider: Any) -> Any:
    """Команды входа по QR умеет только боевой провайдер — демо его не изображает."""
    from app.gateway import mtproto_provider

    if not isinstance(provider, mtproto_provider.MTProtoProvider):
        raise ValueError("Вход по QR доступен только при подключённом Telegram")
    return provider


async def handle(account_id: int, command: str, args: dict[str, Any]) -> dict[str, Any]:
    """Единая точка разбора команд. Ошибка возвращается наверх текстом и попадает
    руководителю на экран — поэтому тексты человеческие."""
    from app.gateway.provider import get_provider

    provider = get_provider()
    async with SessionLocal() as db:
        account = await _account(db, account_id)

        if command == "send_code":
            result = await provider.send_code(account)
            return {"phone_code_hash": result.phone_code_hash, "sent_to": result.sent_to}

        if command == "confirm_code":
            result = await provider.confirm_code(
                account, args.get("code", ""), args.get("phone_code_hash", ""), args.get("password")
            )
            if result.needs_password:
                return {"needs_password": True}
            # Секрет дальше шлюза не идёт: шифруем и сохраняем здесь же.
            if result.session_string:
                account.session_enc = crypto.encrypt(result.session_string)
            account.tg_user_id = result.tg_user_id
            account.tg_username = result.tg_username
            account.status = AccountStatus.CONNECTED
            account.status_reason = None
            account.last_activity_at = datetime.now(UTC)
            await db.commit()
            return {
                "needs_password": False,
                "tg_user_id": result.tg_user_id,
                "tg_username": result.tg_username,
            }

        if command == "qr_start":
            code = await _mtproto(provider).qr_start(account)
            return {"image": code.image, "expires_at": code.expires_at.isoformat()}

        if command == "qr_state":
            state = _mtproto(provider).qr_state(account)
            return {
                "status": state.status,
                "image": state.image,
                "expires_at": state.expires_at.isoformat() if state.expires_at else None,
                "message": state.message,
            }

        if command == "qr_password":
            # Сессию сохранит тот же приёмник, что и при обычном сканировании,
            # — здесь достаточно дождаться, что пароль принят.
            await _mtproto(provider).qr_password(account, args.get("password", ""))
            return {"ok": True}

        if command == "mark_read":
            await provider.mark_read(account, int(args["chat_id"]), int(args["max_id"]))
            return {"ok": True}

        if command == "edit_message":
            await provider.edit_message(
                account, int(args["chat_id"]), int(args["tg_message_id"]), str(args["text"])
            )
            return {"ok": True}

        if command == "sync_history":
            since = await _history_since(db, args.get("since"))
            task = asyncio.create_task(_sync_history(account_id, since))
            _tasks.add(task)
            task.add_done_callback(_tasks.discard)
            return {"started": True, "since": since.isoformat()}

        if command == "disconnect":
            await provider.stop(account)
            return {"ok": True}

        if command == "logout":
            from app.gateway import mtproto_provider

            if isinstance(provider, mtproto_provider.MTProtoProvider):
                await mtproto_provider.logout(provider, account)
            else:
                await provider.stop(account)
            account.session_enc = None
            account.status = AccountStatus.PENDING
            account.status_reason = "Сессия отозвана"
            await db.commit()
            return {"ok": True}

        if command == "demo_incoming":
            # Только демо-режим: вбрасывает событие так, будто оно пришло из
            # Telegram. Нужно, чтобы весь путь входящего сообщения — команда,
            # шлюз, приёмник, база, события в интерфейс — проверялся до того,
            # как появится живой аккаунт. В боевом режиме команда недоступна.
            from app.core.config import settings

            if not settings.demo_mode:
                raise ValueError("Команда доступна только в демо-режиме")
            event = inbound_service.InboundMessage(
                peer=inbound_service.PeerData(
                    tg_user_id=int(args["tg_user_id"]),
                    access_hash=args.get("access_hash"),
                    username=args.get("username"),
                    first_name=args.get("first_name"),
                    last_name=args.get("last_name"),
                ),
                tg_message_id=int(args["tg_message_id"]),
                date=(
                    datetime.fromisoformat(args["date"])
                    if args.get("date")
                    else datetime.now(UTC)
                ),
                text=args.get("text"),
                outgoing=bool(args.get("outgoing")),
                random_id=args.get("random_id"),
                live=bool(args.get("live", True)),
            )
            await write_inbound(account_id, event)
            return {"delivered": True}

        raise ValueError(f"Шлюз не знает команду «{command}»")


_tasks: set[asyncio.Task[None]] = set()


async def _history_since(db, raw: str | None) -> datetime:
    """С какого момента тянуть переписку.

    Настройка задаёт дату («с 1 января 2026»), а не срок в днях: срок уезжал бы
    каждый день и через год начал бы терять историю (см. DEFAULT_SETTINGS).
    """
    if raw:
        return datetime.fromisoformat(raw)
    value = await settings_service.get_value(db, "history_sync_from")
    if value:
        return datetime.fromisoformat(f"{value}T00:00:00+00:00")
    days = int(await settings_service.get_value(db, "history_sync_days") or 365)
    return datetime.now(UTC) - timedelta(days=days)


async def _sync_history(account_id: int, since: datetime) -> None:
    """Подтяжка переписки. Идёт фоном и сообщает о ходе событиями: на большом
    аккаунте это минуты, и держать на это время открытый запрос нельзя."""
    from app.gateway.provider import get_provider

    provider = get_provider()
    written = 0
    async with SessionLocal() as db:
        account = await _account(db, account_id)
        await emit_to_account(
            db, account_id, "account.sync", {"account_id": account_id, "state": "started"}
        )
        try:
            async for event in provider.iter_history(account, since):
                event.live = False
                if await inbound_service.ingest(db, account, event) is not None:
                    written += 1
                if written and written % HISTORY_BATCH_COMMIT == 0:
                    await db.commit()
                    await emit_to_account(
                        db,
                        account_id,
                        "account.sync",
                        {"account_id": account_id, "state": "running", "written": written},
                    )
            account.history_synced_until = datetime.now(UTC)
            await log_event(
                db,
                action="account.history_synced",
                entity_type="account",
                entity_id=account_id,
                actor_kind=ActorKind.GATEWAY,
                after={"written": written, "since": since.isoformat()},
            )
            await db.commit()
            await emit_to_account(
                db,
                account_id,
                "account.sync",
                {"account_id": account_id, "state": "done", "written": written},
            )
            log.info("История аккаунта %s подтянута: %s сообщений", account_id, written)
        except Exception as exc:
            await db.rollback()
            log.exception("Подтяжка истории аккаунта %s прервалась", account_id)
            await emit_to_account(
                db,
                account_id,
                "account.sync",
                {"account_id": account_id, "state": "failed", "error": str(exc)},
            )


# Верхняя граница подтяжки после перезапуска. Обычно перезапуск — это
# секунды, и `account.last_activity_at` их с запасом покрывает. Ограничение
# нужно на случай, если аккаунт простоял без дела долго: тогда шлюз не
# перечитывает историю за месяцы, а только за последнюю неделю.
CATCH_UP_MAX_LOOKBACK_DAYS = 7


async def catch_up(account_id: int) -> None:
    """Догнать пропущенное после перезапуска шлюза (например, при выкатке).

    Пока процесс был выключен, живые события Telegram никуда не приходили —
    сессия не хранит, докуда досмотрена лента (see docs/12-telegram-connect.md).
    Подтягиваем недавнюю историю от последнего известного сообщения: то, что
    уже есть в базе, схлопнется по `tg_message_id`, новое — допишется.
    """
    try:
        async with SessionLocal() as db:
            account = await _account(db, account_id)
            last_seen = account.last_activity_at
        if last_seen is None:
            # Только что подключённый аккаунт: первую полную подтяжку уже
            # запускает `confirm_code` — второй раз это делать не нужно.
            return
        since = max(last_seen, datetime.now(UTC) - timedelta(days=CATCH_UP_MAX_LOOKBACK_DAYS))
        await _sync_history(account_id, since)
    except Exception:
        log.exception("Не удалось подтянуть пропущенное для аккаунта %s", account_id)


async def held_account_ids(worker_id: str) -> set[int]:
    async with SessionLocal() as db:
        rows = await db.execute(
            select(TelegramAccount.id).where(
                TelegramAccount.worker_id == worker_id,
                TelegramAccount.is_active.is_(True),
                TelegramAccount.deleted_at.is_(None),
            )
        )
        return {row[0] for row in rows.all()}
