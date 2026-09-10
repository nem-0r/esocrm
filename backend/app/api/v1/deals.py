"""Раздел «Оплаты». Роутер тонкий: разбирает запрос и зовёт сервис."""

from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

from app.core.deps import CurrentUser, Db
from app.models.enums import DealStatus
from app.schemas.common import CursorPage
from app.schemas.deal import (
    DealCancel,
    DealCreate,
    DealDetail,
    DealPay,
    DealRow,
    DealSummary,
    DealUpdate,
)
from app.services import deal_export, deal_service
from app.services.export_format import content_disposition
from app.services.worktime import local_zone

router = APIRouter()

_CHUNK_SIZE = 1024 * 1024


async def _chunks(body: bytes) -> AsyncIterator[bytes]:
    for offset in range(0, len(body), _CHUNK_SIZE):
        yield body[offset : offset + _CHUNK_SIZE]


async def deal_filters(
    date_from: Annotated[
        date | None, Query(description="Начало периода, по умолчанию год назад")
    ] = None,
    date_to: Annotated[date | None, Query(description="Конец периода, включительно")] = None,
    status: Annotated[DealStatus | None, Query(description="Статус сделки")] = None,
    client_id: Annotated[int | None, Query(description="ID клиента")] = None,
    conversation_id: Annotated[int | None, Query(description="ID диалога")] = None,
    account_id: Annotated[
        int | None, Query(description="ID аккаунта: показать оплаты только этого канала")
    ] = None,
) -> dict[str, Any]:
    """Фильтры по дате, статусу и клиенту работают одновременно."""
    return {
        "date_from": date_from,
        "date_to": date_to,
        "status": status,
        "client_id": client_id,
        "conversation_id": conversation_id,
        "account_id": account_id,
    }


Filters = Annotated[dict[str, Any], Depends(deal_filters)]


@router.get("", response_model=CursorPage[DealRow], summary="Оплаты за период")
async def list_deals(
    db: Db,
    user: CurrentUser,
    filters: Filters,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> Any:
    return await deal_service.list_deals(db, user, cursor=cursor, limit=limit, **filters)


@router.get("/summary", response_model=DealSummary, summary="Плитки над списком оплат")
async def deals_summary(db: Db, user: CurrentUser, filters: Filters) -> Any:
    return await deal_service.summary(db, user, **filters)


@router.get(
    "/by-requisite",
    summary="Поступления по реквизитам за период",
    description=(
        "Сколько денег пришло на каждый реквизит. Нужно для сверки с банковской "
        "выпиской: в выписке видно счёт и сумму, здесь — те же деньги в разрезе сделок."
    ),
)
async def deals_by_requisite(db: Db, user: CurrentUser, filters: Filters) -> Any:
    return await deal_service.by_requisite(db, user, **filters)


@router.get("/export", summary="Выгрузка оплат за период в CSV")
async def export_deals(db: Db, user: CurrentUser, filters: Filters) -> StreamingResponse:
    """Тот же период и те же фильтры, что на экране: выгрузка обязана сходиться
    с тем, что человек только что видел в списке.

    Фильтры берутся из той же зависимости, что у списка и плиток. Раньше
    выгрузка объявляла свой урезанный набор, и фильтр по каналу до неё не
    доезжал: на экране оплаты одного аккаунта, в файле — все.
    """
    today = datetime.now(UTC).astimezone(await local_zone(db)).date()
    day = (filters["date_to"] or today).isoformat()
    return StreamingResponse(
        deal_export.export_csv_rows(db, user, **filters),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": content_disposition(
                f"Оплаты {day}.csv", ascii_name=f"payments-{day}.csv"
            )
        },
    )


@router.post("", response_model=DealDetail, status_code=201, summary="Создать сделку")
async def create_deal(db: Db, user: CurrentUser, data: DealCreate) -> Any:
    return await deal_service.create_deal(db, user, data)


@router.get("/{deal_id}", response_model=DealDetail, summary="Карточка сделки")
async def get_deal(db: Db, user: CurrentUser, deal_id: int) -> Any:
    return await deal_service.get_deal(db, user, deal_id)


@router.patch("/{deal_id}", response_model=DealDetail, summary="Изменить состав сделки")
async def update_deal(db: Db, user: CurrentUser, deal_id: int, data: DealUpdate) -> Any:
    return await deal_service.update_deal(db, user, deal_id, data)


@router.post("/{deal_id}/send", response_model=DealDetail, summary="Отправить счёт в чат")
async def send_deal(db: Db, user: CurrentUser, deal_id: int) -> Any:
    return await deal_service.send_deal(db, user, deal_id)


@router.post("/{deal_id}/pay", response_model=DealDetail, summary="Подтвердить оплату вручную")
async def pay_deal(db: Db, user: CurrentUser, deal_id: int, data: DealPay | None = None) -> Any:
    return await deal_service.pay_deal(
        db,
        user,
        deal_id,
        data.comment if data else None,
        data.receipt_upload_key if data else None,
        data.receipt_file_name if data else None,
        data.receipt_mime_type if data else None,
        data.receipt_size_bytes if data else 0,
    )


@router.get("/{deal_id}/receipt", summary="Скачать чек оплаты")
async def download_receipt(db: Db, user: CurrentUser, deal_id: int) -> StreamingResponse:
    deal, body = await deal_service.receipt_file(db, user, deal_id)
    return StreamingResponse(
        _chunks(body),
        media_type=deal.receipt_mime_type or "application/octet-stream",
        headers={
            "Content-Disposition": content_disposition(
                deal.receipt_file_name or "receipt", disposition="inline"
            ),
            # Чек неизменяем: один раз прикреплённый файл не переписывается —
            # подтверждённую оплату не редактируют, отменяют и заводят заново.
            "Cache-Control": "private, max-age=31536000, immutable",
        },
    )


@router.post("/{deal_id}/cancel", response_model=DealDetail, summary="Отменить сделку")
async def cancel_deal(db: Db, user: CurrentUser, deal_id: int, data: DealCancel) -> Any:
    return await deal_service.cancel_deal(db, user, deal_id, data.reason)
