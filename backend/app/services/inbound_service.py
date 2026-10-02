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
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import PurePosixPath
from uuid import uuid4

from sqlalchemy import BigInteger, cast, func, or_, select
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AccountManager,
    Attachment,
    AttachmentStatus,
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
from app.models.enums import message_kind_for
from app.realtime.events import conversation_audience, emit

log = logging.getLogger("astra.inbound")



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
    # Переслано от, контакт, геопозиция, опрос — см. app/schemas/message.py.
    meta: dict | None = None
    # Правка уже известного сообщения (клиент или менеджер с телефона поменял текст).
    is_edit: bool = False
    edit_date: datetime | None = None


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


def _sync_client_from_peer(client: Client, peer: PeerData) -> None:
    """Профиль в Telegram меняется — держим карточку в актуальном виде,
    но имя, вписанное менеджером руками (display_name), не трогаем.

    Вызывается на каждое входящее, а не только при создании клиента: телефон
    и username часто не видны при первом сообщении (закрыты приватностью или
    ещё не заведены) и появляются в профиле позже."""
    if peer.username is not None:
        client.tg_username = peer.username
    # Телефон, в отличие от tg_username/имени, редактируется менеджером
    # вручную (docs: карточка клиента) — заполняем из Telegram только пока
    # там пусто, а не переписываем чужую правку на каждое сообщение.
    if peer.phone is not None and client.phone is None:
        client.phone = peer.phone
    if peer.first_name is not None:
        client.tg_first_name = peer.first_name
    if peer.last_name is not None:
        client.tg_last_name = peer.last_name


async def _get_or_create_client(
    db: AsyncSession,
    account: TelegramAccount,
    peer: PeerData,
    on_new_client: Callable[[Client], None] | None = None,
) -> Client:
    client = await db.scalar(select(Client).where(Client.telegram_id == peer.tg_user_id))
    if client is None:
        client = Client(
            telegram_id=peer.tg_user_id,
            tg_username=peer.username,
            tg_first_name=peer.first_name,
            tg_last_name=peer.last_name,
            phone=peer.phone,
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
        # Новый клиент — шанс подхватить день рождения из Telegram, если он там
        # открыт. Само обращение к Telegram сюда не входит намеренно: этот
        # модуль не знает про MTProto, вызывающий (шлюз) сам решает, как и когда.
        if on_new_client is not None:
            on_new_client(client)
    else:
        _sync_client_from_peer(client, peer)
    return client


async def get_or_create_conversation(
    db: AsyncSession,
    account: TelegramAccount,
    peer: PeerData,
    started_at: datetime | None = None,
    on_new_client: Callable[[Client], None] | None = None,
) -> Conversation:
    conversation = await db.scalar(
        select(Conversation).where(
            Conversation.account_id == account.id,
            Conversation.tg_chat_id == peer.tg_user_id,
        )
    )
    if conversation is not None:
        # Диалог уже есть — но синк профиля раньше происходил только при первом
        # сообщении. Телефон/username часто становятся видны позже, поэтому
        # обновляем при каждом входящем, а не только при создании диалога.
        client = await db.get(Client, conversation.client_id)
        if client is not None:
            _sync_client_from_peer(client, peer)
        return conversation

    client = await _get_or_create_client(db, account, peer, on_new_client)
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
    db: AsyncSession,
    account: TelegramAccount,
    event: InboundMessage,
    on_new_client: Callable[[Client], None] | None = None,
) -> Message | None:
    """Записать событие Telegram. Возвращает сообщение или None, если это повтор.

    Транзакцию не закрывает: вызывающий решает, когда фиксировать. При подтяжке
    истории это позволяет писать пачками, а не по строке на коммит.

    `on_new_client` — необязательный обратный вызов на случай, когда клиент
    заведён впервые (не при повторных сообщениях). Ничего не ждёт и не должен
    падать — вызывающий сам решает, что с этим делать (например, спросить
    у Telegram дату рождения в отдельной задаче, не задерживая приём).
    """
    conversation = await get_or_create_conversation(
        db, account, event.peer, event.date, on_new_client
    )
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

    # Известное сообщение ищем и по главному номеру, и по остальным номерам
    # альбома: иначе пять фото, отправленные из CRM одним сообщением, при
    # подтяжке истории вернулись бы ещё четырьмя «новыми».
    existing = await db.scalar(
        select(Message).where(
            Message.conversation_id == conversation.id,
            or_(
                Message.tg_message_id == event.tg_message_id,
                Message.tg_extra_ids.any(event.tg_message_id),
            ),
        )
    )
    if existing is not None:
        if event.is_edit:
            await _apply_edit(db, conversation, existing, event)
        return None

    kind = message_kind_for(event.media_kind) if event.media_kind else MessageKind.TEXT
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
        meta=event.meta or None,
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
    # Написал — точно не заблокировал (или снял блокировку): держать баннер
    # после этого не за что.
    if not event.outgoing and conversation.is_blocked_by_client:
        conversation.is_blocked_by_client = False

    if event.live:
        await _publish(db, conversation, message)
    return message


