"""Роутер управления сотрудниками. Раздел доступен только руководителю."""

from typing import Literal

from fastapi import APIRouter, Query

from app.core.deps import AdminUser, Db
from app.schemas.common import CursorPage
from app.schemas.user import ResendInviteOut, UserCreateIn, UserCreateOut, UserOut, UserUpdateIn
from app.services import user_service

router = APIRouter()


@router.get("", response_model=CursorPage[UserOut])
async def list_users(
    db: Db,
    _admin: AdminUser,
    q: str | None = Query(default=None, description="Поиск по имени и почте"),
    status: Literal["online", "offline"] | None = Query(default=None),
    no_account: bool = Query(default=False, description="Только сотрудники без аккаунтов"),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> CursorPage[UserOut]:
    return await user_service.list_users(
        db, q=q, status=status, no_account=no_account, cursor=cursor, limit=limit
    )


@router.post("", response_model=UserCreateOut, status_code=201)
async def create_user(payload: UserCreateIn, db: Db, admin: AdminUser) -> UserCreateOut:
    return await user_service.create_user(db, admin, payload)


@router.get("/{user_id}", response_model=UserOut)
async def get_user(user_id: int, db: Db, _admin: AdminUser) -> UserOut:
    return await user_service.get_user(db, user_id)


@router.patch("/{user_id}", response_model=UserOut)
async def update_user(user_id: int, payload: UserUpdateIn, db: Db, admin: AdminUser) -> UserOut:
    return await user_service.update_user(db, admin, user_id, payload)


@router.post("/{user_id}/resend-invite", response_model=ResendInviteOut)
async def resend_invite(user_id: int, db: Db, admin: AdminUser) -> ResendInviteOut:
    return await user_service.resend_invite(db, admin, user_id)
