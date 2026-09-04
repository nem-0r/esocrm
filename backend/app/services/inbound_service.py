"""Приём сообщений из Telegram: единственная дверь, через которую чужой текст
попадает в базу.

Через неё идут все источники сразу — живое входящее, сообщение, которое менеджер
отправил с телефона мимо CRM, рассылка воронки и подтяжка старой переписки.
Разными их делает не путь, а три поля: `outgoing`, `random_id` и `live`.

Правила, ради которых это отдельный модуль:

- **Повтор не создаёт дубль.** Telegram переприсылает события при переподключении,
  а подтяжка истории идёт по тем же сообщениям, что уже приехали живыми. Опора —
  частичный уникальный индекс `(conversation_id, tg_message_id)`.
- **Своё исходящее опознаётся по `random_id`.** Сообщение с нашим random_id — это
  то, что CRM уже записала при отправке: у него проставляется tg_message_id, новая
  строка не появляется. Исходящее с чужим random_id — работа воронки или ответ
  менеджера с телефона; оно записывается как `userbot`, потому что автор неизвестен.
- **История не звонит в колокольчик.** При подтяжке `live=False`: не растёт счётчик
  непрочитанных, не начинается отсчёт ожидания, не летят события в интерфейс —
  иначе первое подключение аккаунта завалило бы менеджеров тысячей уведомлений.
"""

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import PurePosixPath
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AccountManager,
    Attachment,
    AuthorKind,
    Client,
    Conversation,
    Direction,
    Message,
    MessageKind,
    MessageStatus,
    TelegramAccount,
    TelegramPeer,
    User,
)
from app.realtime.events import conversation_audience, emit

log = logging.getLogger("astra.inbound")

MEDIA_KINDS = {
    "photo": MessageKind.PHOTO,
    "video": MessageKind.VIDEO,
    "voice": MessageKind.VOICE,
    "document": MessageKind.DOCUMENT,
}


@dataclass(slots=True)
class PeerData:
    """Собеседник глазами конкретного аккаунта."""

    tg_user_id: int
    access_hash: int | None = None
    username: str | None = None
    phone: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    is_bot: bool = False


@dataclass(slots=True)
class InboundMessage:
    """Событие Telegram, приведённое к тому, что нужно CRM."""

    peer: PeerData
    tg_message_id: int
    date: datetime
    text: str | None = None
    outgoing: bool = False
    random_id: int | None = None
    reply_to_tg_id: int | None = None
    media_kind: str | None = None
    # Живое событие или подтяжка истории.
    live: bool = True
    attachments: list[dict] = field(default_factory=list)


async def upsert_peer(db: AsyncSession, account_id: int, peer: PeerData) -> TelegramPeer:
    """Запомнить пропуск к собеседнику. Пустым `access_hash` не затираем прежний:
    в части событий Telegram его не присылает, а без него ответить нельзя."""
    row = await db.get(TelegramPeer, {"account_id": account_id, "tg_user_id": peer.tg_user_id})
    if row is None:
        row = TelegramPeer(account_id=account_id, tg_user_id=peer.tg_user_id)
        db.add(row)
    if peer.access_hash is not None:
        row.access_hash = peer.access_hash
    for name in ("username", "phone", "first_name", "last_name"):
        value = getattr(peer, name)
        if value is not None:
            setattr(row, name, value)
    row.is_bot = peer.is_bot
    row.last_seen_at = datetime.now(UTC)
    return row


async def _get_or_create_client(
    db: AsyncSession, account: TelegramAccount, peer: PeerData
) -> Client:
    client = await db.scalar(select(Client).where(Client.telegram_id == peer.tg_user_id))
    if client is None:
        client = Client(
            telegram_id=peer.tg_user_id,
            tg_username=peer.username,
            tg_first_name=peer.first_name,
            tg_last_name=peer.last_name,
            # Через какой аккаунт человек впервые пришёл — нужно для отчёта по каналам.
            created_via_account_id=account.id,
        )
        db.add(client)
        try:
            await db.flush()
        except IntegrityError:
            # Два сообщения от одного нового клиента пришли одновременно —
            # выиграла соседняя транзакция, забираем её строку.
            await db.rollback()
            client = await db.scalar(select(Client).where(Client.telegram_id == peer.tg_user_id))
            if client is None:
                raise
            return client
    else:
        # Профиль в Telegram меняется — держим карточку в актуальном виде,
        # но имя, вписанное менеджером руками (display_name), не трогаем.
        if peer.username is not None:
            client.tg_username = peer.username
        if peer.first_name is not None:
            client.tg_first_name = peer.first_name
        if peer.last_name is not None:
            client.tg_last_name = peer.last_name
    return client


