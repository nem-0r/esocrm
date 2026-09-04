"""Выгрузка оплат в CSV.

Формат — общий для всех отчётов, он описан в `export_format`: UTF-8 с BOM,
разделитель `;`, суммы с запятой, защита от подстановки формул.

Условия отбора берутся у списка оплат (`deal_service.filter_conditions`), а не
пишутся заново: файл обязан совпадать с тем, что человек только что видел на
экране, вплоть до фильтра по каналу и по диалогу.
"""

from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy import func, literal, select
from sqlalchemy.dialects.postgresql import aggregate_order_by
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Client, Conversation, Deal, DealItem, DealStatus, User
from app.services import deal_service
from app.services.export_format import BOM, CsvBuffer, csv_safe, dt, rub

CSV_HEADER = [
    "Номер",
    "Дата события",
    "Статус",
    "Клиент",
    "ID клиента",
    "Услуги",
    "Сумма (руб)",
    "Способ оплаты",
    "Код платежа",
    "Отправлено",
    "Оплачено",
    "Срок действия",
    "Менеджер",
    "Причина отмены",
]

STATUS_LABEL = {
    DealStatus.DRAFT: "Черновик",
    DealStatus.AWAITING: "Ждёт оплаты",
    DealStatus.PAID: "Оплачено",
    DealStatus.CANCELLED: "Отменено",
    DealStatus.EXPIRED: "Истекло",
}

METHOD_LABEL = {"requisites": "Реквизиты", "link": "Ссылка"}


async def export_csv_rows(db: AsyncSession, user: User, **filters: Any) -> AsyncIterator[bytes]:
    """Строки файла по мере чтения из базы: в памяти живёт одна строка, не отчёт.

    `filters` — тот же набор, что у `GET /deals`: период, статус, клиент,
    диалог, канал. Он приходит из общей зависимости роутера, поэтому новый
    фильтр на экране физически не может забыться в выгрузке.
    """
    conditions = await deal_service.filter_conditions(db, user, **filters)

    # Период считается по дате события — так же, как в списке и в плитках.
    event_at = func.coalesce(Deal.paid_at, Deal.created_at)

    items = (
        select(
            DealItem.deal_id.label("deal_id"),
            # Порядок услуг фиксируем: без ORDER BY внутри агрегата Postgres
            # волен склеить строку по-разному в двух соседних выгрузках одного
            # и того же периода — и файлы перестают сравниваться построчно.
            func.string_agg(
                DealItem.name,
                aggregate_order_by(literal(", "), DealItem.position, DealItem.id),
            ).label("names"),
        )
        .group_by(DealItem.deal_id)
        .subquery()
    )

    stmt = (
        select(
            Deal.id,
            event_at.label("event_at"),
            Deal.status,
            Client.display_name,
            Client.telegram_id,
            Client.id.label("client_id"),
            items.c.names,
            Deal.total_amount,
            Deal.payment_method,
            Deal.payment_code,
            Deal.sent_at,
            Deal.paid_at,
            Deal.expires_at,
            User.full_name,
            Deal.cancel_reason,
        )
        .select_from(Deal)
        .join(Client, Client.id == Deal.client_id)
        .join(Conversation, Conversation.id == Deal.conversation_id)
        .join(User, User.id == Deal.sold_by_id)
        .outerjoin(items, items.c.deal_id == Deal.id)
        .where(*conditions)
        .order_by(event_at.desc(), Deal.id.desc())
    )

    csv_buffer = CsvBuffer()

    yield BOM
    yield csv_buffer.row(CSV_HEADER)

    result = await db.stream(stmt)
    async for row in result:
        yield csv_buffer.row(
            [
                f"DEAL-{row[0]}",
                dt(row.event_at),
                STATUS_LABEL.get(row.status, str(row.status)),
                csv_safe(row.display_name or str(row.telegram_id)),
                row.client_id,
                csv_safe(row.names or ""),
                rub(row.total_amount),
                METHOD_LABEL.get(str(row.payment_method), str(row.payment_method)),
                csv_safe(row.payment_code or ""),
                dt(row.sent_at),
                dt(row.paid_at),
                dt(row.expires_at),
                csv_safe(row.full_name),
                csv_safe(row.cancel_reason or ""),
            ]
        )
