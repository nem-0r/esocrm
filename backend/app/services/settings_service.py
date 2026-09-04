"""Настройки системы. Меняются руководителем без релиза."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Setting
from app.models.misc import DEFAULT_SETTINGS


async def get_all(db: AsyncSession) -> dict[str, Any]:
    rows = await db.execute(select(Setting.key, Setting.value))
    stored = dict(rows.all())
    return {**DEFAULT_SETTINGS, **stored}


async def get_value(db: AsyncSession, key: str) -> Any:
    value = await db.scalar(select(Setting.value).where(Setting.key == key))
    return DEFAULT_SETTINGS.get(key) if value is None else value


async def set_values(db: AsyncSession, values: dict[str, Any], user_id: int | None) -> None:
    for key, value in values.items():
        if key not in DEFAULT_SETTINGS:
            continue
        stmt = insert(Setting).values(key=key, value=value, updated_by_id=user_id)
        await db.execute(
            stmt.on_conflict_do_update(
                index_elements=[Setting.key],
                set_={"value": value, "updated_by_id": user_id},
            )
        )
