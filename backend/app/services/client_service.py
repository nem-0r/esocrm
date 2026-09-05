"""Бизнес-логика раздела «Клиенты»: список, карточка, заметки, материалы, выгрузка.

Видимость клиента менеджеру — правило 5: клиент виден, только если у него есть хотя бы
один диалог на аккаунте, назначенном менеджеру. Если карточка не видна — 404, а не 403.

Внутри уже открытой карточки видимость сужается ещё раз, до D-26: чаты, суммы оплат,
дата последнего контакта и счётчик диалогов считаются только по тем диалогам клиента,
которые видит именно этот сотрудник (свои и ничейные), а не по всем диалогам клиента
на аккаунтах, к которым у него в принципе есть доступ — иначе руководитель менеджера
видел бы в карточке чаты и суммы оплат из диалогов, закреплённых за коллегой.
"""

import base64
import csv
import io
import re
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import String, and_, case, cast, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import conversation_scope_orm, visible_account_ids
from app.core.errors import Invalid, NotFound
from app.models import (
    Attachment,
    Client,
    Conversation,
    Deal,
    DealStatus,
    Message,
    Note,
    TelegramAccount,
    User,
)
from app.models.client import zodiac_for
from app.schemas.client import (
    ClientAccountBrief,
    ClientCard,
    ClientConversationRow,
    ClientListRow,
    ClientResponsibleBrief,
    ClientUpdate,
    MaterialAuthor,
    MaterialRow,
    NoteAuthor,
    NoteRow,
)
from app.schemas.common import CursorPage, decode_cursor, encode_cursor
from app.services.audit import log_event

PHONE_RE = re.compile(r"^[0-9+\-\s]+$")
MAX_LIMIT = 200


def _clamp_limit(limit: int) -> int:
    return max(1, min(limit, MAX_LIMIT))


def _display_name(
    display_name: str | None,
    first: str | None,
    last: str | None,
    telegram_id: int,
    username: str | None = None,
) -> str:
    """Повторяет лестницу `Client.name`: имя менеджера, имя из Telegram,
    @username, публичный id. Держать в двух местах пришлось потому, что список
    строится сырым запросом, без объектов модели."""
    if display_name:
        return display_name
    parts = [p for p in (first, last) if p]
    if parts:
        return " ".join(parts)
    return f"@{username}" if username else str(telegram_id)


def _validate_phone(phone: str) -> str:
    phone = phone.strip()
    if not phone or not PHONE_RE.fullmatch(phone):
        raise Invalid("Проверьте формат телефона")
    digits = re.sub(r"\D", "", phone)
    if not (7 <= len(digits) <= 15):
        raise Invalid("Проверьте формат телефона")
    return phone


async def _ensure_visible(db: AsyncSession, user: User, client_id: int) -> Client:
    client = await db.scalar(
        select(Client).where(Client.id == client_id, Client.deleted_at.is_(None))
    )
    if client is None:
        raise NotFound("Клиент не найден")

    account_ids = await visible_account_ids(db, user)
    if account_ids is not None:
        # Клиент виден, пока у сотрудника есть хотя бы один его диалог.
        # Диалоги, переданные другому менеджеру, в счёт не идут.
        visible = await db.scalar(
            select(
                exists().where(
                    Conversation.client_id == client_id,
                    *conversation_scope_orm(user, account_ids, Conversation),
                )
            )
        )
        if not visible:
            raise NotFound("Клиент не найден")
    return client


def _encode_list_cursor(sort: str, row: Any) -> str:
    # paid_amount приходит из SQL SUM() как Decimal — у круглых сумм str(Decimal)
    # может отдать научную нотацию ("3.16E+6"), которую _decode_list_cursor не
    # распарсит обратно как int. Копейки всегда целые — приводим явно.
    value = (
        row.last_contact_at.isoformat() if sort == "last_contact" else str(int(row.paid_amount))
    )
    raw = f"{value}|{row.id}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def _decode_list_cursor(sort: str, cursor: str | None) -> tuple[Any, int] | None:
    if not cursor:
        return None
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        value_str, id_str = raw.rsplit("|", 1)
        value: Any = datetime.fromisoformat(value_str) if sort == "last_contact" else int(value_str)
        return value, int(id_str)
    except (ValueError, UnicodeDecodeError):
        return None