async def get_or_create_conversation(
    db: AsyncSession, account: TelegramAccount, peer: PeerData, started_at: datetime | None = None
) -> Conversation:
    conversation = await db.scalar(
        select(Conversation).where(
            Conversation.account_id == account.id,
            Conversation.tg_chat_id == peer.tg_user_id,
        )
    )
    if conversation is not None:
        return conversation

    client = await _get_or_create_client(db, account, peer)
    peer_row = await upsert_peer(db, account.id, peer)
    peer_row.client_id = client.id
    # Если на аккаунте работает ровно один менеджер, диалог сразу закрепляется
    # за ним: у компании номер = человек, и после подтяжки истории он должен
    # увидеть свою переписку, а не сотни «ничейных» чатов в общей очереди.
    # Когда менеджеров несколько, диалог остаётся свободным — его разбирают
    # из очереди или назначает руководитель.
    sole_manager = await _sole_manager_id(db, account.id)
    conversation = Conversation(
        client_id=client.id,
        account_id=account.id,
        tg_chat_id=peer.tg_user_id,
        started_at=started_at or datetime.now(UTC),
        responsible_id=sole_manager,
        responsible_since=datetime.now(UTC) if sole_manager else None,
    )
    db.add(conversation)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        existing = await db.scalar(
            select(Conversation).where(
                Conversation.account_id == account.id,
                Conversation.tg_chat_id == peer.tg_user_id,
            )
        )
        if existing is None:
            raise
        return existing
    return conversation


async def _sole_manager_id(db: AsyncSession, account_id: int) -> int | None:
    rows = await db.execute(
        select(AccountManager.user_id).where(AccountManager.account_id == account_id)
    )
    ids = [row[0] for row in rows.all()]
    if len(ids) != 1:
        return None
    # «Приём заявок» выключен — новые диалоги ему не назначаются (это ровно
    # то, что обещано в профиле), даже если он единственный менеджер аккаунта.
    # Диалог тогда остаётся ничейным — руководитель назначит его сам.
    accepting = await db.scalar(select(User.accepting_leads).where(User.id == ids[0]))
    return ids[0] if accepting else None


async def ingest(
    db: AsyncSession, account: TelegramAccount, event: InboundMessage
) -> Message | None:
    """Записать событие Telegram. Возвращает сообщение или None, если это повтор.

    Транзакцию не закрывает: вызывающий решает, когда фиксировать. При подтяжке
    истории это позволяет писать пачками, а не по строке на коммит.
    """
    conversation = await get_or_create_conversation(db, account, event.peer, event.date)
    peer_row = await upsert_peer(db, account.id, event.peer)
    if peer_row.client_id is None:
        peer_row.client_id = conversation.client_id

    # Своё исходящее: строка уже есть, ей не хватает только номера в Telegram.
    if event.outgoing and event.random_id:
        own = await db.scalar(select(Message).where(Message.random_id == event.random_id))
        if own is not None:
            if own.tg_message_id is None:
                own.tg_message_id = event.tg_message_id
                own.sent_at = own.sent_at or event.date
                if own.status == MessageStatus.QUEUED:
                    own.status = MessageStatus.SENT
            return None

    duplicate = await db.scalar(
        select(Message.id).where(
            Message.conversation_id == conversation.id,
            Message.tg_message_id == event.tg_message_id,
        )
    )
    if duplicate is not None:
        return None

    kind = MEDIA_KINDS.get(event.media_kind or "", MessageKind.TEXT)
    message = Message(
        conversation_id=conversation.id,
        tg_message_id=event.tg_message_id,
        direction=Direction.OUT if event.outgoing else Direction.IN,
        # Исходящее без нашего random_id — это воронка или ответ с телефона:
        # автора в CRM у него нет, и приписывать его менеджеру нельзя.
        author_kind=AuthorKind.USERBOT if event.outgoing else AuthorKind.CLIENT,
        kind=kind,
        text=event.text,
        status=MessageStatus.SENT,
        sent_at=event.date,
        created_at=event.date,
        reply_to_tg_id=event.reply_to_tg_id,
    )
    db.add(message)
    try:
        await db.flush()
    except IntegrityError:
        # Гонка живого события с подтяжкой истории — обе стороны пишут одно сообщение.
        await db.rollback()
        return None

    if event.attachments:
        await _save_attachments(db, conversation, message, event.attachments)

    _apply_counters(conversation, event)
    account.last_activity_at = datetime.now(UTC)

    if event.live:
        await _publish(db, conversation, message)
    return message


