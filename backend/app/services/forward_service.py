"""Пересылка сообщений из одного чата CRM в другой.

Как именно уйдёт в Telegram, решается здесь:

- **тот же аккаунт Telegram** и у всех сообщений есть номер в Telegram →
  настоящая пересылка (`messages.forwardMessages`): быстро, без повторной
  загрузки файлов, альбомы остаются альбомами, «скрыть отправителя» — флаг
  `drop_author`;
- **разные аккаунты** (или сообщение так и не ушло в Telegram) → копия: текст
  и файлы отправляются заново от имени целевого аккаунта. Переслать «чужое»
  сообщение другого аккаунта Telegram технически не позволяет — у каждого
  аккаунта своя переписка.

В обоих случаях в целевом чате появляются обычные исходящие сообщения CRM —
по одному на каждое пересланное, с отметкой, откуда переслано (видна только в
CRM). Файлы физически не копируются: новые строки вложений ссылаются на тот же
объект в хранилище — он неизменяем. Текст и файлы кладутся в задание даже при
настоящей пересылке: если Telegram её отклонит, шлюз отправит копию.
"""

import secrets
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Invalid, NotFound
from app.models import (
    Attachment,
    AttachmentStatus,
    AuthorKind,
    Direction,
    Message,
    MessageKind,
    MessageStatus,
    Outbox,
    OutboxStatus,
    User,
)
from app.realtime.events import conversation_audience, emit
from app.schemas.message import ForwardRequest, ForwardResult
from app.services import conversation_service
from app.services.audit import log_event
from app.services.message_service import mark_replied, to_out

# Что из «нетекстового» переносится в копию: карточка контакта или карты в CRM
# остаётся карточкой. «Переслано от» и отметки удаления — свойства исходного
# сообщения, не нового.
_CARRIED_META = ("contact", "location", "poll")


def _copy_problem(message: Message) -> str | None:
    """Почему сообщение нельзя отправить копией (для настоящей пересылки не важно)."""
    if (message.meta or {}).get("unsupported"):
        return "Такое сообщение можно переслать только внутри того же аккаунта Telegram"
    for attachment in message.attachments:
        if attachment.deleted_at is not None:
            continue
        if attachment.status == AttachmentStatus.PENDING.value:
            return (
                f"Файл «{attachment.file_name}» ещё загружается из Telegram — "
                "переслать его в другой аккаунт можно после загрузки"
            )
        if attachment.status != AttachmentStatus.READY.value or not attachment.storage_key:
            return (
                f"Файла «{attachment.file_name}» нет в CRM — переслать его можно только "
                "внутри того же аккаунта Telegram"
            )
    return None


