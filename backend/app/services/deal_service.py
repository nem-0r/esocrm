"""Сделки (оплаты): список, сводка, карточка, изменение, отправка, оплата, отмена.

Смена статуса проверяется по ALLOWED_DEAL_TRANSITIONS — на переходе, а не на поле.
В прошлой версии запрет «оплаченную сделку менять нельзя» обходили сменой статуса.

Права: руководитель видит все сделки, менеджер — только сделки диалогов тех аккаунтов,
на которые он назначен. Чужая сделка для менеджера — 404, а не 403.
"""

from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import Select, case, distinct, exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import storage
from app.core.config import settings
from app.core.deps import conversation_scope_orm, visible_account_ids
from app.core.errors import Conflict, Invalid, NotFound
from app.models import (
    ActorKind,
    Conversation,
    Deal,
    DealEvent,
    DealEventKind,
    DealItem,
    DealStatus,
    Notification,
    NotificationKind,
    PaidSource,
    PaymentMethod,
    PaymentRequisite,
    User,
    UserRole,
)
from app.models.enums import ALLOWED_DEAL_TRANSITIONS
from app.realtime.events import emit, emit_to_conversation
from app.schemas.common import decode_cursor, encode_cursor
from app.schemas.deal import DealCreate, DealItemIn, DealUpdate, detail_payload, row_payload
from app.services import robokassa
from app.services.audit import log_event
from app.services.message_service import send_outgoing
from app.services.money import MoneyError, format_rubles, validate_amount
from app.services.settings_service import get_all as settings_get_all
from app.services.settings_service import get_value
from app.services.worktime import day_bounds, local_zone

# Провайдера ещё нет, а кнопок-обманок мы не отдаём.
# При реализации Робокассы: сообщение клиенту обязано начинаться с deal.intro_text,
# как invoice_text() делает сегодня для реквизитов (ТЗ п. 4.5) —
# см. docs/11-payments-architecture.md, раздел 3.
LINK_NOT_READY = "Оплата по ссылке появится после подключения Робокассы"
FROZEN = "Оплаченную или отменённую сделку изменить нельзя"
TRANSITION_ERRORS: dict[DealStatus, str] = {
    DealStatus.AWAITING: "Отправить в чат можно только черновик сделки",
    DealStatus.PAID: "Оплату подтверждают только у сделки, ожидающей оплаты",
    DealStatus.CANCELLED: FROZEN,
}


def _ensure_transition(deal: Deal, target: DealStatus) -> None:
    if target not in ALLOWED_DEAL_TRANSITIONS[deal.status]:
        raise Conflict(TRANSITION_ERRORS[target], current_status=deal.status.value)


def _validated_items(items: list[DealItemIn]) -> list[tuple[str, int]]:
    if not items:
        raise Invalid("Добавьте хотя бы одну позицию")
    result: list[tuple[str, int]] = []
    for item in items:
        name = (item.name or "").strip()
        if not name or len(name) > 255:
            raise Invalid("Название услуги должно быть от 1 до 255 символов")
        try:
            amount = validate_amount(item.amount)
        except MoneyError as exc:
            raise Invalid(str(exc)) from exc
        result.append((name, amount))
    return result


def _replace_items(deal: Deal, items: list[tuple[str, int]]) -> None:
    """Состав сделки заменяется целиком, сумма пересчитывается из позиций."""
    deal.items = [
        DealItem(name=name, amount=amount, position=position)
        for position, (name, amount) in enumerate(items)
    ]
    deal.total_amount = sum(amount for _, amount in items)


async def _record(
    db: AsyncSession, deal: Deal, user: User, kind: DealEventKind, *, action: str,
    comment: str | None = None, data: dict[str, Any] | None = None,
    before: dict[str, Any] | None = None, after: dict[str, Any] | None = None,
) -> None:
    """Строка «Журнала» и запись аудита — в одной транзакции с самим изменением."""
    db.add(DealEvent(deal_id=deal.id, actor_id=user.id, kind=kind, comment=comment, data=data))
    await log_event(
        db, action=action, entity_type="deal", entity_id=deal.id, actor=user,
        before=before, after=after,
    )


async def _detail(db: AsyncSession, deal: Deal) -> dict[str, Any]:
    events = await db.execute(
        select(DealEvent, User)
        .outerjoin(User, User.id == DealEvent.actor_id)
        .where(DealEvent.deal_id == deal.id)
        .order_by(DealEvent.created_at, DealEvent.id)
    )
    sold_by = await db.get(User, deal.sold_by_id)
    return detail_payload(deal, sold_by, list(events.all()))


