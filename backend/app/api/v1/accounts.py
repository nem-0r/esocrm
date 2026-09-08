"""Роутер Telegram-аккаунтов.

Чтение доступно любому вошедшему (менеджер видит только свои аккаунты — это
считает сервис), запись — только руководителю: `app.core.deps.AdminUser`.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Query

from app.core.deps import AdminUser, CurrentUser, Db
from app.schemas.account import (
    AccountCreate,
    AccountRow,
    AccountSummary,
    AccountUpdate,
    ConfirmCodeIn,
    ConfirmCodeOut,
    ManagersIn,
    QrPasswordIn,
    QrStartOut,
    QrStateOut,
    SendCodeOut,
)
from app.schemas.common import Ok
from app.services import account_service

router = APIRouter()


@router.get("", response_model=list[AccountRow], summary="Аккаунты со статусом")
async def list_accounts(
    db: Db,
    user: CurrentUser,
    only_attention: Annotated[
        bool, Query(description="Только требующие внимания: ошибка/ожидание или без менеджера")
    ] = False,
) -> Any:
    return await account_service.list_accounts(db, user, only_attention=only_attention)


@router.get("/summary", response_model=AccountSummary, summary="Плитки над списком аккаунтов")
async def accounts_summary(db: Db, user: CurrentUser) -> Any:
    return await account_service.summary(db, user)


@router.post("", response_model=AccountRow, status_code=201, summary="Подключить аккаунт")
async def create_account(db: Db, admin: AdminUser, data: AccountCreate) -> Any:
    return await account_service.create_account(db, admin, data)


@router.post("/{account_id}/send-code", response_model=SendCodeOut, summary="Запросить код входа")
async def send_code(db: Db, admin: AdminUser, account_id: int) -> Any:
    return await account_service.send_code(db, admin, account_id)


@router.post(
    "/{account_id}/confirm-code", response_model=ConfirmCodeOut, summary="Подтвердить код входа"
)
async def confirm_code(db: Db, admin: AdminUser, account_id: int, data: ConfirmCodeIn) -> Any:
    return await account_service.confirm_code(db, admin, account_id, data)


@router.post("/{account_id}/qr/start", response_model=QrStartOut, summary="Показать QR для входа")
async def qr_start(db: Db, admin: AdminUser, account_id: int) -> Any:
    return await account_service.qr_start(db, admin, account_id)


@router.get("/{account_id}/qr/state", response_model=QrStateOut, summary="Состояние входа по QR")
async def qr_state(db: Db, admin: AdminUser, account_id: int) -> Any:
    return await account_service.qr_state(db, admin, account_id)


@router.post(
    "/{account_id}/qr/password",
    response_model=ConfirmCodeOut,
    summary="Облачный пароль при входе по QR",
)
async def qr_password(db: Db, admin: AdminUser, account_id: int, data: QrPasswordIn) -> Any:
    return await account_service.qr_password(db, admin, account_id, data)


@router.post(
    "/{account_id}/sync-history",
    summary="Подтянуть переписку из Telegram",
    description=(
        "Запускает подтяжку истории на шлюзе. Работа идёт фоном, ход виден "
        "событиями `account.sync`; ответ означает «принято в работу», а не «готово»."
    ),
)
async def sync_history(db: Db, admin: AdminUser, account_id: int) -> Any:
    return await account_service.sync_history(db, admin, account_id)


@router.post("/{account_id}/managers", response_model=AccountRow, summary="Назначить менеджеров")
async def set_managers(db: Db, admin: AdminUser, account_id: int, data: ManagersIn) -> Any:
    return await account_service.set_managers(db, admin, account_id, data)


@router.patch("/{account_id}", response_model=AccountRow, summary="Изменить аккаунт")
async def update_account(db: Db, admin: AdminUser, account_id: int, data: AccountUpdate) -> Any:
    return await account_service.update_account(db, admin, account_id, data)


@router.delete("/{account_id}", response_model=Ok, summary="Отключить аккаунт")
async def delete_account(db: Db, admin: AdminUser, account_id: int) -> Any:
    await account_service.delete_account(db, admin, account_id)
    return Ok()
