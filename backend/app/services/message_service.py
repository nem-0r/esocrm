"""Переписка: чтение и отправка исходящих.

Исходящее пишется в одной транзакции с очередью `outbox`: интерфейс сразу
показывает сообщение с часиками, шлюз забирает строку и отправляет в Telegram.
Служебное сообщение в Telegram не уходит вовсе — оно сразу «отправлено»
и никакой очереди не создаёт.
"""

import secrets
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Invalid
from app.models import (
    Attachment,
    AuthorKind,
    Conversation,
    Direction,
    Message,
    MessageKind,
    MessageStatus,
    Outbox,
    OutboxStatus,
    User,
)
from app.realtime.events import conversation_audience, emit
from app.schemas.common import CursorPage
from app.schemas.message import (
    MAX_TEXT_LENGTH,
    AttachmentOut,
    MessageAuthor,
    MessageCreate,
    MessageOut,
    attachment_url,
)
from app.services import conversation_service
from app.services.audit import log_event

DEFAULT_LIMIT = 40
MAX_LIMIT = 100

# По типу файла выбирается вид сообщения: в списке чатов вместо пустоты
# будет «Фото» или «Файл», а шлюз знает, чем отправлять.
_MIME_KINDS: list[tuple[str, MessageKind]] = [
    ("image/", MessageKind.PHOTO),
    ("video/", MessageKind.VIDEO),
    ("audio/", MessageKind.VOICE),
]


def _kind_for(attachment: Attachment) -> MessageKind:
    mime = attachment.mime_type or ""
    for prefix, kind in _MIME_KINDS:
        if mime.startswith(prefix):
            return kind
    return MessageKind.DOCUMENT


def to_out(message: Message, attachments: list[Attachment] | None = None) -> MessageOut:
    """Сообщение в вид для API.

    `attachments` передаётся явно сразу после создания сообщения: обращение
    к связи у только что записанного объекта пытается сходить в базу изнутри
    уже открытой транзакции и падает с MissingGreenlet.
    """
    return MessageOut(
        id=message.id,
        conversation_id=message.conversation_id,
        direction=message.direction,
        author_kind=message.author_kind,
        author=MessageAuthor.model_validate(message.author) if message.author else None,
        kind=message.kind,
        text=message.text,
        is_internal=message.is_internal,
        status=message.status,
        error_text=message.error_text,
        created_at=message.created_at,
        sent_at=message.sent_at,
        read_at=message.read_at,
        edited_at=message.edited_at,
        reply_to_tg_id=message.reply_to_tg_id,
        attachments=[
            AttachmentOut(
                id=att.id,
                file_name=att.file_name,
                mime_type=att.mime_type,
                size_bytes=att.size_bytes,
                url=attachment_url(att.id),
                width=att.width,
                height=att.height,
                duration_sec=att.duration_sec,
            )
            for att in (message.attachments if attachments is None else attachments)
            if att.deleted_at is None
        ],
    )


async def list_messages(
    db: AsyncSession,
    user: User,
    conversation_id: int,
    *,
    cursor: str | None = None,
    limit: int = DEFAULT_LIMIT,
) -> CursorPage[MessageOut]:
    """Переписка сверху вниз: сначала свежие, дальше — по курсору."""
    await conversation_service.load_visible(db, user, conversation_id)
    limit = max(1, min(limit, MAX_LIMIT))
    stmt = select(Message).where(
        Message.conversation_id == conversation_id, Message.deleted_at.is_(None)
    )
    parsed = conversation_service.decode_cursor(cursor)
    if parsed:
        stmt = stmt.where(
            conversation_service.keyset_condition(Message.created_at, Message.id, parsed)
        )
    stmt = stmt.order_by(Message.created_at.desc(), Message.id.desc()).limit(limit + 1)

    rows = list((await db.execute(stmt)).unique().scalars().all())
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = (
        conversation_service.encode_cursor(rows[-1].created_at, rows[-1].id)
        if has_more and rows
        else None
    )
    return CursorPage[MessageOut](items=[to_out(m) for m in rows], next_cursor=next_cursor)


async def _bind_attachments(
    db: AsyncSession, conversation: Conversation, attachment_ids: list[int] | None
) -> list[Attachment]:
    """Вложения загружаются заранее, отправка их только привязывает к сообщению.

    Чужое вложение не привязывается: иначе файл из соседнего чата уехал бы клиенту.
    """
    ids = list(dict.fromkeys(attachment_ids or []))
    if not ids:
        return []
    rows = (
        (
            await db.execute(
                select(Attachment).where(
                    Attachment.id.in_(ids), Attachment.deleted_at.is_(None)
                )
            )
        )
        .scalars()
        .all()
    )
    found = {att.id: att for att in rows}
    if len(found) != len(ids):
        raise Invalid("Вложение не найдено")
    for att in rows:
        if att.conversation_id != conversation.id:
            raise Invalid("Вложение из другого чата")
        att.client_id = conversation.client_id
    return [found[att_id] for att_id in ids]