async def _commit_and_emit(db: AsyncSession, deal: Deal) -> dict[str, Any]:
    await db.commit()
    # `updated_at` пересчитывает база, и после записи значение помечено
    # устаревшим. Обычное обращение к полю дочитало бы его синхронно, вне
    # асинхронного контекста, и упало бы. Дочитываем явно, одним await.
    await db.refresh(deal)
    payload = await _detail(db, deal)
    await emit_to_conversation(db, deal.conversation_id, "deal.updated", {"deal": payload})
    return payload


async def _scoped(db: AsyncSession, user: User, stmt: Select[Any]) -> Select[Any]:
    """Ограничить выборку диалогами, которые видит пользователь: свои и ничейные."""
    account_ids = await visible_account_ids(db, user)
    conditions = conversation_scope_orm(user, account_ids, Conversation)
    return stmt.where(*conditions) if conditions else stmt


async def _conditions(
    db: AsyncSession, user: User, *, date_from: date | None, date_to: date | None,
    status: DealStatus | None, client_id: int | None, conversation_id: int | None,
    account_id: int | None = None,
) -> list[Any]:
    """Фильтры списка и сводки. Период по умолчанию — последний год.

    Период считается по **дате события**: у оплаченной сделки это дата оплаты,
    у остальных — дата создания. Иначе получается расхождение, из-за которого
    перестают верить отчётам: сделка, созданная в июле и оплаченная в августе,
    попадала бы в августовскую статистику, но не в августовский список оплат,
    и плитка «оплачено за период» показывала бы не то же число, что статистика.
    """
    # «Сегодня» — по часовому поясу организации, не по UTC: иначе в последние
    # часы дня по Москве (21:00–24:00 UTC) сервер уже считал бы «сегодня»
    # вчерашним, и только что оплаченная сделка выпадала бы из периода
    # по умолчанию (последний год до сегодня).
    today = datetime.now(UTC).astimezone(await local_zone(db)).date()
    since = date_from or today - timedelta(days=365)
    until = date_to or today
    event_at = func.coalesce(Deal.paid_at, Deal.created_at)
    lo, hi = await day_bounds(db, since, until)
    conds: list[Any] = [event_at >= lo, event_at < hi]
    if status is not None:
        conds.append(Deal.status == status)
    if client_id is not None:
        conds.append(Deal.client_id == client_id)
    if conversation_id is not None:
        conds.append(Deal.conversation_id == conversation_id)
    # ТЗ п. 4.6: из чата показываются оплаты только этого канала. Клиент может
    # платить через несколько направлений, и мешать их в одном списке нельзя —
    # менеджер решит, что видит свои продажи, а увидит чужие.
    if account_id is not None:
        conds.append(Conversation.account_id == account_id)
    account_ids = await visible_account_ids(db, user)
    conds.extend(conversation_scope_orm(user, account_ids, Conversation))
    return conds


async def filter_conditions(db: AsyncSession, user: User, **filters: Any) -> list[Any]:
    """Те же условия отбора, что у списка и плиток, — для выгрузки.

    Выгрузка обязана отдавать ровно те строки, которые человек видит на экране.
    Пока она собирала условия сама, фильтр по каналу и по диалогу до неё просто
    не доезжал: на экране 18 оплат одного канала, в файле — все 34.
    """
    return await _conditions(db, user, **filters)


async def list_deals(
    db: AsyncSession, user: User, *, cursor: str | None, limit: int, **filters: Any
) -> dict[str, Any]:
    conds = await _conditions(db, user, **filters)
    after = decode_cursor(cursor)
    if after is not None:
        conds.append(Deal.id < after)
    stmt = (
        select(Deal, User).join(User, User.id == Deal.sold_by_id)
        .join(Conversation, Conversation.id == Deal.conversation_id).where(*conds)
        .order_by(func.coalesce(Deal.paid_at, Deal.created_at).desc(), Deal.id.desc())
        .limit(limit + 1)
    )
    rows = (await db.execute(stmt)).all()
    page = rows[:limit]
    return {
        "items": [row_payload(deal, sold_by) for deal, sold_by in page],
        "next_cursor": encode_cursor(page[-1][0].id) if len(rows) > limit and page else None,
    }


