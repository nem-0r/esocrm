"""Демо-данные: `python -m app.seed.run`.

Прогон детерминированный — один и тот же запуск даёт одну и ту же базу.
Скрипт очищает данные и создаёт их заново, поэтому в продакшене он отказывается
работать: сид, способный стереть живую базу, — это то, как прошлая версия
продукта потеряла рабочие данные.
"""

import asyncio
import random
import sys
import uuid
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import text

from app.core.config import settings
from app.core.crypto import encrypt, new_token, token_hash
from app.core.db import SessionLocal
from app.core.storage import ensure_bucket, put_object
from app.models import (
    AccountManager,
    Attachment,
    AuthorKind,
    BirthTimeApprox,
    Client,
    Conversation,
    Deal,
    DealEvent,
    DealEventKind,
    DealItem,
    DealStatus,
    Direction,
    FunnelStage,
    Message,
    MessageKind,
    MessageStatus,
    Note,
    Notification,
    NotificationKind,
    PaidSource,
    PaymentMethod,
    PaymentRequisite,
    TelegramAccount,
    Template,
    User,
    UserRole,
)
from app.models.account import AccountStatus
from app.models.client import zodiac_for
from app.seed import data as d
from app.services.auth_service import hash_password
from app.services.deal_service import invoice_text

# Порядок не важен: CASCADE разберётся. `settings` и `alembic_version` не трогаем.
TABLES = [
    "deal_events",
    "deal_items",
    "payment_events",
    "deals",
    "attachments",
    "outbox",
    "messages",
    "conversations",
    "notes",
    "notifications",
    "search_history",
    "event_log",
    "auth_sessions",
    "login_attempts",
    "account_managers",
    "telegram_accounts",
    "payment_requisites",
    "templates",
    "clients",
    "gateway_workers",
    "users",
    "daily_stats",
]

rnd = random.Random(d.RANDOM_SEED)  # noqa: S311 — демо-данные, а не криптография
NOW = datetime.now(UTC)

# Минимальный валидный PNG 1×1 — реальный файл для демо-чеков, не заглушка.
DEMO_RECEIPT_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000100000001080600000"
    "01f15c4890000000a4944415478da6360000002000155ff2ba00000000049454e44ae426082"
)


def ago(days: float = 0, hours: float = 0, minutes: float = 0) -> datetime:
    return NOW - timedelta(days=days, hours=hours, minutes=minutes)


