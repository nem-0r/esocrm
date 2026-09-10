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
    # «Другое» в выборе счёта: реквизитов из справочника нет, менеджер вписывает
    # их вручную — например, счёт клиента попросил перевести на карту, которой
    # нет среди заведённых руководителем. Ровно одно из двух полей заполнено,
    # когда payment_method — реквизиты; проверяется в сервисе.
    custom_requisites_text: str | None = None
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
    # Без прикреплённого чека оплату подтвердить нельзя. Файл уже загружен
    # через POST /files/upload (тот же путь, что и вложения в чате) — сюда
    # приходит только ссылка на него, без второй загрузки. Проверяется в
    # сервисе, чтобы сообщение об ошибке было на русском и с именем поля.
    receipt_upload_key: str | None = None
    receipt_file_name: str | None = None
    receipt_mime_type: str | None = None
    receipt_size_bytes: int = 0


class DealRow(ApiModel):
    id: int
    number: str
    title: str
    client: ClientRef
    account: AccountRef
    total_amount: int
    status: DealStatus
    payment_method: PaymentMethod
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
    # Чек, приложенный при подтверждении оплаты. url пуст, пока чека нет —
    # фронтенду не нужно отдельно проверять receipt_file_name на пустоту.
    receipt_file_name: str | None = None
    receipt_mime_type: str | None = None
    receipt_size_bytes: int | None = None
    receipt_url: str | None = None
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


def receipt_url(deal_id: int) -> str:
    """Чек отдаётся через API, а не прямой ссылкой на хранилище: там права."""
    return f"/api/v1/deals/{deal_id}/receipt"


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
        receipt_file_name=deal.receipt_file_name,
        receipt_mime_type=deal.receipt_mime_type,
        receipt_size_bytes=deal.receipt_size_bytes,
        receipt_url=receipt_url(deal.id) if deal.receipt_storage_key else None,
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