async def by_requisite(db: AsyncSession, user: User, **filters: Any) -> list[dict[str, Any]]:
    """Поступления в разрезе реквизитов — то, с чем сверяют банковскую выписку.

    Считаем по реквизиту, на который деньги пришли фактически
    (`paid_to_requisite_id`), а не по тому, что был в счёте: клиент нередко
    платит другим способом, и выписка сойдётся только так. У старых сделок
    фактический реквизит не заполнен — для них берём реквизит счёта.
    """
    conditions = await _conditions(db, user, **{**filters, "status": None})
    target = func.coalesce(Deal.paid_to_requisite_id, Deal.requisite_id)
    stmt = (
        select(
            target.label("requisite_id"),
            PaymentRequisite.title,
            PaymentRequisite.country,
            Deal.payment_method,
            func.sum(Deal.total_amount).label("amount"),
            func.count().label("count"),
        )
        .select_from(Deal)
        .join(Conversation, Conversation.id == Deal.conversation_id)
        .outerjoin(PaymentRequisite, PaymentRequisite.id == target)
        .where(*conditions, Deal.status == DealStatus.PAID)
        .group_by(target, PaymentRequisite.title, PaymentRequisite.country, Deal.payment_method)
        .order_by(func.sum(Deal.total_amount).desc())
    )
    rows = (await db.execute(stmt)).all()
    return [
        {
            "requisite_id": row.requisite_id,
            # У оплаты по ссылке своего реквизита нет и не будет — деньги идут
            # на счёт магазина в Робокассе (см. create_deal). Это нормальный,
            # уже сверенный случай — не путаем с «Без реквизита»: та подпись
            # означает настоящую дыру в сверке (счёт удалён/расхождение).
            "title": row.title
            or ("Робокасса — без реквизита" if row.payment_method == "link" else "Без реквизита"),
            "country": row.country,
            "amount": int(row.amount or 0),
            "count": int(row.count or 0),
        }
        for row in rows
    ]


async def summary(db: AsyncSession, user: User, **filters: Any) -> dict[str, int]:
    """Плитки над списком оплат.

    «Оплачено» — за выбранный период. «Ждут оплаты» — состояние на сейчас,
    без периода: сделка, отправленная в прошлом месяце, всё равно ждёт оплаты
    сегодня, и прятать её за фильтром дат — вводить в заблуждение. Иначе
    плитка показывает ноль, а бейдж в навигации — пятёрку.
    """
    paid = Deal.status == DealStatus.PAID
    awaiting = Deal.status == DealStatus.AWAITING

    period_conds = await _conditions(db, user, **filters)
    stmt = (
        select(
            func.coalesce(func.sum(case((paid, Deal.total_amount), else_=0)), 0),
            func.count().filter(paid),
            # Плитка подписана «клиентов с оплатами», значит и считать надо
            # оплативших. Раньше сюда попадал любой клиент со сделкой в периоде —
            # черновик, отменённая, истёкшая, — и плитка показывала 11 при девяти
            # реально оплативших: цифра выглядела как выручка в клиентах, а была
            # числом выставленных счетов.
            func.count(distinct(case((paid, Deal.client_id)))),
        )
        .select_from(Deal)
        .join(Conversation, Conversation.id == Deal.conversation_id)
        .where(*period_conds)
    )
    paid_amount, paid_count, clients_with_deals = (await db.execute(stmt)).one()

    # Те же фильтры, но без дат: период к текущему состоянию не относится.
    current_filters = {**filters, "date_from": None, "date_to": None, "status": None}
    current_conds = await _conditions(db, user, **current_filters)
    # Первые два условия — границы периода, их убираем.
    current_conds = [awaiting, *current_conds[2:]]
    stmt = (
        select(
            func.coalesce(func.sum(Deal.total_amount), 0),
            func.count(),
        )
        .select_from(Deal)
        .join(Conversation, Conversation.id == Deal.conversation_id)
        .where(*current_conds)
    )
    awaiting_amount, awaiting_count = (await db.execute(stmt)).one()

    return {
        "paid_amount": int(paid_amount or 0),
        "awaiting_amount": int(awaiting_amount or 0),
        "paid_count": int(paid_count or 0),
        "awaiting_count": int(awaiting_count or 0),
        "clients_with_deals": int(clients_with_deals or 0),
    }


async def _get_deal(
    db: AsyncSession, user: User, deal_id: int, *, lock: bool = False
) -> tuple[Deal, Conversation]:
    """Сделка вместе с её диалогом. Невидимая менеджеру сделка — 404, а не 403."""
    if lock:
        # Блокировка идёт первой: параллельные операции по одной сделке
        # в прошлой версии разъезжались.
        await db.execute(select(Deal.id).where(Deal.id == deal_id).with_for_update())
    stmt = await _scoped(
        db,
        user,
        select(Deal, Conversation)
        .join(Conversation, Conversation.id == Deal.conversation_id)
        .where(Deal.id == deal_id),
    )
    found = (await db.execute(stmt.execution_options(populate_existing=True))).first()
    if found is None:
        raise NotFound("Сделка не найдена")
    return found[0], found[1]