def _deal_agg_subquery(user: User, account_ids: list[int] | None):
    """Суммы по сделкам клиента — только по сделкам, чей диалог виден этому
    сотруднику (D-26). Без этого условия менеджер видел бы в списке/выгрузке
    суммы оплат из чужих, закреплённых за коллегой диалогов — то же самое
    правило, что уже применено к списку диалогов в `get_client_card`."""
    stmt = select(
        Deal.client_id.label("client_id"),
        func.coalesce(
            func.sum(case((Deal.status == DealStatus.PAID, Deal.total_amount), else_=0)), 0
        ).label("paid_amount"),
        func.count(case((Deal.status == DealStatus.PAID, 1))).label("paid_count"),
        func.coalesce(
            func.sum(case((Deal.status == DealStatus.AWAITING, Deal.total_amount), else_=0)), 0
        ).label("awaiting_amount"),
    ).select_from(Deal)
    if account_ids is not None:
        stmt = stmt.join(Conversation, Conversation.id == Deal.conversation_id).where(
            *conversation_scope_orm(user, account_ids, Conversation)
        )
    return stmt.group_by(Deal.client_id).subquery()


async def list_clients(
    db: AsyncSession,
    user: User,
    *,
    q: str | None,
    sort: str,
    cursor: str | None,
    limit: int,
) -> CursorPage[ClientListRow]:
    """Список клиентов. Суммы и даты последнего контакта — сгруппированными подзапросами,
    не по одному запросу на строку: список обязан жить на 50 000 клиентов."""
    sort = sort if sort in ("last_contact", "amount") else "last_contact"
    limit = _clamp_limit(limit)

    # Считается заранее: суммы и даты последнего контакта в списке обязаны
    # опираться на те же диалоги, что и сама видимость клиента (D-26) —
    # иначе менеджер видел бы в цифрах чужие, закреплённые за коллегой чаты.
    account_ids = await visible_account_ids(db, user)

    conv_agg_stmt = select(
        Conversation.client_id.label("client_id"),
        func.max(Conversation.last_message_at).label("last_contact_at"),
        func.count(Conversation.id).label("conversations_count"),
    )
    if account_ids is not None:
        conv_agg_stmt = conv_agg_stmt.where(
            *conversation_scope_orm(user, account_ids, Conversation)
        )
    conv_agg = conv_agg_stmt.group_by(Conversation.client_id).subquery()
    deal_agg = _deal_agg_subquery(user, account_ids)

    inner = (
        select(
            Client.id.label("id"),
            Client.display_name.label("display_name"),
            Client.tg_first_name.label("tg_first_name"),
            Client.tg_last_name.label("tg_last_name"),
            Client.telegram_id.label("telegram_id"),
            Client.phone.label("phone"),
            Client.tg_username.label("tg_username"),
            Client.birth_date.label("birth_date"),
            Client.birth_time.label("birth_time"),
            Client.birth_time_approx.label("birth_time_approx"),
            Client.birth_city.label("birth_city"),
            Client.zodiac_sign.label("zodiac_sign"),
            Client.first_contact_at.label("first_contact_at"),
            Client.created_at.label("created_at"),
            func.coalesce(
                conv_agg.c.last_contact_at, Client.first_contact_at
            ).label("last_contact_at"),
            func.coalesce(conv_agg.c.conversations_count, 0).label("conversations_count"),
            func.coalesce(deal_agg.c.paid_amount, 0).label("paid_amount"),
            func.coalesce(deal_agg.c.paid_count, 0).label("paid_count"),
            func.coalesce(deal_agg.c.awaiting_amount, 0).label("awaiting_amount"),
        )
        .select_from(Client)
        .outerjoin(conv_agg, conv_agg.c.client_id == Client.id)
        .outerjoin(deal_agg, deal_agg.c.client_id == Client.id)
        .where(Client.deleted_at.is_(None))
    )

    if q:
        pattern = f"%{q.strip()}%"
        inner = inner.where(
            or_(
                Client.display_name.ilike(pattern),
                Client.tg_first_name.ilike(pattern),
                Client.tg_last_name.ilike(pattern),
                Client.phone.ilike(pattern),
                cast(Client.id, String).ilike(pattern),
            )
        )

    if account_ids is not None:
        inner = inner.where(
            exists().where(
                Conversation.client_id == Client.id,
                *conversation_scope_orm(user, account_ids, Conversation),
            )
        )

    sub = inner.subquery()
    sort_col = sub.c.last_contact_at if sort == "last_contact" else sub.c.paid_amount
    outer = select(sub).order_by(sort_col.desc(), sub.c.id.desc())

    decoded = _decode_list_cursor(sort, cursor)
    if decoded is not None:
        value, last_id = decoded
        outer = outer.where(or_(sort_col < value, and_(sort_col == value, sub.c.id < last_id)))

    rows = (await db.execute(outer.limit(limit + 1))).all()
    has_more = len(rows) > limit
    rows = rows[:limit]

    items = [
        ClientListRow(
            id=r.id,
            name=_display_name(
                r.display_name, r.tg_first_name, r.tg_last_name, r.telegram_id, r.tg_username
            ),
            phone=r.phone,
            tg_username=r.tg_username,
            birth_date=r.birth_date,
            birth_time=r.birth_time,
            birth_time_approx=r.birth_time_approx,
            birth_city=r.birth_city,
            zodiac_sign=r.zodiac_sign,
            data_complete=bool(r.birth_date and r.birth_time and r.birth_city),
            first_contact_at=r.first_contact_at,
            created_at=r.created_at,
            last_contact_at=r.last_contact_at,
            paid_amount=r.paid_amount,
            paid_count=r.paid_count,
            awaiting_amount=r.awaiting_amount,
            conversations_count=r.conversations_count,
        )
        for r in rows
    ]
    next_cursor = _encode_list_cursor(sort, rows[-1]) if has_more and rows else None
    return CursorPage(items=items, next_cursor=next_cursor)


