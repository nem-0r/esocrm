"""Схемы раздела «Оплаты»: строка списка, карточка, сводка и тела запросов.

Проверка содержимого (позиции, суммы, обязательные причины) намеренно вынесена в
сервис: только так ошибка уходит клиенту в едином формате `{"error": {...}}`,
а не в формате валидации FastAPI, который фронтенд не разбирает.
"""

from datetime import UTC, datetime
from typing import Any

from app.models.enums import DealEventKind, DealStatus, FunnelStage, PaidSource, PaymentMethod
from app.schemas.common import ApiModel


class UserRef(ApiModel):
    """Ссылка на сотрудника в строке и в журнале."""

    id: int
    full_name: str


class ClientRef(ApiModel):
    id: int
    name: str


class AccountRef(ApiModel):
    """Канал, через который прошла оплата (ТЗ п. 4.6)."""

    id: int
    title: str
    funnel_stage: FunnelStage


class ClientCard(ClientRef):
    """В карточке сделки телефон нужен, в строке списка — нет."""

    phone: str | None = None


class DealItemIn(ApiModel):
    name: str
    amount: int  # копейки


class DealItemOut(ApiModel):
    id: int
    name: str
    amount: int
    position: int


class DealEventOut(ApiModel):
    """Строка блока «Журнал»."""

    id: int
    kind: DealEventKind
    comment: str | None = None
    actor: UserRef | None = None
    created_at: datetime
    event_at: datetime | None = None


class DealCreate(ApiModel):
    conversation_id: int
    payment_method: PaymentMethod
    requisite_id: int | None = None
    items: list[DealItemIn] = []
    # ТЗ п. 4.4 и 4.5: текст, которым менеджер сопровождает счёт. Уходит клиенту
    # перед реквизитами или ссылкой — одинаково для обоих способов оплаты.
    intro_text: str | None = None


class DealUpdate(ApiModel):
    items: list[DealItemIn] | None = None
    requisite_id: int | None = None
    # Причина изменения обязательна: требование доски, проверяется в сервисе.
    comment: str = ""


class DealCancel(ApiModel):
    reason: str = ""


class DealPay(ApiModel):
    comment: str | None = None
    # ТЗ п. 6.3: без номера чека оплату подтвердить нельзя. Проверяется в сервисе,
    # чтобы сообщение об ошибке было на русском и с именем поля.
    receipt_number: str | None = None
    # На какой реквизит деньги пришли фактически. Пусто — значит на тот,
    # что был в счёте.
    paid_to_requisite_id: int | None = None


class DealRow(ApiModel):
    id: int
    number: str
    title: str
    client: ClientRef
    account: AccountRef
    total_amount: int
    status: DealStatus
    payment_method: PaymentMethod
    payment_code: str | None = None
    created_at: datetime
    sent_at: datetime | None = None
    expires_at: datetime | None = None
    paid_at: datetime | None = None
    # Кто подтвердил: менеджер вручную или уведомление Робокассы — важно для
    # сверки, когда деньги идут двумя разными способами одновременно.
    paid_source: PaidSource | None = None
    sold_by: UserRef | None = None
    items_count: int
    # «2 дня без ответа» на доске оплат: целые сутки с момента отправки,
    # пока сделка ждёт оплаты. В остальных статусах — пусто.
    days_without_answer: int | None = None
    updated_at: datetime


class DealDetail(DealRow):
    client: ClientCard
    conversation_id: int
    items: list[DealItemOut]
    requisites_snapshot: str | None = None
    requisite_id: int | None = None
    payment_url: str | None = None
    # Куда деньги пришли фактически — по этому реквизиту сходится выписка.
    paid_to_requisite_id: int | None = None
    paid_to_requisite_title: str | None = None
    intro_text: str | None = None
    cancel_reason: str | None = None
    receipt_number: str | None = None
    receipt_at: datetime | None = None
    edit_count: int
    events: list[DealEventOut]


class DealSummary(ApiModel):
    """Три плитки над списком оплат."""

    paid_amount: int
    awaiting_amount: int
    paid_count: int
    awaiting_count: int
    clients_with_deals: int


def user_ref(user: Any) -> dict[str, Any] | None:
    return {"id": user.id, "full_name": user.full_name} if user else None


def days_without_answer(deal: Any) -> int | None:
    if deal.status != DealStatus.AWAITING or deal.sent_at is None:
        return None
    return (datetime.now(UTC) - deal.sent_at).days


def row_payload(deal: Any, sold_by: Any) -> dict[str, Any]:
    """Строка списка. Собирается словарём: тот же payload уходит и в websocket."""
    return {
        "id": deal.id,
        "number": deal.number,
        "title": deal.title,
        "client": {"id": deal.client.id, "name": deal.client.name},
        # ТЗ п. 4.6: в карточке клиента видно, через какой канал прошла оплата.
        "account": {
            "id": deal.conversation.account.id,
            "title": deal.conversation.account.title,
            "funnel_stage": deal.conversation.account.funnel_stage,
        },
        "total_amount": deal.total_amount,
        "status": deal.status,
        "payment_method": deal.payment_method,
        "payment_code": deal.payment_code,
        "created_at": deal.created_at,
        # Дата, по которой сделка попадает в период: оплата или создание.
        "event_at": deal.paid_at or deal.created_at,
        "sent_at": deal.sent_at,
        "expires_at": deal.expires_at,
        "paid_at": deal.paid_at,
        "paid_source": deal.paid_source,
        "sold_by": user_ref(sold_by),
        "items_count": len(deal.items),
        "days_without_answer": days_without_answer(deal),
        "updated_at": deal.updated_at,
    }


def detail_payload(deal: Any, sold_by: Any, events: list[tuple[Any, Any]]) -> dict[str, Any]:
    """Карточка сделки: строка списка плюс позиции, реквизиты и «Журнал»."""
    data = row_payload(deal, sold_by)
    data.update(
        conversation_id=deal.conversation_id,
        client={"id": deal.client.id, "name": deal.client.name, "phone": deal.client.phone},
        items=[
            {"id": i.id, "name": i.name, "amount": i.amount, "position": i.position}
            for i in deal.items
        ],
        requisites_snapshot=deal.requisites_snapshot,
        requisite_id=deal.requisite_id,
        payment_url=deal.payment_url,
        paid_to_requisite_id=deal.paid_to_requisite_id,
        paid_to_requisite_title=(
            deal.paid_to_requisite.title if deal.paid_to_requisite is not None else None
        ),
        intro_text=deal.intro_text,
        cancel_reason=deal.cancel_reason,
        receipt_number=deal.receipt_number,
        receipt_at=deal.receipt_at,
        edit_count=deal.edit_count,
        events=[
            {
                "id": event.id,
                "kind": event.kind,
                "comment": event.comment,
                "actor": user_ref(actor),
                "created_at": event.created_at,
            }
            for event, actor in events
        ],
    )
    return data