async def _active_requisite(db: AsyncSession, requisite_id: int | None) -> PaymentRequisite:
    if requisite_id is None:
        raise Invalid("Выберите счёт для оплаты")
    requisite = await db.scalar(
        select(PaymentRequisite).where(
            PaymentRequisite.id == requisite_id, PaymentRequisite.deleted_at.is_(None),
            PaymentRequisite.is_active.is_(True),
        )
    )
    if requisite is None:
        raise Invalid("Счёт не найден или отключён")
    return requisite


async def get_deal(db: AsyncSession, user: User, deal_id: int) -> dict[str, Any]:
    deal, _ = await _get_deal(db, user, deal_id)
    return await _detail(db, deal)


async def create_deal(db: AsyncSession, user: User, data: DealCreate) -> dict[str, Any]:
    if data.payment_method == PaymentMethod.LINK and not settings.robokassa_enabled:
        raise Invalid(LINK_NOT_READY)
    items = _validated_items(data.items)
    # Реквизит нужен только для оплаты по реквизитам: у ссылки его нет и не будет —
    # деньги идут на счёт магазина в Робокассе, а не на конкретный счёт из справочника.
    # «Другое» — реквизитов из справочника нет, менеджер вписал их сам: тогда
    # requisite_id остаётся пустым, а снимок реквизитов фиксируется сразу, а не
    # при отправке (нечего переснимать — это и так текст менеджера, не запись
    # из справочника, которую могли отредактировать позже).
    custom_text = (data.custom_requisites_text or "").strip()
    requisite: PaymentRequisite | None = None
    if data.payment_method == PaymentMethod.REQUISITES:
        if data.requisite_id is not None:
            requisite = await _active_requisite(db, data.requisite_id)
        elif not custom_text:
            raise Invalid("Выберите счёт для оплаты или укажите реквизиты вручную")
    stmt = await _scoped(
        db, user, select(Conversation).where(Conversation.id == data.conversation_id)
    )
    conversation = (await db.execute(stmt)).scalars().first()
    if conversation is None:
        raise NotFound("Диалог не найден")

    deal = Deal(
        client_id=conversation.client_id, conversation_id=conversation.id,
        created_by_id=user.id, sold_by_id=user.id,  # продажу засчитываем создателю оплаты
        payment_method=data.payment_method,
        requisite_id=requisite.id if requisite else None,
        requisites_snapshot=custom_text if (requisite is None and custom_text) else None,
        status=DealStatus.DRAFT,
        intro_text=(data.intro_text or "").strip() or None,
    )
    _replace_items(deal, items)
    db.add(deal)
    await db.flush()
    money = {"total_amount": deal.total_amount, "payment_method": deal.payment_method.value}
    await _record(
        db, deal, user, DealEventKind.CREATED, action="deal.create",
        data={"total_amount": deal.total_amount}, after=money,
    )
    await db.commit()
    deal, _ = await _get_deal(db, user, deal.id)
    return await _detail(db, deal)


async def update_deal(
    db: AsyncSession, user: User, deal_id: int, data: DealUpdate
) -> dict[str, Any]:
    deal, conversation = await _get_deal(db, user, deal_id)
    if deal.status not in (DealStatus.DRAFT, DealStatus.AWAITING):
        raise Conflict(FROZEN, current_status=deal.status.value)
    comment = (data.comment or "").strip()
    if not comment:
        raise Invalid("Укажите причину изменения")

    before = {"total_amount": deal.total_amount, "requisite_id": deal.requisite_id}
    if data.items is not None:
        _replace_items(deal, _validated_items(data.items))
    if data.requisite_id is not None:
        deal.requisite_id = (await _active_requisite(db, data.requisite_id)).id
    deal.edit_count += 1
    after = {"total_amount": deal.total_amount, "requisite_id": deal.requisite_id}

    # Счёт уже мог уйти клиенту — старые сумма/ссылка/реквизиты в чате устарели
    # и платить по ним больше нельзя (ссылка Робокассы подписана на старую
    # сумму, `confirm_paid_by_provider` отобьёт её как несовпадение). Пересобираем
    # счёт и шлём клиенту новое сообщение, чтобы CRM и чат не разъезжались.
    resent_to_client = False
    if deal.sent_at is not None and before != after:
        if deal.payment_method == PaymentMethod.LINK:
            deal.payment_url = await _build_payment_link(db, deal)
            text = (
                "Счёт изменён — актуальные данные для оплаты:\n\n"
                f"Оплатить: {deal.payment_url}\n\n"
                f"Сумма к оплате: {format_rubles(deal.total_amount)}"
            )
        elif deal.requisite_id is not None:
            requisite = await _active_requisite(db, deal.requisite_id)
            deal.requisites_snapshot = requisite.details_text
            text = (
                "Счёт изменён — актуальные данные для оплаты:\n\n"
                f"{requisite.details_text}\n\n"
                f"Сумма к оплате: {format_rubles(deal.total_amount)}"
            )
        else:
            # «Другое»: реквизиты — текст менеджера, править справочнику нечего,
            # пересобираем сообщение с тем же снимком.
            text = (
                "Счёт изменён — актуальные данные для оплаты:\n\n"
                f"{deal.requisites_snapshot}\n\n"
                f"Сумма к оплате: {format_rubles(deal.total_amount)}"
            )
        await send_outgoing(db, conversation=conversation, author=user, text=text)
        resent_to_client = True

    await _record(
        db, deal, user, DealEventKind.EDITED, action="deal.update", comment=comment,
        data={"before": before, "after": after, "resent_to_client": resent_to_client},
        before=before, after=after,
    )
    return await _commit_and_emit(db, deal)