async def get_client_card(db: AsyncSession, user: User, client_id: int) -> ClientCard:
    client = await _ensure_visible(db, user, client_id)

    # Карточка показывает только те диалоги клиента, которые этому сотруднику
    # доступны. Без этого менеджер видел в карточке чаты чужих аккаунтов и этапа
    # «Бот»: сами переписки открыть было нельзя (404), но список вёл в никуда —
    # ссылка, которая не работает, и утечка того, где ещё клиент общается.
    account_ids = await visible_account_ids(db, user)
    scope = [
        Conversation.client_id == client_id,
        *conversation_scope_orm(user, account_ids, Conversation),
    ]

    last_contact_at, conversations_count = (
        await db.execute(
            select(func.max(Conversation.last_message_at), func.count(Conversation.id)).where(
                *scope
            )
        )
    ).one()

    # Та же видимость, что у списка диалогов выше: сумма оплат считается
    # только по сделкам, чей диалог виден этому сотруднику — иначе менеджер
    # видел бы в шапке карточки суммы из чужих, закреплённых за коллегой чатов.
    paid_amount, paid_count, awaiting_amount = (
        await db.execute(
            select(
                func.coalesce(
                    func.sum(case((Deal.status == DealStatus.PAID, Deal.total_amount), else_=0)), 0
                ),
                func.count(case((Deal.status == DealStatus.PAID, 1))),
                func.coalesce(
                    func.sum(
                        case((Deal.status == DealStatus.AWAITING, Deal.total_amount), else_=0)
                    ),
                    0,
                ),
            )
            .select_from(Deal)
            .join(Conversation, Conversation.id == Deal.conversation_id)
            .where(*scope)
        )
    ).one()

    conv_rows = (
        await db.execute(
            select(Conversation, TelegramAccount, User)
            .join(TelegramAccount, TelegramAccount.id == Conversation.account_id)
            .outerjoin(User, User.id == Conversation.responsible_id)
            .where(*scope)
            .order_by(Conversation.last_message_at.desc().nullslast())
        )
    ).all()

    conversations = [
        ClientConversationRow(
            id=conv.id,
            account=ClientAccountBrief(id=acc.id, title=acc.title, funnel_stage=acc.funnel_stage),
            responsible=(
                ClientResponsibleBrief(id=resp.id, full_name=resp.full_name) if resp else None
            ),
            last_message_at=conv.last_message_at,
            unread_count=conv.unread_count,
        )
        for conv, acc, resp in conv_rows
    ]

    # ТЗ п. 5.2. Число аккаунтов считаем по диалогам, которые видит этот
    # пользователь: менеджеру незачем знать, что клиент есть ещё где-то.
    accounts_count = len({acc.id for _, acc, _ in conv_rows})
    created_via = None
    if client.created_via_account_id is not None:
        origin = await db.get(TelegramAccount, client.created_via_account_id)
        if origin is not None:
            created_via = ClientAccountBrief(
                id=origin.id, title=origin.title, funnel_stage=origin.funnel_stage
            )

    return ClientCard(
        id=client.id,
        name=client.name,
        phone=client.phone,
        tg_username=client.tg_username,
        birth_date=client.birth_date,
        birth_time=client.birth_time,
        birth_time_approx=client.birth_time_approx,
        birth_city=client.birth_city,
        zodiac_sign=client.zodiac_sign,
        data_complete=client.data_complete,
        first_contact_at=client.first_contact_at,
        created_at=client.created_at,
        last_contact_at=last_contact_at,
        paid_amount=paid_amount,
        paid_count=paid_count,
        awaiting_amount=awaiting_amount,
        conversations_count=conversations_count,
        source_code=client.source_code,
        source=client.source,
        tg_first_name=client.tg_first_name,
        tg_last_name=client.tg_last_name,
        display_name=client.display_name,
        created_via_account=created_via,
        accounts_count=accounts_count,
        pdn_consent_at=client.pdn_consent_at,
        pdn_consent_version=client.pdn_consent_version,
        marketing_consent=client.marketing_consent,
        marketing_consent_at=client.marketing_consent_at,
        conversations=conversations,
    )


