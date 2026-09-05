"""Шаблоны сообщений: общие (ведёт руководитель) и личные (у каждого свои).

Чужой личный шаблон для менеджера — 404, а не 403: 403 подтверждает, что запись есть.
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Forbidden, Invalid, NotFound
from app.models import Template, User, UserRole
from app.schemas.template import TemplateCreate, TemplateUpdate
from app.services.audit import log_event


def payload(template: Template) -> dict[str, Any]:
    return {
        "id": template.id,
        "title": template.title,
        "text": template.text,
        "owner_id": template.owner_id,
        "is_personal": template.owner_id is not None,
        "sort_order": template.sort_order,
        "is_active": template.is_active,
        "created_at": template.created_at,
        "updated_at": template.updated_at,
    }


async def list_templates(db: AsyncSession, user: User) -> list[dict[str, Any]]:
    """Общие шаблоны видят все, личные — только их владелец."""
    rows = await db.execute(
        select(Template)
        .where(
            Template.deleted_at.is_(None),
            Template.is_active.is_(True),
            or_(Template.owner_id.is_(None), Template.owner_id == user.id),
        )
        .order_by(Template.sort_order, Template.id)
    )
    return [payload(template) for template in rows.scalars().all()]


async def _get_own(db: AsyncSession, user: User, template_id: int) -> Template:
    stmt = select(Template).where(Template.id == template_id, Template.deleted_at.is_(None))
    if user.role != UserRole.ADMIN:
        stmt = stmt.where(Template.owner_id == user.id)
    template = await db.scalar(stmt)
    if template is None:
        raise NotFound("Шаблон не найден")
    return template


async def create_template(
    db: AsyncSession, user: User, data: TemplateCreate
) -> dict[str, Any]:
    if not data.is_personal and user.role != UserRole.ADMIN:
        raise Forbidden("Общие шаблоны заводит только руководитель")
    title = data.title.strip()
    text = data.text.strip()
    if not title or not text:
        raise Invalid("Заполните название и текст шаблона")
    template = Template(
        title=title, text=text, sort_order=data.sort_order,
        owner_id=user.id if data.is_personal else None,
    )
    db.add(template)
    await db.flush()
    await log_event(
        db, action="template.create", entity_type="template", entity_id=template.id,
        actor=user, after={"title": title, "owner_id": template.owner_id},
    )
    await db.commit()
    return payload(template)


async def update_template(
    db: AsyncSession, user: User, template_id: int, data: TemplateUpdate
) -> dict[str, Any]:
    template = await _get_own(db, user, template_id)
    before = {"title": template.title, "is_active": template.is_active}
    changes = data.model_dump(exclude_unset=True)
    for field, value in changes.items():
        if field in ("title", "text"):
            value = (value or "").strip()
            if not value:
                raise Invalid("Заполните название и текст шаблона")
        setattr(template, field, value)
    await log_event(
        db, action="template.update", entity_type="template", entity_id=template.id,
        actor=user, before=before, after={"title": template.title, "is_active": template.is_active},
    )
    await db.commit()
    # updated_at пересчитан на стороне БД (onupdate=func.now()) — в отличие от
    # INSERT, обновление не возвращает его в объект само, и доступ к полю после
    # commit() требует явного запроса, которого здесь ждать нельзя (MissingGreenlet).
    await db.refresh(template)
    return payload(template)


async def delete_template(db: AsyncSession, user: User, template_id: int) -> None:
    template = await _get_own(db, user, template_id)
    template.deleted_at = datetime.now(UTC)
    await log_event(
        db, action="template.delete", entity_type="template", entity_id=template.id,
        actor=user, before={"title": template.title},
    )
    await db.commit()