def invoice_text(deal: Deal, details_text: str) -> str:
    """Счёт клиенту: сопроводительный текст, реквизиты и сумма.

    `details_text` — текст реквизита из справочника либо то, что менеджер
    вписал вручную при выборе «Другое»; для счёта разницы нет, это просто
    текст блока с реквизитами.

    Текст менеджера идёт первым — клиент читает сообщение сверху вниз, и сухие
    реквизиты без единого слова выглядят как ошибка отправки (ТЗ п. 4.4 и 4.5).

    Кода платежа для комментария к переводу больше нет: сверка теперь идёт по
    чеку, который менеджер прикладывает при подтверждении оплаты, а не по
    надежде, что клиент аккуратно перепишет код в комментарий банковского
    перевода.
    """
    parts: list[str] = []
    if deal.intro_text and deal.intro_text.strip():
        parts.append(deal.intro_text.strip())
    parts.append(details_text)
    parts.append(f"Сумма к оплате: {format_rubles(deal.total_amount)}")
    return "\n\n".join(parts)


def link_invoice_text(deal: Deal) -> str:
    """Счёт клиенту для оплаты по ссылке — тот же порядок, что у реквизитов
    (ТЗ п. 4.4 и 4.5): текст менеджера первым, потом ссылка и сумма.

    Description в самой ссылке — поле формата запроса к Робокассе (её лимиты,
    её страница оплаты), не сообщение клиенту в чате: это разные вещи, и одно
    не заменяет другое (docs/11-payments-architecture.md, разд. 3).
    """
    parts: list[str] = []
    if deal.intro_text and deal.intro_text.strip():
        parts.append(deal.intro_text.strip())
    parts.append(f"Оплатить: {deal.payment_url}")
    parts.append(f"Сумма к оплате: {format_rubles(deal.total_amount)}")
    return "\n\n".join(parts)


async def _build_payment_link(db: AsyncSession, deal: Deal) -> str:
    if not settings.robokassa_enabled:
        raise Invalid(LINK_NOT_READY)
    all_settings = await settings_get_all(db)
    return robokassa.build_payment_url(
        merchant_login=settings.robokassa_merchant_login,
        password1=settings.robokassa_password1,
        inv_id=deal.id,
        out_sum_kopecks=deal.total_amount,
        description=deal.title,
        receipt_items=[
            robokassa.ReceiptItem(name=item.name, amount_kopecks=item.amount) for item in deal.items
        ],
        sno=str(all_settings.get("robokassa_sno") or "usn_income"),
        tax=str(all_settings.get("robokassa_tax") or "none"),
        is_test=settings.robokassa_is_test,
    )