async def _save_attachments(
    db: AsyncSession, conversation: Conversation, message: Message, items: list[dict]
) -> None:
    """Файл из чата кладём к себе сразу: ссылка Telegram живёт недолго, а переписку
    надо будет открывать через год. Не скачался или крупный — вложение всё равно
    заводится (с пометкой «докачивается» или «слишком большой»): менеджер видит,
    что клиент что-то прислал, а шлюз докачает файл в фоне."""
    from app.core import storage

    now = datetime.now(UTC)
    for item in items:
        body = item.get("body")
        file_name = (item.get("file_name") or "file")[:255]
        status = item.get("status") or (
            AttachmentStatus.READY.value if body else AttachmentStatus.PENDING.value
        )
        key: str | None = None
        if status == AttachmentStatus.READY.value and body:
            key = (
                f"incoming/{now:%Y}/{now:%m}/{uuid4().hex}"
                f"{PurePosixPath(file_name).suffix.lower()[:16]}"
            )
            try:
                await storage.put_object(key, body, filename=file_name)
            except Exception:
                log.exception("Файл %s из диалога %s не сохранён", file_name, conversation.id)
                key = None
                status = AttachmentStatus.PENDING.value
        elif status == AttachmentStatus.READY.value:
            status = AttachmentStatus.PENDING.value
        extra = {
            name: item[name]
            for name in ("title", "performer", "emoji", "error", "paused")
            if item.get(name)
        }
        if status == AttachmentStatus.PENDING.value and item.get("source"):
            extra["source"] = item["source"]
            extra["attempts"] = 0
        db.add(
            Attachment(
                message_id=message.id,
                conversation_id=conversation.id,
                client_id=conversation.client_id,
                file_name=file_name,
                mime_type=item.get("mime_type"),
                size_bytes=len(body) if key and body else int(item.get("size") or 0),
                storage_key=key,
                status=status,
                kind=item.get("kind"),
                waveform=item.get("waveform"),
                telegram_file_id=item.get("telegram_file_id"),
                width=item.get("width"),
                height=item.get("height"),
                duration_sec=item.get("duration_sec"),
                meta=extra or None,
            )
        )
    await db.flush()


async def _apply_edit(
    db: AsyncSession, conversation: Conversation, message: Message, event: InboundMessage
) -> None:
    """Клиент (или менеджер с телефона) поправил сообщение в Telegram.

    Пустой текст правки не стирает наш: Telegram присылает «правку» и когда
    меняются только реакции или превью ссылки — текст при этом не приходит.
    """
    new_text = (event.text or "").strip() or None
    if new_text is None or new_text == (message.text or "").strip():
        return
    message.text = event.text
    message.edited_at = event.edit_date or datetime.now(UTC)
    if event.live:
        await _publish_updated(db, conversation, message)


async def _publish_updated(db: AsyncSession, conversation: Conversation, message: Message) -> None:
    from app.services.message_service import to_out

    await db.flush()
    audience = await conversation_audience(db, conversation.id)
    await emit(
        "message.updated",
        {
            "conversation_id": conversation.id,
            "message": to_out(message).model_dump(mode="json"),
        },
        audience,
    )


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


async def mark_deleted(
    db: AsyncSession, account: TelegramAccount, tg_chat_id: int | None, tg_ids: list[int]
) -> int:
    """Сообщения удалили в Telegram (клиент или менеджер с телефона).

    В CRM они остаются — это история работы с клиентом, — но с пометкой
    «удалено в Telegram»: менеджер не должен думать, что клиент это видит.
    Номера сообщений в личных чатах уникальны в пределах аккаунта, поэтому
    хватает аккаунта, даже когда Telegram не называет чат.
    """
    if not tg_ids:
        return 0
    conditions = [Conversation.account_id == account.id]
    if tg_chat_id is not None:
        conditions.append(Conversation.tg_chat_id == tg_chat_id)
    ids_array = cast(tg_ids, ARRAY(BigInteger))
    rows = (
        await db.execute(
            select(Message, Conversation)
            .join(Conversation, Conversation.id == Message.conversation_id)
            .where(
                *conditions,
                or_(
                    Message.tg_message_id.in_(tg_ids),
                    Message.tg_extra_ids.overlap(ids_array),
                ),
            )
        )
    ).all()
    now = datetime.now(UTC).isoformat()
    changed: list[tuple[Message, Conversation]] = []
    for message, conversation in rows:
        meta = dict(message.meta or {})
        if meta.get("deleted_in_telegram_at"):
            continue
        meta["deleted_in_telegram_at"] = now
        message.meta = meta
        changed.append((message, conversation))
    for message, conversation in changed:
        await _publish_updated(db, conversation, message)
    return len(changed)


async def mark_incoming_read(
    db: AsyncSession, account: TelegramAccount, tg_chat_id: int, max_id: int
) -> bool:
    """Сообщения клиента прочитали на телефоне — в CRM они тоже прочитаны.

    Непрочитанными остаются только те, что новее прочитанного в Telegram.
    Если прочитано всё, гаснет и напоминание «ждёт ответа» (ТЗ п. 2.1: плашка
    срабатывает один раз и гаснет, когда менеджер увидел чат) — сам счётчик
    «ждут ответа» остаётся, клиенту ведь ещё не ответили.
    """
    conversation = await db.scalar(
        select(Conversation).where(
            Conversation.account_id == account.id, Conversation.tg_chat_id == tg_chat_id
        )
    )
    if conversation is None:
        return False
    remaining = int(
        await db.scalar(
            select(func.count())
            .select_from(Message)
            .where(
                Message.conversation_id == conversation.id,
                Message.direction == Direction.IN,
                Message.deleted_at.is_(None),
                Message.tg_message_id > max_id,
            )
        )
        or 0
    )
    changed = False
    unread = min(conversation.unread_count, remaining)
    if unread != conversation.unread_count:
        conversation.unread_count = unread
        changed = True
    awaiting = conversation.awaiting_reply_since
    seen = conversation.awaiting_seen_at
    if remaining == 0 and awaiting is not None and (seen is None or seen < awaiting):
        conversation.awaiting_seen_at = datetime.now(UTC)
        changed = True
    if changed:
        from app.services import conversation_service

        await db.flush()
        detail = await conversation_service.build_detail(db, conversation.id)
        await conversation_service.emit_updated(db, detail)
    return changed