async def _save_attachments(
    db: AsyncSession, conversation: Conversation, message: Message, items: list[dict]
) -> None:
    """Файл из чата кладём к себе сразу: ссылка Telegram живёт недолго, а переписку
    надо будет открывать через год. Не скачался — сообщение всё равно сохраняется,
    иначе одна картинка потеряла бы текст."""
    from app.core import storage

    now = datetime.now(UTC)
    for item in items:
        body = item.get("body")
        file_name = item.get("file_name") or "file"
        key = f"incoming/{now:%Y}/{now:%m}/{uuid4().hex}{PurePosixPath(file_name).suffix.lower()}"
        try:
            if body:
                await storage.put_object(key, body, filename=file_name)
        except Exception:
            log.exception("Файл %s из диалога %s не сохранён", file_name, conversation.id)
            continue
        db.add(
            Attachment(
                message_id=message.id,
                conversation_id=conversation.id,
                client_id=conversation.client_id,
                file_name=file_name,
                mime_type=item.get("mime_type"),
                size_bytes=len(body or b""),
                storage_key=key,
                telegram_file_id=item.get("telegram_file_id"),
                width=item.get("width"),
                height=item.get("height"),
                duration_sec=item.get("duration_sec"),
            )
        )
    await db.flush()


def _apply_counters(conversation: Conversation, event: InboundMessage) -> None:
    """Счётчики диалога. При подтяжке истории двигаем только отметки времени:
    старая переписка не должна показаться менеджеру непрочитанной и просроченной."""
    if conversation.last_message_at is None or event.date > conversation.last_message_at:
        conversation.last_message_at = event.date

    if event.outgoing:
        if (
            conversation.last_manager_message_at is None
            or event.date > conversation.last_manager_message_at
        ):
            conversation.last_manager_message_at = event.date
        # Ответ дан — хоть из CRM, хоть с телефона, хоть воронкой.
        conversation.awaiting_reply_since = None
        return

    if (
        conversation.last_client_message_at is None
        or event.date > conversation.last_client_message_at
    ):
        conversation.last_client_message_at = event.date
    if not event.live:
        return
    conversation.unread_count += 1
    # Ожидание начинается с первого сообщения без ответа, а не с последнего:
    # иначе клиент, написавший пять раз подряд, каждый раз обнулял бы просрочку.
    if conversation.awaiting_reply_since is None:
        conversation.awaiting_reply_since = event.date


async def _publish(db: AsyncSession, conversation: Conversation, message: Message) -> None:
    from app.services.message_service import to_out

    audience = await conversation_audience(db, conversation.id)
    payload = {
        "conversation_id": conversation.id,
        "message": to_out(message, []).model_dump(mode="json"),
    }
    await emit("message.new", payload, audience)
    await emit("counters.updated", {"conversation_id": conversation.id, "stale": True}, audience)


async def mark_outgoing_read(
    db: AsyncSession, account: TelegramAccount, tg_chat_id: int, up_to_tg_id: int
) -> int:
    """Клиент прочитал наши сообщения (updateReadHistoryOutbox). Возвращает,
    сколько строк изменилось — ноль означает, что событие уже применяли."""
    conversation = await db.scalar(
        select(Conversation).where(
            Conversation.account_id == account.id, Conversation.tg_chat_id == tg_chat_id
        )
    )
    if conversation is None:
        return 0
    rows = (
        await db.execute(
            select(Message).where(
                Message.conversation_id == conversation.id,
                Message.direction == Direction.OUT,
                Message.tg_message_id.isnot(None),
                Message.tg_message_id <= up_to_tg_id,
                Message.read_at.is_(None),
            )
        )
    ).scalars().all()
    now = datetime.now(UTC)
    for message in rows:
        message.read_at = now
        message.status = MessageStatus.READ
    if rows:
        from app.services.message_service import to_out

        audience = await conversation_audience(db, conversation.id)
        for message in rows:
            await emit(
                "message.updated",
                {
                    "conversation_id": conversation.id,
                    "message": to_out(message, []).model_dump(mode="json"),
                },
                audience,
            )
    return len(rows)
