"""Справочник услуг: перечень, который менеджер видит в окне оплаты.

Читают все — выбор услуги нужен каждому, кто выставляет счёт. Меняет только
руководитель. Удаление мягкое: позиции прошлых сделок ссылаются на услугу, а
их название и цена лежат в самих позициях снимком.

«Подсказки из истории» — ответ на вопрос «какие услуги вообще продаются»:
названия, которые менеджеры уже вписывали в сделки вручную, с числом продаж и
типичной ценой. На живой базе это и есть реальный перечень — руководителю
остаётся нажать «Добавить» у нужных.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict, Invalid, NotFound
from app.models import Service, User
from app.services.audit import log_event
from app.services.money import MoneyError, validate_amount

FIELDS = ("name", "price", "description", "is_active", "sort_order")
SUGGESTION_DAYS = 365
SUGGESTION_LIMIT = 30


def _payload(service: Service) -> dict[str, Any]:
    return {field: getattr(service, field) for field in FIELDS}


def _clean_name(value: str | None) -> str:
    name = " ".join((value or "").split())
    if not name or len(name) > 255:
        raise Invalid("Название услуги — от 1 до 255 символов", field="name")
    return name


def _clean_price(value: int | None) -> int | None:
    if value is None:
        return None
    try:
        return validate_amount(int(value))
    except MoneyError as exc:
        raise Invalid(str(exc), field="price") from exc


async def list_services(db: AsyncSession, *, include_inactive: bool = False) -> list[Service]:
    stmt = select(Service).where(Service.deleted_at.is_(None))
    if not include_inactive:
        stmt = stmt.where(Service.is_active.is_(True))
    rows = await db.execute(stmt.order_by(Service.sort_order, func.lower(Service.name)))
    return list(rows.scalars().all())


async def get_active(db: AsyncSession, service_id: int) -> Service:
    """Услуга для позиции сделки: существует, не удалена и продаётся."""
    service = await db.scalar(
        select(Service).where(Service.id == service_id, Service.deleted_at.is_(None))
    )
    if service is None:
        raise Invalid("Услуга не найдена в справочнике — выберите другую", field="items")
    if not service.is_active:
        raise Invalid(
            f"Услуга «{service.name}» снята с продажи — выберите другую", field="items"
        )
    return service


async def _get(db: AsyncSession, service_id: int) -> Service:
    service = await db.scalar(
        select(Service).where(Service.id == service_id, Service.deleted_at.is_(None))
    )
    if service is None:
        raise NotFound("Услуга не найдена")
    return service


async def _flush_unique(db: AsyncSession) -> None:
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise Conflict("Услуга с таким названием уже есть в справочнике") from exc


async def create_service(db: AsyncSession, admin: User, data: Any) -> Service:
    service = Service(
        name=_clean_name(data.name),
        price=_clean_price(data.price),
        description=(data.description or "").strip() or None,
        is_active=data.is_active,
        sort_order=data.sort_order,
    )
    db.add(service)
    await _flush_unique(db)
    await log_event(
        db,
        action="service.create",
        entity_type="service",
        entity_id=service.id,
        actor=admin,
        after=_payload(service),
    )
    await db.commit()
    await db.refresh(service)
    return service


async def update_service(db: AsyncSession, admin: User, service_id: int, data: Any) -> Service:
    service = await _get(db, service_id)
    before = _payload(service)
    fields = data.model_fields_set
    if "name" in fields:
        service.name = _clean_name(data.name)
    if "price" in fields:
        service.price = _clean_price(data.price)
    if "description" in fields:
        service.description = (data.description or "").strip() or None
    if "is_active" in fields and data.is_active is not None:
        service.is_active = data.is_active
    if "sort_order" in fields and data.sort_order is not None:
        service.sort_order = data.sort_order
    await _flush_unique(db)
    await log_event(
        db,
        action="service.update",
        entity_type="service",
        entity_id=service.id,
        actor=admin,
        before=before,
        after=_payload(service),
    )
    await db.commit()
    await db.refresh(service)
    return service


async def delete_service(db: AsyncSession, admin: User, service_id: int) -> None:
    service = await _get(db, service_id)
    service.deleted_at = datetime.now(UTC)
    await log_event(
        db,
        action="service.delete",
        entity_type="service",
        entity_id=service.id,
        actor=admin,
        before=_payload(service),
    )
    await db.commit()


async def suggestions(db: AsyncSession) -> list[dict[str, Any]]:
    """Названия из сделок за год, которых нет в справочнике: сколько раз и почём.

    Написание нормализуется (регистр, пробелы): «натальная карта» и
    «Натальная  карта» — одна услуга, показывается самое частое написание.
    Типичная цена — медиана по отправленным клиенту счетам: на неё не влияет
    одна случайная скидка.
    """
    since = datetime.now(UTC) - timedelta(days=SUGGESTION_DAYS)
    rows = await db.execute(
        text(
            """
            with items as (
                select regexp_replace(lower(trim(i.name)), '\\s+', ' ', 'g') as norm,
                       trim(i.name) as name, i.amount, d.created_at
                from deal_items i
                join deals d on d.id = i.deal_id
                where i.service_id is null
                  and d.status <> 'draft'
                  and d.created_at >= :since
            ),
            grouped as (
                select norm,
                       mode() within group (order by name) as name,
                       count(*) as uses,
                       percentile_disc(0.5) within group (order by amount) as typical_price,
                       max(created_at) as last_used_at
                from items
                group by norm
            )
            select g.name, g.uses, g.typical_price, g.last_used_at
            from grouped g
            where not exists (
                select 1 from services s
                where s.deleted_at is null
                  and regexp_replace(lower(trim(s.name)), '\\s+', ' ', 'g') = g.norm
            )
            order by g.uses desc, g.last_used_at desc
            limit :limit
            """
        ),
        {"since": since, "limit": SUGGESTION_LIMIT},
    )
    return [
        {
            "name": row.name,
            "uses": int(row.uses),
            "typical_price": int(row.typical_price) if row.typical_price is not None else None,
            "last_used_at": row.last_used_at,
        }
        for row in rows.all()
    ]
