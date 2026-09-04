"""Настройки системы. Меняются руководителем без релиза."""

from datetime import date, time
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter
from pydantic import BaseModel

from app.core.deps import AdminUser, CurrentUser, Db
from app.core.errors import Invalid
from app.services import settings_service
from app.services.audit import log_event

# Значения — из документации Робокассы на формат чека по 54-ФЗ. Другие строки
# JSON-чек не примет, и Робокасса молча отклонит уведомление об оплате.
ROBOKASSA_SNO_VALUES = {"osn", "usn_income", "usn_income_outcome", "envd", "esn", "patent"}
ROBOKASSA_TAX_VALUES = {"none", "vat0", "vat10", "vat20", "vat110", "vat120"}

router = APIRouter()


class SettingsPatch(BaseModel):
    """Все поля необязательные: интерфейс шлёт только изменённые."""

    awaiting_banner_minutes: int | None = None
    response_time_goal_minutes: int | None = None
    deal_link_ttl_days: int | None = None
    working_hours_enabled: bool | None = None
    working_hours_start: str | None = None
    working_hours_end: str | None = None
    working_days: list[int] | None = None
    timezone: str | None = None
    history_sync_days: int | None = None
    history_sync_from: str | None = None
    robokassa_sno: str | None = None
    robokassa_tax: str | None = None


def _check_minutes(name: str, value: int, label: str) -> None:
    if not 1 <= value <= 1440:
        raise Invalid(f"{label}: допустимо от 1 до 1440 минут", details={"field": name})


def _check_time(name: str, value: str, label: str) -> None:
    try:
        hours, minutes = value.split(":")
        time(int(hours), int(minutes))
    except (ValueError, TypeError) as exc:
        raise Invalid(f"{label}: укажите время в формате ЧЧ:ММ", details={"field": name}) from exc


def validate(payload: dict[str, Any]) -> dict[str, Any]:
    if (value := payload.get("awaiting_banner_minutes")) is not None:
        _check_minutes("awaiting_banner_minutes", value, "Порог баннера")
    if (value := payload.get("response_time_goal_minutes")) is not None:
        _check_minutes("response_time_goal_minutes", value, "Цель по времени ответа")
    if (value := payload.get("deal_link_ttl_days")) is not None and not 1 <= value <= 90:
        raise Invalid(
            "Срок действия оплаты: допустимо от 1 до 90 дней",
            details={"field": "deal_link_ttl_days"},
        )
    if (value := payload.get("working_hours_start")) is not None:
        _check_time("working_hours_start", value, "Начало рабочего дня")
    if (value := payload.get("working_hours_end")) is not None:
        _check_time("working_hours_end", value, "Конец рабочего дня")
    days = payload.get("working_days")
    if days is not None and (
        not days or any(not isinstance(day, int) or not 1 <= day <= 7 for day in days)
    ):
        raise Invalid(
            "Рабочие дни: выберите хотя бы один день недели",
            details={"field": "working_days"},
        )
    if (value := payload.get("timezone")) is not None:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise Invalid(
                "Часовой пояс не распознан", details={"field": "timezone"}
            ) from exc
    if (value := payload.get("history_sync_from")) is not None:
        try:
            date.fromisoformat(value)
        except (ValueError, TypeError) as exc:
            raise Invalid(
                "Начало истории: укажите дату в формате ГГГГ-ММ-ДД",
                details={"field": "history_sync_from"},
            ) from exc
    if (value := payload.get("history_sync_days")) is not None and not 1 <= value <= 3650:
        raise Invalid(
            "Глубина импорта: допустимо от 1 до 3650 дней",
            details={"field": "history_sync_days"},
        )
    if (value := payload.get("robokassa_sno")) is not None and value not in ROBOKASSA_SNO_VALUES:
        raise Invalid(
            "Система налогообложения: недопустимое значение",
            details={"field": "robokassa_sno"},
        )
    if (value := payload.get("robokassa_tax")) is not None and value not in ROBOKASSA_TAX_VALUES:
        raise Invalid(
            "Ставка НДС: недопустимое значение",
            details={"field": "robokassa_tax"},
        )
    return payload


@router.get("", summary="Настройки системы")
async def read_settings(db: Db, user: CurrentUser) -> dict[str, Any]:
    """Читать может любой сотрудник: интерфейсу нужен порог баннера и цель по ответу."""
    _ = user
    return await settings_service.get_all(db)


@router.patch("", summary="Изменить настройки")
async def update_settings(db: Db, user: AdminUser, payload: SettingsPatch) -> dict[str, Any]:
    changes = validate(payload.model_dump(exclude_none=True))
    if not changes:
        return await settings_service.get_all(db)

    before = await settings_service.get_all(db)
    await settings_service.set_values(db, changes, user.id)
    await log_event(
        db,
        action="settings.updated",
        entity_type="settings",
        actor=user,
        before={k: before.get(k) for k in changes},
        after=changes,
    )
    await db.commit()
    return await settings_service.get_all(db)