async def update_client(
    db: AsyncSession, user: User, client_id: int, patch: ClientUpdate
) -> ClientCard:
    client = await _ensure_visible(db, user, client_id)
    fields = patch.model_fields_set

    new_phone = _validate_phone(patch.phone) if "phone" in fields and patch.phone else None

    before: dict[str, Any] = {}
    after: dict[str, Any] = {}

    def _apply(field: str, value: Any) -> None:
        current = getattr(client, field)
        if current == value:
            return
        before[field] = current.isoformat() if hasattr(current, "isoformat") else current
        setattr(client, field, value)
        after[field] = value.isoformat() if hasattr(value, "isoformat") else value

    if "display_name" in fields:
        _apply("display_name", patch.display_name)
    if "phone" in fields:
        _apply("phone", new_phone)
    if "birth_city" in fields:
        _apply("birth_city", patch.birth_city)
    if "birth_time" in fields:
        _apply("birth_time", patch.birth_time)
        # Точное время вытесняет приблизительное — держать оба значит хранить
        # два ответа на один вопрос и не знать, какому верить.
        if patch.birth_time is not None:
            _apply("birth_time_approx", None)
    if "birth_time_approx" in fields:
        _apply("birth_time_approx", patch.birth_time_approx)
    if "birth_date" in fields:
        _apply("birth_date", patch.birth_date)
        client.zodiac_sign = zodiac_for(patch.birth_date)
        after["zodiac_sign"] = client.zodiac_sign
    if "source" in fields:
        _apply("source", patch.source)
    if "marketing_consent" in fields and patch.marketing_consent is not None:
        _apply("marketing_consent", patch.marketing_consent)
        # Дата — след того, когда согласие изменилось; отзыв тоже нужно датировать.
        _apply("marketing_consent_at", datetime.now(UTC))

    if before:
        await log_event(
            db,
            action="client.update",
            entity_type="client",
            entity_id=client.id,
            actor=user,
            before=before,
            after=after,
        )
        await db.commit()

    return await get_client_card(db, user, client_id)


