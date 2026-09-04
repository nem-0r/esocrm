"""Шаблоны сообщений: общие и личные."""

from typing import Any

from fastapi import APIRouter

from app.core.deps import CurrentUser, Db
from app.schemas.common import Ok
from app.schemas.template import TemplateCreate, TemplateOut, TemplateUpdate
from app.services import template_service

router = APIRouter()


@router.get("", response_model=list[TemplateOut], summary="Доступные шаблоны")
async def list_templates(db: Db, user: CurrentUser) -> Any:
    return await template_service.list_templates(db, user)


@router.post("", response_model=TemplateOut, status_code=201, summary="Создать шаблон")
async def create_template(db: Db, user: CurrentUser, data: TemplateCreate) -> Any:
    return await template_service.create_template(db, user, data)


@router.patch("/{template_id}", response_model=TemplateOut, summary="Изменить шаблон")
async def update_template(
    db: Db, user: CurrentUser, template_id: int, data: TemplateUpdate
) -> Any:
    return await template_service.update_template(db, user, template_id, data)


@router.delete("/{template_id}", response_model=Ok, summary="Удалить шаблон")
async def delete_template(db: Db, user: CurrentUser, template_id: int) -> Any:
    await template_service.delete_template(db, user, template_id)
    return Ok()
