"""Справочник счетов получателя.

Читают все — менеджер выбирает счёт при создании оплаты. Меняет только руководитель.
Удаление мягкое: сделки хранят снимок реквизитов, но ссылка на строку остаётся.
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Invalid, NotFound
from app.models import PaymentRequisite, User
from app.schemas.requisite import RequisiteCreate, RequisiteUpdate
from app.services.audit import log_event

NEED_ONE_ACTIVE = "Нужен хотя бы один активный счёт"
FIELDS = ("title", "bank_name", "account_masked", "details_text", "is_active", "sort_order")


def _payload(requisite: PaymentRequisite) -> dict[str, Any]:
    return {field: getattr(requisite, field) for field in FIELDS}


async def list_requisites(
    db: AsyncSession, *, include_inactive: bool = False
) -> list[PaymentRequisite]:
    stmt = select(PaymentRequisite).where(PaymentRequisite.deleted_at.is_(None))
    if not include_inactive:
        stmt = stmt.where(PaymentRequisite.is_active.is_(True))
    rows = await db.execute(stmt.order_by(PaymentRequisite.sort_order, PaymentRequisite.id))
    return list(rows.scalars().all())


async def _get(db: AsyncSession, requisite_id: int) -> PaymentRequisite:
    requisite = await db.scalar(
        select(PaymentRequisite).where(
            PaymentRequisite.id == requisite_id, PaymentRequisite.deleted_at.is_(None)
        )
    )
    if requisite is None:
        raise NotFound("Счёт не найден")
    return requisite


async def _active_count(db: AsyncSession, *, except_id: int) -> int:
    return int(
        await db.scalar(
            select(func.count())
            .select_from(PaymentRequisite)
            .where(
                PaymentRequisite.deleted_at.is_(None),
                PaymentRequisite.is_active.is_(True),
                PaymentRequisite.id != except_id,
            )
        )
        or 0
    )


async def create_requisite(
    db: AsyncSession, user: User, data: RequisiteCreate
) -> PaymentRequisite:
    requisite = PaymentRequisite(**data.model_dump())
    db.add(requisite)
    await db.flush()
    await log_event(
        db, action="requisite.create", entity_type="payment_requisite",
        entity_id=requisite.id, actor=user, after=_payload(requisite),
    )
    await db.commit()
    return requisite


async def update_requisite(
    db: AsyncSession, user: User, requisite_id: int, data: RequisiteUpdate
) -> PaymentRequisite:
    requisite = await _get(db, requisite_id)
    before = _payload(requisite)
    changes = data.model_dump(exclude_unset=True)
    # Отключение последнего активного счёта оставило бы менеджеров без выбора.
    if changes.get("is_active") is False and await _active_count(db, except_id=requisite.id) == 0:
        raise Invalid(NEED_ONE_ACTIVE)
    for field, value in changes.items():
        setattr(requisite, field, value)
    await log_event(
        db, action="requisite.update", entity_type="payment_requisite",
        entity_id=requisite.id, actor=user, before=before, after=_payload(requisite),
    )
    await db.commit()
    return requisite


async def delete_requisite(db: AsyncSession, user: User, requisite_id: int) -> None:
    requisite = await _get(db, requisite_id)
    if requisite.is_active and await _active_count(db, except_id=requisite.id) == 0:
        raise Invalid(NEED_ONE_ACTIVE)
    before = _payload(requisite)
    requisite.deleted_at = datetime.now(UTC)
    requisite.is_active = False
    await log_event(
        db, action="requisite.delete", entity_type="payment_requisite",
        entity_id=requisite.id, actor=user, before=before,
    )
    await db.commit()