async def list_notes(
    db: AsyncSession, user: User, client_id: int, *, cursor: str | None, limit: int
) -> CursorPage[NoteRow]:
    await _ensure_visible(db, user, client_id)
    limit = _clamp_limit(limit)
    after_id = decode_cursor(cursor)

    stmt = (
        select(Note, User)
        .join(User, User.id == Note.author_id)
        .where(Note.client_id == client_id, Note.deleted_at.is_(None))
        .order_by(Note.id.desc())
    )
    if after_id is not None:
        stmt = stmt.where(Note.id < after_id)

    rows = (await db.execute(stmt.limit(limit + 1))).all()
    has_more = len(rows) > limit
    rows = rows[:limit]

    items = [
        NoteRow(
            id=note.id,
            text=note.text,
            author=NoteAuthor(
                id=author.id, full_name=author.full_name, avatar_color=author.avatar_color
            ),
            created_at=note.created_at,
        )
        for note, author in rows
    ]
    next_cursor = encode_cursor(items[-1].id) if has_more and items else None
    return CursorPage(items=items, next_cursor=next_cursor)


async def create_note(db: AsyncSession, user: User, client_id: int, text: str) -> NoteRow:
    await _ensure_visible(db, user, client_id)

    note = Note(client_id=client_id, author_id=user.id, text=text)
    db.add(note)
    await db.flush()

    await log_event(
        db,
        action="note.create",
        entity_type="note",
        entity_id=note.id,
        actor=user,
        after={"text": text},
    )
    await db.commit()

    return NoteRow(
        id=note.id,
        text=note.text,
        author=NoteAuthor(id=user.id, full_name=user.full_name, avatar_color=user.avatar_color),
        created_at=note.created_at,
    )


async def delete_note(db: AsyncSession, user: User, client_id: int, note_id: int) -> None:
    await _ensure_visible(db, user, client_id)

    note = await db.scalar(
        select(Note).where(
            Note.id == note_id, Note.client_id == client_id, Note.deleted_at.is_(None)
        )
    )
    # Чужая заметка для менеджера — тоже 404: не подтверждаем даже её существование.
    if note is None or note.author_id != user.id:
        raise NotFound("Заметка не найдена")

    note.deleted_at = datetime.now(UTC)
    await log_event(db, action="note.delete", entity_type="note", entity_id=note.id, actor=user)
    await db.commit()


async def list_materials(
    db: AsyncSession, user: User, client_id: int, *, cursor: str | None, limit: int
) -> CursorPage[MaterialRow]:
    await _ensure_visible(db, user, client_id)
    limit = _clamp_limit(limit)
    after_id = decode_cursor(cursor)

    # Материалы — те же переписки, только вложениями. Значит и видимость та же:
    # менеджеру не показываем файлы из чатов на чужих аккаунтах, иначе список
    # ведёт к файлам, которые всё равно не откроются.
    account_ids = await visible_account_ids(db, user)
    stmt = (
        select(Attachment, Message.direction, User)
        .join(Message, Message.id == Attachment.message_id)
        .join(Conversation, Conversation.id == Attachment.conversation_id)
        .outerjoin(User, User.id == Message.author_id)
        .where(Attachment.client_id == client_id, Attachment.deleted_at.is_(None))
        .order_by(Attachment.id.desc())
    )
    if account_ids is not None:
        stmt = stmt.where(*conversation_scope_orm(user, account_ids, Conversation))
    if after_id is not None:
        stmt = stmt.where(Attachment.id < after_id)

    rows = (await db.execute(stmt.limit(limit + 1))).all()
    has_more = len(rows) > limit
    rows = rows[:limit]

    items = [
        MaterialRow(
            id=att.id,
            file_name=att.file_name,
            mime_type=att.mime_type,
            size_bytes=att.size_bytes,
            url=f"/api/v1/files/{att.id}",
            created_at=att.created_at,
            direction=direction.value,
            author=MaterialAuthor(id=author.id, full_name=author.full_name) if author else None,
        )
        for att, direction, author in rows
    ]
    next_cursor = encode_cursor(items[-1].id) if has_more and items else None
    return CursorPage(items=items, next_cursor=next_cursor)


