"""Состояние сервера — только руководителю."""

from typing import Any

from fastapi import APIRouter

from app.core.deps import AdminUser, Db
from app.services import system_status

router = APIRouter()


@router.get("/status", summary="Состояние сервера: диск, нагрузка, шлюз, план ресурсов")
async def status(db: Db, admin: AdminUser) -> dict[str, Any]:
    _ = admin
    return await system_status.build(db)
