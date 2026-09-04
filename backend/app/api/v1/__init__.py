from fastapi import APIRouter

from app.api.v1 import (
    accounts,
    auth,
    clients,
    conversations,
    deals,
    files,
    messages,
    notifications,
    payments,
    requisites,
    search,
    settings,
    stats,
    templates,
    users,
    ws,
)

api_router = APIRouter()
api_router.include_router(auth.router, prefix="/auth", tags=["вход"])
api_router.include_router(users.router, prefix="/users", tags=["сотрудники"])
api_router.include_router(accounts.router, prefix="/accounts", tags=["аккаунты"])
api_router.include_router(conversations.router, prefix="/conversations", tags=["чаты"])
api_router.include_router(messages.router, prefix="/conversations", tags=["чаты"])
api_router.include_router(clients.router, prefix="/clients", tags=["клиенты"])
api_router.include_router(deals.router, prefix="/deals", tags=["оплаты"])
api_router.include_router(requisites.router, prefix="/requisites", tags=["справочники"])
api_router.include_router(templates.router, prefix="/templates", tags=["справочники"])
api_router.include_router(stats.router, prefix="/stats", tags=["статистика"])
api_router.include_router(search.router, prefix="/search", tags=["поиск"])
api_router.include_router(notifications.router, prefix="/notifications", tags=["уведомления"])
# Публичный, без авторизации — сюда стучится Робокасса, а не сотрудник CRM.
api_router.include_router(payments.router, prefix="/payments", tags=["оплаты (провайдер)"])
api_router.include_router(settings.router, prefix="/settings", tags=["настройки"])
api_router.include_router(files.router, prefix="/files", tags=["файлы"])
api_router.include_router(ws.router, tags=["живые обновления"])