CSV_HEADER = [
    "ID",
    "Имя",
    "Телефон",
    "Telegram",
    "Дата рождения",
    "Время рождения",
    "Город",
    "Знак",
    "Первое обращение",
    "Оплачено (руб)",
    "Сделок оплачено",
    "Ждёт оплаты (руб)",
]
_DANGEROUS_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _csv_safe(value: str) -> str:
    """Excel исполняет значения, начинающиеся с этих символов, как формулу —
    экранируем апострофом. Не исправлено в прошлой версии продукта."""
    if value and value[0] in _DANGEROUS_PREFIXES:
        return "'" + value
    return value


def _rub(kopecks: int | None) -> str:
    """Копейки в рубли для выгрузки.

    Приведение к int обязательно: сумма приходит из SQL как Decimal,
    а формат «02d» такой тип не принимает и роняет выгрузку на середине файла.
    """
    rub, kop = divmod(int(kopecks or 0), 100)
    return f"{rub},{kop:02d}"


async def export_csv_rows(
    db: AsyncSession, user: User, date_from: date | None, date_to: date | None
) -> AsyncIterator[bytes]:
    """CSV построчно, без накопления в одну гигантскую строку. UTF-8 с BOM — иначе
    Excel на Windows ломает кириллицу. Разделитель `;` — под русскую локаль Excel."""
    today = date.today()
    date_from = date_from or (today - timedelta(days=365))
    date_to = date_to or today
    start = datetime.combine(date_from, datetime.min.time(), tzinfo=UTC)
    end = datetime.combine(date_to, datetime.min.time(), tzinfo=UTC) + timedelta(days=1)

    account_ids = await visible_account_ids(db, user)
    deal_agg = _deal_agg_subquery(user, account_ids)
    stmt = (
        select(
            Client.id,
            Client.display_name,
            Client.tg_first_name,
            Client.tg_last_name,
            Client.telegram_id,
            Client.phone,
            Client.tg_username,
            Client.birth_date,
            Client.birth_time,
            Client.birth_city,
            Client.zodiac_sign,
            Client.first_contact_at,
            func.coalesce(deal_agg.c.paid_amount, 0),
            func.coalesce(deal_agg.c.paid_count, 0),
            func.coalesce(deal_agg.c.awaiting_amount, 0),
        )
        .select_from(Client)
        .outerjoin(deal_agg, deal_agg.c.client_id == Client.id)
        .where(
            Client.deleted_at.is_(None),
            Client.first_contact_at >= start,
            Client.first_contact_at < end,
        )
        .order_by(Client.first_contact_at.desc())
    )

    if account_ids is not None:
        stmt = stmt.where(
            exists().where(
                Conversation.client_id == Client.id,
                *conversation_scope_orm(user, account_ids, Conversation),
            )
        )

    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";", lineterminator="\r\n")

    yield "﻿".encode()

    writer.writerow(CSV_HEADER)
    yield buffer.getvalue().encode()
    buffer.seek(0)
    buffer.truncate(0)

    result = await db.stream(stmt)
    async for row in result:
        (
            client_id,
            display_name,
            first,
            last,
            telegram_id,
            phone,
            tg_username,
            birth_date,
            birth_time,
            birth_city,
            zodiac_sign,
            first_contact_at,
            paid_amount,
            paid_count,
            awaiting_amount,
        ) = row
        writer.writerow(
            [
                client_id,
                _csv_safe(_display_name(display_name, first, last, telegram_id)),
                _csv_safe(phone or ""),
                _csv_safe(f"@{tg_username}" if tg_username else ""),
                birth_date.isoformat() if birth_date else "",
                birth_time.strftime("%H:%M") if birth_time else "",
                _csv_safe(birth_city or ""),
                zodiac_sign or "",
                first_contact_at.strftime("%Y-%m-%d %H:%M"),
                _rub(paid_amount),
                paid_count,
                _rub(awaiting_amount),
            ]
        )
        yield buffer.getvalue().encode()
        buffer.seek(0)
        buffer.truncate(0)
