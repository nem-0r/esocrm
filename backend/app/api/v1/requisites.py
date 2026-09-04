"""Справочник счетов получателя. Читают все, меняет только руководитель."""

from typing import Annotated, Any

from fastapi import APIRouter, Query

from app.core.deps import AdminUser, CurrentUser, Db
from app.schemas.common import Ok
from app.schemas.requisite import RequisiteCreate, RequisiteOut, RequisiteUpdate
from app.services import requisite_service

router = APIRouter()


@router.get("", response_model=list[RequisiteOut], summary="Счета получателя")
async def list_requisites(
    db: Db,
    user: CurrentUser,
    include_inactive: Annotated[bool, Query(description="Показывать отключённые")] = False,
) -> Any:
    return await requisite_service.list_requisites(db, include_inactive=include_inactive)


@router.post("", response_model=RequisiteOut, status_code=201, summary="Добавить счёт")
async def create_requisite(db: Db, admin: AdminUser, data: RequisiteCreate) -> Any:
    return await requisite_service.create_requisite(db, admin, data)


@router.patch("/{requisite_id}", response_model=RequisiteOut, summary="Изменить счёт")
async def update_requisite(
    db: Db, admin: AdminUser, requisite_id: int, data: RequisiteUpdate
) -> Any:
    return await requisite_service.update_requisite(db, admin, requisite_id, data)


@router.delete("/{requisite_id}", response_model=Ok, summary="Удалить счёт")
async def delete_requisite(db: Db, admin: AdminUser, requisite_id: int) -> Any:
    await requisite_service.delete_requisite(db, admin, requisite_id)
    return Ok()