async def send_deal(db: AsyncSession, user: User, deal_id: int) -> dict[str, Any]:
    deal, conversation = await _get_deal(db, user, deal_id)
    _ensure_transition(deal, DealStatus.AWAITING)

    if deal.payment_method == PaymentMethod.LINK:
        deal.payment_url = await _build_payment_link(db, deal)
        # У ссылки нет отдельного идентификатора платежа до оплаты — Робокасса
        # не выдаёт его заранее. InvId (наш deal.id) и есть устойчивый ключ,
        # сохраняем его же, чтобы поле не пустовало и было чем искать в логах.
        deal.provider_payment_id = str(deal.id)
        text = link_invoice_text(deal)
    elif deal.requisite_id is not None:
        requisite = await _active_requisite(db, deal.requisite_id)
        # Снимок реквизитов: правка справочника не должна переписывать историю.
        deal.requisites_snapshot = requisite.details_text
        text = invoice_text(deal, requisite.details_text)
    else:
        # «Другое»: снимок уже зафиксирован при создании сделки (create_deal) —
        # это текст самого менеджера, переснимать с ним нечего.
        if not deal.requisites_snapshot:
            raise Invalid("Реквизиты не указаны")
        text = invoice_text(deal, deal.requisites_snapshot)

    message = await send_outgoing(db, conversation=conversation, author=user, text=text)
    await db.flush()

    now = datetime.now(UTC)
    deal.status = DealStatus.AWAITING
    deal.sent_at = now
    deal.expires_at = now + timedelta(days=int(await get_value(db, "deal_link_ttl_days") or 7))
    deal.sent_message_id = message.id
    await _record(
        db, deal, user, DealEventKind.SENT, action="deal.send",
        after={"status": deal.status.value, "expires_at": deal.expires_at.isoformat()},
    )
    return await _commit_and_emit(db, deal)


RECEIPT_MIME_TYPES = {
    "application/pdf",
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/gif",
    "image/heic",
    "image/heif",
    "image/bmp",
    "image/tiff",
}


async def pay_deal(
    db: AsyncSession,
    user: User,
    deal_id: int,
    comment: str | None = None,
    receipt_upload_key: str | None = None,
    receipt_file_name: str | None = None,
    receipt_mime_type: str | None = None,
    receipt_size_bytes: int = 0,
    paid_to_requisite_id: int | None = None,
) -> dict[str, Any]:
    deal, _ = await _get_deal(db, user, deal_id, lock=True)
    _ensure_transition(deal, DealStatus.PAID)
    # Чек обязателен, только теперь это файл, а не число, которое менеджер мог
    # вписать наугад. Оплата без чека — это выручка, которой нет в кассе:
    # расхождение всплывёт при сверке, а найти его будет нечем.
    if not receipt_upload_key or not receipt_file_name:
        raise Invalid(
            "Прикрепите чек оплаты — без него подтвердить нельзя",
            field="receipt_upload_key",
        )
    if (receipt_mime_type or "").lower() not in RECEIPT_MIME_TYPES:
        raise Invalid(
            "Чек принимается только как изображение или PDF", field="receipt_upload_key"
        )
    # Оплата необратима (PAID — конечный статус), поэтому чек обязан реально
    # лежать в хранилище прямо сейчас — иначе сделка навсегда осталась бы
    # «оплаченной» без единого доказательства этого.
    if not await storage.object_exists(receipt_upload_key):
        raise Invalid("Файл чека не найден — загрузите его ещё раз", field="receipt_upload_key")
    deal.status = DealStatus.PAID
    deal.paid_at = datetime.now(UTC)
    deal.paid_by_id = user.id
    deal.paid_source = PaidSource.MANUAL
    deal.receipt_storage_key = receipt_upload_key
    deal.receipt_file_name = receipt_file_name
    deal.receipt_mime_type = receipt_mime_type
    deal.receipt_size_bytes = receipt_size_bytes
    # Куда деньги пришли фактически. По умолчанию — тот реквизит, что был
    # в счёте; если платили на другой, менеджер указывает его явно, иначе
    # сверка с выпиской этого счёта не сойдётся.
    deal.paid_to_requisite_id = paid_to_requisite_id or deal.requisite_id
    await _record(
        db, deal, user, DealEventKind.PAID, action="deal.pay",
        comment=(comment or "").strip() or None,
        after={"status": deal.status.value, "paid_source": PaidSource.MANUAL.value},
    )

    # Об оплате узнают все руководители и тот, кому засчитана продажа.
    admins = await db.execute(
        select(User.id).where(User.role == UserRole.ADMIN, User.is_active.is_(True))
    )
    recipients = sorted({*admins.scalars().all(), deal.sold_by_id})
    notice = {
        "deal_id": deal.id,
        "number": deal.number,
        "amount": deal.total_amount,
        "client_name": deal.client.name,
        "kind": NotificationKind.DEAL_PAID.value,
    }
    for user_id in recipients:
        db.add(
            Notification(
                user_id=user_id, kind=NotificationKind.DEAL_PAID,
                entity_type="deal", entity_id=deal.id, payload=notice,
            )
        )
    payload = await _commit_and_emit(db, deal)
    await emit("notification.new", {"notification": notice}, recipients)
    return payload