async def wipe(db) -> None:
    await db.execute(text(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY CASCADE"))
    # Номера, видимые пользователю, снова стартуют с «красивых» значений.
    await db.execute(text("ALTER SEQUENCE clients_id_seq RESTART WITH 80000"))
    await db.execute(text("ALTER SEQUENCE deals_id_seq RESTART WITH 1000"))
    await db.commit()


async def make_staff(db) -> dict[str, User]:
    password = await hash_password(d.DEMO_PASSWORD)
    staff: dict[str, User] = {}
    for row in d.STAFF:
        invite_pending = row.get("invite_pending", False)
        user = User(
            full_name=row["full_name"],
            email=row["email"],
            phone=row["phone"],
            role=UserRole(row["role"]),
            avatar_color=row["avatar_color"],
            is_active=row.get("is_active", True),
            accepting_leads=True,
            password_hash=None if invite_pending else password,
            accepted_at=None if invite_pending else ago(days=90),
            invite_token_hash=token_hash(new_token()) if invite_pending else None,
            invite_expires_at=NOW + timedelta(days=5) if invite_pending else None,
            invite_sent_at=ago(days=2) if invite_pending else None,
            created_at=ago(days=120),
        )
        schedule = row.get("schedule")
        if schedule:
            user.schedule_enabled = True
            user.work_days = schedule["days"]
            user.work_start = time.fromisoformat(schedule["start"])
            user.work_end = time.fromisoformat(schedule["end"])
        db.add(user)
        staff[row["key"]] = user
    await db.flush()

    # Онлайн-статус: «более 5 минут отсутствия — офлайн».
    staff["elena"].last_seen_at = ago(minutes=1)
    staff["marina"].last_seen_at = ago(minutes=1)
    staff["anna"].last_seen_at = ago(hours=3)
    return staff


async def make_accounts(db, staff: dict[str, User]) -> dict[str, TelegramAccount]:
    accounts: dict[str, TelegramAccount] = {}
    for index, row in enumerate(d.ACCOUNTS):
        connected = row["status"] == "connected"
        account = TelegramAccount(
            title=row["title"],
            phone=row["phone"],
            funnel_stage=FunnelStage(row["funnel_stage"]),
            api_id=27272230 + index,
            api_hash_enc=encrypt(f"demo-api-hash-{index}"),
            session_enc=encrypt(f"demo-session-{index}") if connected else None,
            tg_user_id=7800000000 + index if connected else None,
            tg_username=row["tg_username"],
            status=AccountStatus(row["status"]),
            status_reason=row.get("status_reason"),
            last_activity_at=ago(minutes=rnd.randint(2, 90)) if connected else ago(days=3),
            history_synced_until=ago(days=90) if connected else None,
            created_at=ago(days=110),
        )
        db.add(account)
        accounts[row["key"]] = account
    await db.flush()

    for row in d.ACCOUNTS:
        for manager_key in row["managers"]:
            db.add(
                AccountManager(
                    account_id=accounts[row["key"]].id,
                    user_id=staff[manager_key].id,
                    assigned_at=ago(days=100),
                    assigned_by_id=staff["elena"].id,
                )
            )
    await db.flush()
    return accounts


async def make_reference_books(db, staff: dict[str, User]) -> list[PaymentRequisite]:
    requisites = []
    for order, row in enumerate(d.REQUISITES):
        requisite = PaymentRequisite(
            title=row["title"],
            bank_name=row["bank_name"],
            account_masked=row["account_masked"],
            details_text=row["details_text"],
            sort_order=order,
            created_at=ago(days=110),
        )
        db.add(requisite)
        requisites.append(requisite)

    for order, (title, body) in enumerate(d.COMMON_TEMPLATES):
        db.add(Template(title=title, text=body, sort_order=order, created_at=ago(days=100)))

    title, body = d.MARINA_PERSONAL_TEMPLATE
    db.add(
        Template(
            title=title,
            text=body,
            owner_id=staff["marina"].id,
            sort_order=0,
            created_at=ago(days=40),
        )
    )
    await db.flush()
    return requisites


async def make_clients(db) -> list[Client]:
    clients: list[Client] = []
    for index, name in enumerate(d.CLIENT_NAMES):
        birth = date(rnd.randint(1975, 2000), rnd.randint(1, 12), rnd.randint(1, 28))
        # Примерно у каждого пятого данные неполные — это отдельная метка в интерфейсе.
        incomplete = index % 5 == 0
        first_contact = ago(days=rnd.randint(1, 120), hours=rnd.randint(0, 23))
        client = Client(
            telegram_id=500000000 + index * 137,
            tg_username=f"user{index:03d}" if index % 3 else None,
            tg_first_name=name.split()[0],
            tg_last_name=name.split()[1] if len(name.split()) > 1 else None,
            display_name=name,
            phone=(
                f"+7 9{rnd.randint(10, 89)} {rnd.randint(100, 999)}"
                f"-{rnd.randint(10, 99)}-{rnd.randint(10, 99)}"
            ),
            source_code=f"tarot-{rnd.randint(1000000, 9999999)}" if index % 7 else None,
            birth_date=birth,
            birth_time=None
            if incomplete
            else time(rnd.randint(0, 23), rnd.choice([0, 15, 20, 30, 45])),
            # Точного времени нет — но часть клиентов помнит хотя бы часть суток.
            birth_time_approx=(
                rnd.choice(list(BirthTimeApprox)) if incomplete and index % 3 == 0 else None
            ),
            birth_city=None if incomplete else rnd.choice(d.CITIES),
            zodiac_sign=zodiac_for(birth),
            # Согласия собрал бот воронки при первом обращении.
            pdn_consent_at=first_contact,
            pdn_consent_version="1.2",
            marketing_consent=index % 9 != 0,
            marketing_consent_at=first_contact,
            first_contact_at=first_contact,
            created_at=first_contact,
        )
        db.add(client)
        clients.append(client)
    await db.flush()
    return clients


def _client_text(texts: list[str]) -> str:
    """В демо-фразах есть подстановка `{city}`. Она обязана превратиться в город:
    фигурные скобки на экране во время показа выглядят как поломка."""
    return rnd.choice(texts).replace("{city}", rnd.choice(d.CITIES))


def _stage_texts(stage: FunnelStage) -> tuple[list[str], list[str]]:
    if stage == FunnelStage.WARMUP:
        return d.CLIENT_MSG_WARMUP, d.MANAGER_MSG_WARMUP
    if stage == FunnelStage.DIAGNOSTIC:
        return d.CLIENT_MSG_DIAGNOSTIC, d.MANAGER_MSG_DIAGNOSTIC
    return d.CLIENT_MSG_SALES, d.MANAGER_MSG_SALES


async def make_conversations(
    db, clients: list[Client], accounts: dict[str, TelegramAccount], staff: dict[str, User]
) -> list[Conversation]:
    """Диалоги и переписка. Ровно шесть диалогов остаются без ответа менеджера —
    на них держится баннер «ждёт больше 30 минут» и фильтр «ждут ответа»."""
    account_list = [accounts["warmup"], accounts["sales"], accounts["diagnostic"]]
    responsible_for = {
        accounts["warmup"].id: [staff["marina"], staff["anna"]],
        accounts["sales"].id: [staff["marina"]],
        accounts["diagnostic"].id: [staff["anna"]],
    }

    conversations: list[Conversation] = []
    for index, client in enumerate(clients):
        for account in account_list[: 1 + index % 3]:
            started = client.first_contact_at + timedelta(hours=rnd.randint(0, 48))
            conversation = Conversation(
                client_id=client.id,
                account_id=account.id,
                tg_chat_id=client.telegram_id,
                started_at=started,
                created_at=started,
            )
            db.add(conversation)
            conversations.append(conversation)
    await db.flush()

    # Шесть диалогов из «продаж» оставим ждущими ответа — с разным ожиданием.
    waiting_minutes = [4 * 60 + 12, 62, 41, 18, 11, 6]
    waiting_ids = {c.id for c in conversations[:6]}
    waiting_by_id = dict(zip(waiting_ids, waiting_minutes, strict=False))

    total_messages = 0
    for conversation in conversations:
        account = next(a for a in account_list if a.id == conversation.account_id)
        client_texts, manager_texts = _stage_texts(account.funnel_stage)
        managers = responsible_for[account.id]
        manager = rnd.choice(managers)

        pairs = rnd.randint(2, 5)
        # Переписка укладывается в прошлое: сначала считаем, сколько времени она
        # займёт, и только потом выбираем начало. Иначе накопленные шаги
        # уводят последние сообщения в будущее, и в списке видны завтрашние даты.
        span = timedelta(hours=pairs * 42 + 30)
        latest_start = NOW - span
        earliest_start = min(conversation.started_at, latest_start)
        cursor = earliest_start + (latest_start - earliest_start) * rnd.random()
        conversation.started_at = cursor
        last_client: datetime | None = None
        last_manager: datetime | None = None

        for step in range(pairs):
            cursor += timedelta(hours=rnd.randint(2, 40))
            db.add(
                Message(
                    conversation_id=conversation.id,
                    tg_message_id=1000 + step * 2,
                    direction=Direction.IN,
                    author_kind=AuthorKind.CLIENT,
                    kind=MessageKind.TEXT,
                    text=_client_text(client_texts),
                    status=MessageStatus.READ,
                    created_at=cursor,
                    sent_at=cursor,
                    read_at=cursor + timedelta(minutes=2),
                )
            )
            last_client = cursor
            total_messages += 1

            cursor += timedelta(minutes=rnd.randint(3, 55))
            failed = total_messages % 137 == 0
            db.add(
                Message(
                    conversation_id=conversation.id,
                    tg_message_id=1001 + step * 2,
                    direction=Direction.OUT,
                    author_kind=AuthorKind.MANAGER,
                    author_id=manager.id,
                    kind=MessageKind.TEXT,
                    text=rnd.choice(manager_texts),
                    status=MessageStatus.FAILED if failed else MessageStatus.READ,
                    error_text=rnd.choice(d.FAILED_ERROR_TEXTS) if failed else None,
                    random_id=rnd.getrandbits(62),
                    sent_at=None if failed else cursor,
                    read_at=None if failed else cursor + timedelta(minutes=6),
                    created_at=cursor,
                )
            )
            last_manager = cursor
            total_messages += 1

        # Рассылки воронки: ушли мимо CRM, автор — юзербот, а не менеджер.
        if account.funnel_stage == FunnelStage.WARMUP and rnd.random() < 0.7:
            cursor += timedelta(hours=rnd.randint(4, 30))
            db.add(
                Message(
                    conversation_id=conversation.id,
                    tg_message_id=2000,
                    direction=Direction.OUT,
                    author_kind=AuthorKind.USERBOT,
                    kind=MessageKind.TEXT,
                    text=rnd.choice(d.USERBOT_BROADCASTS),
                    status=MessageStatus.READ,
                    sent_at=cursor,
                    read_at=cursor + timedelta(minutes=30),
                    created_at=cursor,
                )
            )
            last_manager = cursor
            total_messages += 1

        # Служебные заметки — клиент их не видит. Каждый четвёртый диалог, а не
        # по случайности: требование доски нужно показывать, а не надеяться на бросок.
        if conversation.id % 4 == 0:
            cursor += timedelta(minutes=8)
            db.add(
                Message(
                    conversation_id=conversation.id,
                    direction=Direction.OUT,
                    author_kind=AuthorKind.MANAGER,
                    author_id=manager.id,
                    kind=MessageKind.SERVICE,
                    text=rnd.choice(d.SERVICE_NOTES),
                    is_internal=True,
                    status=MessageStatus.SENT,
                    sent_at=cursor,
                    created_at=cursor,
                )
            )
            total_messages += 1

        conversation.responsible_id = None if rnd.random() < 0.12 else manager.id
        conversation.responsible_since = conversation.responsible_id and last_manager
        conversation.last_manager_message_at = last_manager
        conversation.last_client_message_at = last_client
        conversation.last_message_at = max(filter(None, [last_client, last_manager]))
        conversation.unread_count = 0

        if conversation.id in waiting_ids:
            minutes = waiting_by_id[conversation.id]
            asked = ago(minutes=minutes)
            db.add(
                Message(
                    conversation_id=conversation.id,
                    tg_message_id=3000,
                    direction=Direction.IN,
                    author_kind=AuthorKind.CLIENT,
                    kind=MessageKind.TEXT,
                    text=_client_text(client_texts),
                    status=MessageStatus.SENT,
                    created_at=asked,
                    sent_at=asked,
                )
            )
            total_messages += 1
            conversation.last_client_message_at = asked
            conversation.last_message_at = asked
            conversation.awaiting_reply_since = asked
            conversation.unread_count = rnd.randint(1, 3)

    await db.flush()
    return conversations


async def make_deals(
    db,
    conversations: list[Conversation],
    requisites: list[PaymentRequisite],
    staff: dict[str, User],
) -> int:
    """34 сделки во всех состояниях: 24 оплачено, 5 ждут, 2 истекли, 3 отменены."""
    plan = (
        [DealStatus.PAID] * 24
        + [DealStatus.AWAITING] * 5
        + [DealStatus.EXPIRED] * 2
        + [DealStatus.CANCELLED] * 3
    )
    sellers = [staff["marina"]] * 7 + [staff["anna"]] * 3
    pool = [c for c in conversations if c.awaiting_reply_since is None]
    rnd.shuffle(pool)

    for index, status in enumerate(plan):
        conversation = pool[index % len(pool)]
        seller = sellers[index % len(sellers)]
        requisite = requisites[index % len(requisites)]
        created = ago(days=rnd.randint(1, 120), hours=rnd.randint(0, 20))

        deal = Deal(
            client_id=conversation.client_id,
            conversation_id=conversation.id,
            created_by_id=seller.id,
            sold_by_id=seller.id,
            payment_method=PaymentMethod.REQUISITES,
            requisite_id=requisite.id,
            requisites_snapshot=requisite.details_text,
            status=status,
            total_amount=0,
            created_at=created,
        )
        db.add(deal)
        await db.flush()

        names = rnd.sample(list(d.DEAL_ITEM_PRICES), rnd.randint(1, 3))
        total = 0
        for position, name in enumerate(names):
            amount = d.DEAL_ITEM_PRICES[name]
            db.add(
                DealItem(
                    deal_id=deal.id,
                    name=name,
                    amount=amount,
                    position=position,
                    created_at=created,
                )
            )
            total += amount
        deal.total_amount = total

        events = [(DealEventKind.CREATED, None, created)]
        sent_at = created + timedelta(minutes=rnd.randint(2, 40))
        deal.sent_at = sent_at
        deal.expires_at = sent_at + timedelta(days=7)
        events.append((DealEventKind.SENT, None, sent_at))

        if status == DealStatus.PAID:
            paid_at = sent_at + timedelta(hours=rnd.randint(1, 90))
            deal.paid_at = paid_at
            deal.paid_by_id = seller.id
            deal.paid_source = PaidSource.MANUAL
            events.append((DealEventKind.PAID, "Оплата подтверждена вручную по чеку", paid_at))
            # Оплаченная сделка без чека — то же самое, чего эта фича и должна
            # была избежать. Демо обязано показывать реальный прикреплённый файл.
            receipt_key = f"uploads/{paid_at:%Y}/{paid_at:%m}/{uuid.uuid4()}.png"
            await put_object(receipt_key, DEMO_RECEIPT_PNG, filename="чек.png")
            deal.receipt_storage_key = receipt_key
            deal.receipt_file_name = "чек.png"
            deal.receipt_mime_type = "image/png"
            deal.receipt_size_bytes = len(DEMO_RECEIPT_PNG)
        elif status == DealStatus.EXPIRED:
            # Срок истёк — двигаем отправку назад, чтобы это было правдой.
            deal.sent_at = ago(days=rnd.randint(9, 30))
            deal.expires_at = deal.sent_at + timedelta(days=7)
            events[-1] = (DealEventKind.SENT, None, deal.sent_at)
            events.append((DealEventKind.EXPIRED, None, deal.expires_at))
        elif status == DealStatus.CANCELLED:
            reason = rnd.choice(d.CANCEL_REASONS)
            cancelled_at = sent_at + timedelta(hours=rnd.randint(2, 60))
            deal.cancelled_at = cancelled_at
            deal.cancel_reason = reason
            events.append((DealEventKind.CANCELLED, reason, cancelled_at))
        else:
            # Ждёт оплаты: часть сделок близка к истечению срока.
            deal.sent_at = ago(days=rnd.randint(1, 6))
            deal.expires_at = deal.sent_at + timedelta(days=7)
            events[-1] = (DealEventKind.SENT, None, deal.sent_at)

        # Счёт в чате. Текст берём у продакшен-кода, а не пишем свой: демо
        # должно показывать ровно то, что клиент увидит в Telegram.
        invoice = Message(
            conversation_id=conversation.id,
            direction=Direction.OUT,
            author_kind=AuthorKind.MANAGER,
            author_id=seller.id,
            kind=MessageKind.TEXT,
            text=invoice_text(deal, requisite),
            status=MessageStatus.READ,
            random_id=rnd.getrandbits(62),
            sent_at=deal.sent_at,
            read_at=deal.sent_at + timedelta(minutes=rnd.randint(3, 40)),
            created_at=deal.sent_at,
        )
        db.add(invoice)
        await db.flush()
        deal.sent_message_id = invoice.id

        # Счёт мог оказаться свежее всего, что было в диалоге, — тогда список
        # чатов обязан показывать именно его, иначе превью врёт.
        if conversation.last_message_at is None or deal.sent_at > conversation.last_message_at:
            conversation.last_message_at = deal.sent_at
            conversation.last_manager_message_at = deal.sent_at

        for kind, comment, at in events:
            db.add(
                DealEvent(
                    deal_id=deal.id,
                    actor_id=seller.id,
                    kind=kind,
                    comment=comment,
                    created_at=at,
                )
            )

    await db.flush()
    return len(plan)


async def make_materials(db, clients: list[Client], staff: dict[str, User]) -> int:
    """Материалы клиента — это вложения исходящих сообщений.

    Файлы кладём в хранилище по-настоящему: вкладка «Материалы» и скачивание
    должны работать на демо-стенде так же, как на боевом, иначе показывать нечего.
    """
    ensure_bucket()
    rows = (
        await db.execute(
            text(
                "select c.id as conv_id, c.client_id, m.id as msg_id, m.author_id "
                "from messages m join conversations c on c.id = m.conversation_id "
                "where m.direction = 'out' and m.author_kind = 'manager' "
                "and m.is_internal = false and m.status = 'read' "
                "order by m.created_at desc limit 18"
            )
        )
    ).all()

    created = 0
    for index, row in enumerate(rows):
        file_name, mime = d.MATERIALS[index % len(d.MATERIALS)]
        key = f"uploads/demo/{row.msg_id}-{index}.pdf"
        body = (
            f"Демонстрационный файл: {file_name}\n"
            "Это не настоящий разбор, а заглушка для показа вкладки «Материалы».\n"
        ).encode()
        await put_object(key, body, file_name)
        db.add(
            Attachment(
                message_id=row.msg_id,
                conversation_id=row.conv_id,
                client_id=row.client_id,
                file_name=file_name,
                mime_type=mime,
                size_bytes=len(body),
                storage_key=key,
                created_at=ago(days=rnd.randint(1, 40)),
            )
        )
        created += 1
    await db.flush()
    return created


async def make_notes_and_notifications(db, clients: list[Client], staff: dict[str, User]) -> None:
    for index, note_text in enumerate(d.NOTE_TEXTS):
        db.add(
            Note(
                client_id=clients[index % len(clients)].id,
                author_id=(staff["marina"] if index % 2 else staff["anna"]).id,
                text=note_text,
                created_at=ago(days=rnd.randint(1, 60)),
            )
        )

    paid = (
        await db.execute(
            text(
                "select id, total_amount, client_id from deals "
                "where status = 'paid' order by paid_at desc limit 8"
            )
        )
    ).all()
    names = {c.id: c.display_name for c in clients}
    for position, row in enumerate(paid):
        for user in (staff["elena"], staff["marina"]):
            db.add(
                Notification(
                    user_id=user.id,
                    kind=NotificationKind.DEAL_PAID,
                    entity_type="deal",
                    entity_id=row.id,
                    payload={
                        "number": f"DEAL-{row.id}",
                        "amount": row.total_amount,
                        "client_name": names.get(row.client_id, "Клиент"),
                    },
                    read_at=None if position < 3 else ago(days=1),
                    created_at=ago(days=position, hours=rnd.randint(0, 12)),
                )
            )


async def main() -> int:
    if settings.app_env == "production":
        print("Отказ: демо-данные нельзя заливать в продакшен — они стирают базу.")
        return 1

    async with SessionLocal() as db:
        await wipe(db)
        staff = await make_staff(db)
        accounts = await make_accounts(db, staff)
        requisites = await make_reference_books(db, staff)
        clients = await make_clients(db)
        conversations = await make_conversations(db, clients, accounts, staff)
        deals_count = await make_deals(db, conversations, requisites, staff)
        materials = await make_materials(db, clients, staff)

        # ТЗ п. 5.2: через какой аккаунт клиент пришёл впервые — это аккаунт
        # самого раннего его диалога.
        await db.execute(
            text(
                """
                update clients c
                set created_via_account_id = first_conv.account_id
                from (
                    select distinct on (client_id) client_id, account_id
                    from conversations
                    order by client_id, started_at, id
                ) first_conv
                where first_conv.client_id = c.id
                """
            )
        )
        await make_notes_and_notifications(db, clients, staff)
        await db.commit()

        messages = (await db.execute(text("select count(*) from messages"))).scalar_one()
        awaiting = (
            await db.execute(
                text("select count(*) from conversations where awaiting_reply_since is not null")
            )
        ).scalar_one()

    print("Демо-данные готовы.")
    print(f"  сотрудников: {len(staff)}   аккаунтов: {len(accounts)}   клиентов: {len(clients)}")
    print(f"  диалогов: {len(conversations)}   сообщений: {messages}   сделок: {deals_count}")
    print(f"  ждут ответа: {awaiting}   материалов: {materials}")
    print(f"Вход: elena@astra.ru / {d.DEMO_PASSWORD} (руководитель)")
    print(f"      marina@astra.ru / {d.DEMO_PASSWORD} (менеджер)")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