async def send_outgoing(
    db: AsyncSession,
    *,
    conversation: Conversation,
    author: User,
    text: str | None,
    kind: MessageKind = MessageKind.TEXT,
    is_internal: bool = False,
    attachment_ids: list[int] | None = None,
) -> Message:
    """Создать исходящее сообщение и поставить его в очередь отправки.

    Транзакцию не закрывает — коммитит вызывающий: сделка и её сообщение
    должны попасть в базу вместе или не попасть вовсе.
    """
    now = datetime.now(UTC)
    attachments = await _bind_attachments(db, conversation, attachment_ids)
    if kind == MessageKind.TEXT and attachments:
        kind = _kind_for(attachments[0])

    message = Message(
        conversation_id=conversation.id,
        direction=Direction.OUT,
        author_kind=AuthorKind.MANAGER,
        author=author,
        kind=kind,
        text=text,
        is_internal=is_internal,
        # Служебное никуда не уходит, поэтому сразу «отправлено».
        status=MessageStatus.SENT if is_internal else MessageStatus.QUEUED,
        sent_at=now if is_internal else None,
        # random_id — метка своего исходящего: по нему событие Telegram опознаётся
        # как отправленное менеджером, а не воронкой.
        random_id=None if is_internal else secrets.randbits(63),
        created_at=now,
    )
    message.attachments.extend(attachments)
    db.add(message)
    await db.flush()

    if not is_internal:
        db.add(
            Outbox(
                message_id=message.id,
                account_id=conversation.account_id,
                conversation_id=conversation.id,
                payload={"text": text, "attachment_ids": [att.id for att in attachments]},
                status=OutboxStatus.PENDING,
                next_attempt_at=now,
            )
        )

    conversation.last_message_at = now
    if not is_internal:
        conversation.last_manager_message_at = now
        # Ответ дан — таймер ожидания останавливается.
        conversation.awaiting_reply_since = None
        # Блокировка строки перед проверкой: без неё два менеджера, ответившие
        # в один и тот же ничейный чат почти одновременно, оба проходят
        # проверку «ответственного нет», и один тихо перетирает захват
        # другого — тот, кто реально ответил первым, теряет свой же чат.
        current_responsible = await db.scalar(
            select(Conversation.responsible_id)
            .where(Conversation.id == conversation.id)
            .with_for_update()
        )
        if current_responsible is None:
            # Кто первый ответил, тот и ведёт.
            conversation.responsible_id = author.id
            conversation.responsible_since = now

    await log_event(
        db,
        action="message.sent",
        entity_type="message",
        entity_id=message.id,
        actor=author,
        after={
            "conversation_id": conversation.id,
            "is_internal": is_internal,
            "kind": str(kind),
            "attachment_ids": [att.id for att in attachments],
        },
    )
    await db.flush()

    audience = await conversation_audience(db, conversation.id)
    await emit(
        "message.new",
        {
            "conversation_id": conversation.id,
            "message": to_out(message, attachments).model_dump(mode="json"),
        },
        audience,
    )
    await emit("counters.updated", {"conversation_id": conversation.id, "stale": True}, audience)
    return message


async def post_message(
    db: AsyncSession, user: User, conversation_id: int, payload: MessageCreate
) -> MessageOut:
    """Отправка из чата. Писать можно только в свой диалог — чужой не найден."""
    from app.services import file_service

    conversation = await conversation_service.load_visible(db, user, conversation_id)
    text = (payload.text or "").strip() or None
    attachment_ids = payload.attachment_ids or []
    uploads = payload.uploads or []
    if text is None and not attachment_ids and not uploads:
        raise Invalid("Нечего отправлять")
    if text is not None and len(text) > MAX_TEXT_LENGTH:
        raise Invalid("Сообщение длиннее 4096 символов")

    message = await send_outgoing(
        db,
        conversation=conversation,
        author=user,
        text=text,
        is_internal=payload.is_internal,
        attachment_ids=attachment_ids or None,
    )

    # Список вложений собираем сами и ни разу не читаем связь `message.attachments`:
    # у только что записанного объекта она не загружена, и обращение к ней
    # уходит в базу изнутри уже открытой транзакции — это падение, а не задержка.
    attached: list[Attachment] = []
    if attachment_ids:
        rows = await db.execute(select(Attachment).where(Attachment.id.in_(attachment_ids)))
        attached.extend(rows.scalars().all())

    # Свежезагруженные файлы превращаются в строки `attachments` здесь —
    # вместе с сообщением, в одной транзакции. Иначе в базе копились бы
    # вложения без владельца, а в очередь отправки ушло бы пустое сообщение.
    if uploads:
        created = await file_service.attach_uploads(
            db,
            message=message,
            conversation=conversation,
            client_id=conversation.client_id,
            uploads=[u.model_dump() for u in uploads],
        )
        if created and message.kind == MessageKind.TEXT:
            message.kind = _kind_for(created[0])
        attached.extend(created)

    if attached:
        await _refresh_outbox_payload(db, message, [a.id for a in attached])

    await db.commit()
    return to_out(message, attached)


async def _refresh_outbox_payload(
    db: AsyncSession, message: Message, attachment_ids: list[int]
) -> None:
    """Дописать вложения в задание на отправку, созданное до их привязки."""
    row = await db.scalar(select(Outbox).where(Outbox.message_id == message.id))
    if row is None:
        return
    payload = dict(row.payload or {})
    payload["attachment_ids"] = attachment_ids
    row.payload = payload