async def receipt_file(db: AsyncSession, user: User, deal_id: int) -> tuple[Deal, bytes]:
    """Файл чека вместе со сделкой — видимость та же, что у самой сделки."""
    deal, _ = await _get_deal(db, user, deal_id)
    if not deal.receipt_storage_key:
        raise NotFound("Чек не прикреплён")
    try:
        body = await storage.get_object(deal.receipt_storage_key)
    except storage.ObjectNotFound as exc:
        raise NotFound("Файл чека не найден в хранилище") from exc
    return deal, body


class ProviderResult:
    """Итог обработки уведомления провайдера — для роутера, не для клиента API."""

    __slots__ = ("outcome", "already_paid")

    def __init__(self, outcome: str, *, already_paid: bool = False) -> None:
        self.outcome = outcome  # "paid" | "already_paid" | "amount_mismatch" | "wrong_status"
        self.already_paid = already_paid


async def confirm_paid_by_provider(
    db: AsyncSession, deal_id: int, amount_kopecks: int, provider_payment_id: str
) -> ProviderResult:
    """Единственное место, где сделка становится оплаченной по ссылке.

    Вызывается уже ПОСЛЕ проверки подписи (`api/v1/payments.py`) — здесь подпись
    не проверяется повторно. Идемпотентность держится на статусе самой сделки под
    блокировкой строки, а не на таблице `payment_events`: повторная доставка
    того же уведомления должна быть не отличима от первой по результату, даже
    если между ними сделку успели подтвердить вручную или наоборот.
    """
    # Блокировка первой строкой — тот же порядок, что у ручного pay_deal:
    # параллельные операции над одной сделкой не должны разъезжаться.
    await db.execute(select(Deal.id).where(Deal.id == deal_id).with_for_update())
    deal = await db.get(Deal, deal_id)
    if deal is None:
        return ProviderResult("wrong_status")

    if deal.status == DealStatus.PAID:
        # Повтор уведомления — норма, а не ошибка (Робокасса ретраит, пока не
        # увидит «OK»). Ничего не меняем, отвечаем так, будто обработали сейчас.
        return ProviderResult("already_paid", already_paid=True)

    # Сумме из запроса верить нельзя — сверяем со своей базой, не с уведомлением.
    # Типичная причина расхождения — устаревшая ссылка: сделку отредактировали
    # после отправки, а клиент заплатил по старой (see update_deal). Деньги уже
    # у провайдера, сделка не закрыта — молчать логом мало, нужен живой человек.
    if amount_kopecks != deal.total_amount:
        db.add(
            DealEvent(
                deal_id=deal.id, actor_id=None, kind=DealEventKind.EDITED,
                comment=(
                    f"Провайдер прислал оплату {format_rubles(amount_kopecks)}, "
                    f"в сделке {format_rubles(deal.total_amount)} — платёж не принят"
                ),
                data={
                    "provider": "robokassa", "provider_payment_id": provider_payment_id,
                    "amount_received": amount_kopecks,
                },
            )
        )
        admins = await db.execute(
            select(User.id).where(User.role == UserRole.ADMIN, User.is_active.is_(True))
        )
        recipients = sorted({*admins.scalars().all(), deal.sold_by_id})
        notice = {
            "deal_id": deal.id, "number": deal.number,
            "amount_received": amount_kopecks, "amount_expected": deal.total_amount,
            "kind": NotificationKind.PAYMENT_MISMATCH.value,
        }
        for user_id in recipients:
            db.add(
                Notification(
                    user_id=user_id, kind=NotificationKind.PAYMENT_MISMATCH,
                    entity_type="deal", entity_id=deal.id, payload=notice,
                )
            )
        await db.commit()
        await emit("notification.new", {"notification": notice}, recipients)
        return ProviderResult("amount_mismatch")

    # AWAITING — обычный путь. EXPIRED — деньги всё равно пришли, и отказать
    # клиенту нельзя; ручное подтверждение истёкшей сделки при этом остаётся
    # запрещённым (ALLOWED_DEAL_TRANSITIONS этого не разрешает нарочно) —
    # здесь отдельная, более узкая дверь, только для источника «провайдер».
    if deal.status not in (DealStatus.AWAITING, DealStatus.EXPIRED):
        # Деньги пришли, а сделка уже не в статусе, допускающем оплату —
        # например, её отменили ровно в момент, когда клиент платил. Провайдер
        # деньги принял, CRM их девать некуда — молчать логом мало, нужен
        # живой человек, который сверится с личным кабинетом Робокассы.
        #
        # Робокасса повторяет доставку, пока не получит «OK» — а «wrong status»
        # им не является, так что без этой проверки один и тот же случай
        # заваливал бы админов уведомлением на каждый повтор (часами).
        already_notified = await db.scalar(
            select(
                exists().where(
                    Notification.kind == NotificationKind.PAYMENT_ORPHANED,
                    Notification.entity_type == "deal",
                    Notification.entity_id == deal.id,
                )
            )
        )
        if already_notified:
            return ProviderResult("wrong_status")

        from app.services.deal_export import STATUS_LABEL

        status_label = STATUS_LABEL.get(deal.status, deal.status.value)
        db.add(
            DealEvent(
                deal_id=deal.id, actor_id=None, kind=DealEventKind.EDITED,
                comment=(
                    f"Провайдер прислал оплату {format_rubles(amount_kopecks)}, "
                    f"но сделка в статусе «{status_label}» — деньги нужно сверить вручную"
                ),
                data={
                    "provider": "robokassa", "provider_payment_id": provider_payment_id,
                    "amount_received": amount_kopecks, "deal_status": deal.status.value,
                },
            )
        )
        admins = await db.execute(
            select(User.id).where(User.role == UserRole.ADMIN, User.is_active.is_(True))
        )
        recipients = sorted({*admins.scalars().all(), deal.sold_by_id})
        notice = {
            "deal_id": deal.id, "number": deal.number,
            "amount_received": amount_kopecks, "deal_status_label": status_label,
            "kind": NotificationKind.PAYMENT_ORPHANED.value,
        }
        for user_id in recipients:
            db.add(
                Notification(
                    user_id=user_id, kind=NotificationKind.PAYMENT_ORPHANED,
                    entity_type="deal", entity_id=deal.id, payload=notice,
                )
            )
        await db.commit()
        await emit("notification.new", {"notification": notice}, recipients)
        return ProviderResult("wrong_status")

    was_expired = deal.status == DealStatus.EXPIRED
    deal.status = DealStatus.PAID
    deal.paid_at = datetime.now(UTC)
    deal.paid_by_id = None  # оплатил клиент, а не сотрудник
    deal.paid_source = PaidSource.PROVIDER
    deal.provider_payment_id = provider_payment_id

    comment = "Оплата подтверждена Робокассой"
    if was_expired:
        comment += " — сделка уже была просрочена, деньги пришли позже срока"
    db.add(
        DealEvent(
            deal_id=deal.id, actor_id=None, kind=DealEventKind.PAID, comment=comment,
            data={"provider": "robokassa", "provider_payment_id": provider_payment_id},
        )
    )
    await log_event(
        db, action="deal.pay", entity_type="deal", entity_id=deal.id,
        actor=None, actor_kind=ActorKind.SYSTEM,
        before={"status": DealStatus.EXPIRED.value if was_expired else DealStatus.AWAITING.value},
        after={"status": DealStatus.PAID.value, "paid_source": PaidSource.PROVIDER.value},
    )

    admins = await db.execute(
        select(User.id).where(User.role == UserRole.ADMIN, User.is_active.is_(True))
    )
    recipients = sorted({*admins.scalars().all(), deal.sold_by_id})
    await db.flush()
    await db.refresh(deal)
    notice = {
        "deal_id": deal.id, "number": deal.number, "amount": deal.total_amount,
        "client_name": deal.client.name, "kind": NotificationKind.DEAL_PAID.value,
    }
    for user_id in recipients:
        db.add(
            Notification(
                user_id=user_id, kind=NotificationKind.DEAL_PAID,
                entity_type="deal", entity_id=deal.id, payload=notice,
            )
        )
    await db.commit()
    await db.refresh(deal)
    payload = await _detail(db, deal)
    await emit_to_conversation(db, deal.conversation_id, "deal.updated", {"deal": payload})
    await emit("notification.new", {"notification": notice}, recipients)
    return ProviderResult("paid")


async def cancel_deal(
    db: AsyncSession, user: User, deal_id: int, reason: str | None
) -> dict[str, Any]:
    deal, _ = await _get_deal(db, user, deal_id)
    _ensure_transition(deal, DealStatus.CANCELLED)
    text = (reason or "").strip()
    if not text:
        raise Invalid("Укажите причину отмены")

    before = {"status": deal.status.value}
    deal.status = DealStatus.CANCELLED
    deal.cancelled_at = datetime.now(UTC)
    deal.cancel_reason = text
    await _record(
        db, deal, user, DealEventKind.CANCELLED, action="deal.cancel", comment=text,
        before=before, after={"status": deal.status.value, "cancel_reason": text},
    )
    return await _commit_and_emit(db, deal)