async def forward(
    db: AsyncSession, user: User, target_conversation_id: int, request: ForwardRequest
) -> ForwardResult:
    target = await conversation_service.load_visible(db, user, target_conversation_id)
    source = await conversation_service.load_visible(db, user, request.source_conversation_id)
    if target.is_blocked_by_client:
        raise Invalid("Клиент заблокировал этот номер — сообщения ему не доходят")

    ids = list(dict.fromkeys(request.message_ids))
    rows = (
        (
            await db.execute(
                select(Message)
                .where(
                    Message.id.in_(ids),
                    Message.conversation_id == source.id,
                    Message.deleted_at.is_(None),
                )
                .order_by(Message.created_at, Message.id)
            )
        )
        .unique()
        .scalars()
        .all()
    )
    # Чужое сообщение (из другого чата) для этого запроса не существует — 404,
    # как и везде: иначе по ответу можно было бы угадывать чужие id.
    if len(rows) != len(ids):
        raise NotFound("Сообщение не найдено")
    if any(message.is_internal for message in rows):
        raise Invalid("Служебные заметки не пересылаются — клиенту их не видно")
    if any(not (message.text or message.attachments) for message in rows):
        raise Invalid("Пустое сообщение переслать нельзя")

    native = source.account_id == target.account_id and all(
        message.tg_message_id and message.status != MessageStatus.FAILED for message in rows
    )
    if not native:
        for message in rows:
            if problem := _copy_problem(message):
                raise Invalid(problem)

    comment = (request.comment or "").strip() or None
    now = datetime.now(UTC)
    batch_id = uuid.uuid4().hex
    created: list[tuple[Message, list[Attachment]]] = []

    if comment:
        note = Message(
            conversation_id=target.id,
            direction=Direction.OUT,
            author_kind=AuthorKind.MANAGER,
            author=user,
            kind=MessageKind.TEXT,
            text=comment,
            status=MessageStatus.QUEUED,
            random_id=secrets.randbits(63),
            created_at=now,
        )
        db.add(note)
        await db.flush()
        db.add(
            Outbox(
                message_id=note.id,
                account_id=target.account_id,
                conversation_id=target.id,
                payload={"text": comment, "attachment_ids": []},
                status=OutboxStatus.PENDING,
                next_attempt_at=now,
            )
        )
        created.append((note, []))

    client_name = source.client.name if source.client else None
    for original in rows:
        meta = {key: value for key, value in (original.meta or {}).items() if key in _CARRIED_META}
        meta["forwarded"] = {
            "conversation_id": source.id,
            "message_id": original.id,
            "client_name": client_name,
            # Правда, а не флаг из запроса: между аккаунтами всегда уходит
            # копия без подписи «Переслано от …», независимо от положения
            # переключателя в CRM — переключатель работает только внутри
            # одного аккаунта (drop_author настоящей пересылки).
            "hide_sender": (not native) or request.hide_sender,
            "native": native,
        }
        copy = Message(
            conversation_id=target.id,
            direction=Direction.OUT,
            author_kind=AuthorKind.MANAGER,
            author=user,
            kind=original.kind if original.kind != MessageKind.SERVICE else MessageKind.TEXT,
            text=original.text,
            status=MessageStatus.QUEUED,
            random_id=secrets.randbits(63),
            created_at=now,
            meta=meta,
        )
        db.add(copy)
        await db.flush()

        attachments: list[Attachment] = []
        for item in original.attachments:
            if item.deleted_at is not None:
                continue
            attachment = Attachment(
                message_id=copy.id,
                conversation_id=target.id,
                # Отправленное клиенту — его материалы: вкладка «Материалы»
                # целевого клиента покажет, что ему переслали.
                client_id=target.client_id,
                file_name=item.file_name,
                mime_type=item.mime_type,
                size_bytes=item.size_bytes,
                storage_key=item.storage_key,
                telegram_file_id=item.telegram_file_id,
                width=item.width,
                height=item.height,
                duration_sec=item.duration_sec,
                kind=item.kind,
                waveform=item.waveform,
                thumb_key=item.thumb_key,
                status=item.status,
                meta=item.meta,
            )
            db.add(attachment)
            attachments.append(attachment)
        await db.flush()

        payload: dict = {"text": original.text, "attachment_ids": [a.id for a in attachments]}
        if native:
            payload["forward"] = {
                "batch_id": batch_id,
                "from_chat_id": source.tg_chat_id,
                "tg_message_ids": sorted(
                    {int(original.tg_message_id), *(int(v) for v in original.tg_extra_ids or [])}
                ),
                "drop_author": request.hide_sender,
            }
        db.add(
            Outbox(
                message_id=copy.id,
                account_id=target.account_id,
                conversation_id=target.id,
                payload=payload,
                status=OutboxStatus.PENDING,
                next_attempt_at=now,
            )
        )
        created.append((copy, attachments))

    await mark_replied(db, target, user, now)
    await log_event(
        db,
        action="message.forwarded",
        entity_type="conversation",
        entity_id=target.id,
        actor=user,
        after={
            "source_conversation_id": source.id,
            "message_ids": [message.id for message in rows],
            "created": [message.id for message, _ in created],
            "mode": "native" if native else "copy",
            "hide_sender": request.hide_sender,
        },
    )
    await db.commit()

    audience = await conversation_audience(db, target.id)
    result = [to_out(message, attachments) for message, attachments in created]
    for item in result:
        await emit(
            "message.new",
            {"conversation_id": target.id, "message": item.model_dump(mode="json")},
            audience,
        )
    await emit("counters.updated", {"conversation_id": target.id, "stale": True}, audience)
    return ForwardResult(
        conversation_id=target.id, messages=result, mode="native" if native else "copy"
    )
