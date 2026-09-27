"""Справочник услуг. Читают все (выбор в окне оплаты), меняет только руководитель."""

from typing import Annotated, Any

from fastapi import APIRouter, Query

from app.core.deps import AdminUser, CurrentUser, Db
from app.models import UserRole
from app.schemas.common import Ok
from app.schemas.service import ServiceCreate, ServiceOut, ServiceSuggestion, ServiceUpdate
from app.services import service_catalog

router = APIRouter()


@router.get("", response_model=list[ServiceOut], summary="Услуги и цены")
async def list_services(
    db: Db,
    user: CurrentUser,
    include_inactive: Annotated[bool, Query(description="Показывать снятые с продажи")] = False,
) -> Any:
    # Снятые с продажи нужны только в справочнике руководителя.
    show_all = include_inactive and user.role == UserRole.ADMIN
    return await service_catalog.list_services(db, include_inactive=show_all)


@router.get(
    "/suggestions",
    response_model=list[ServiceSuggestion],
    summary="Названия из прошлых сделок, которых нет в справочнике",
)
async def suggestions(db: Db, admin: AdminUser) -> Any:
    return await service_catalog.suggestions(db)


@router.post("", response_model=ServiceOut, status_code=201, summary="Добавить услугу")
async def create_service(db: Db, admin: AdminUser, data: ServiceCreate) -> Any:
    return await service_catalog.create_service(db, admin, data)


@router.patch("/{service_id}", response_model=ServiceOut, summary="Изменить услугу")
async def update_service(db: Db, admin: AdminUser, service_id: int, data: ServiceUpdate) -> Any:
    return await service_catalog.update_service(db, admin, service_id, data)


@router.delete("/{service_id}", response_model=Ok, summary="Удалить услугу")
async def delete_service(db: Db, admin: AdminUser, service_id: int) -> Any:
    await service_catalog.delete_service(db, admin, service_id)
    return Ok()
