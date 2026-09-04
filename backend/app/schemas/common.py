"""Общие схемы ответов.

Пагинация курсорная: на 50 000 записей постраничность по смещению начинает тормозить.
Курсор — это непрозрачная для клиента строка, внутри — id последней записи.
"""

from pydantic import BaseModel, ConfigDict


class ApiModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class CursorPage[T](BaseModel):
    items: list[T]
    next_cursor: str | None = None
    total: int | None = None


class Ok(BaseModel):
    ok: bool = True


def encode_cursor(value: int | None) -> str | None:
    return str(value) if value is not None else None


def decode_cursor(cursor: str | None) -> int | None:
    if not cursor:
        return None
    try:
        return int(cursor)
    except ValueError:
        return None
