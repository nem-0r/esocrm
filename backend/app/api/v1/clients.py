"""Раздел «Клиенты»: список, карточка, заметки, материалы, выгрузка в CSV.

Роутер тонкий: разбор запроса, вызов сервиса, отдача ответа — вся логика
в `client_service`. Маршрут `/export` объявлен раньше `/{client_id}`:
иначе слово «export» попало бы в путь как id и упало бы валидацией,
а не отдало файл.
"""

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from app.core.deps import CurrentUser, Db
from app.schemas.client import (
    ClientCard,
    ClientListRow,
    ClientUpdate,
    MaterialRow,
    NoteCreate,
    NoteRow,
)
from app.schemas.common import CursorPage, Ok
from app.services import client_service
from app.services.export_format import content_disposition

router = APIRouter()


@router.get("", response_model=CursorPage[ClientListRow], summary="Список клиентов")
async def list_clients(
    db: Db,
    user: CurrentUser,
    q: Annotated[str | None, Query(description="Поиск по имени, телефону, id")] = None,
    sort: Annotated[str, Query(description="last_contact | amount")] = "last_contact",
    cursor: Annotated[str | None, Query(description="Курсор следующей страницы")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> Any:
    return await client_service.list_clients(db, user, q=q, sort=sort, cursor=cursor, limit=limit)


@router.get("/export", summary="Выгрузить клиентов в CSV")
async def export_clients(
    db: Db,
    user: CurrentUser,
    date_from: Annotated[
        date | None, Query(description="Начало периода, по умолчанию год назад")
    ] = None,
    date_to: Annotated[date | None, Query(description="Конец периода, включительно")] = None,
) -> StreamingResponse:
    day = (date_to or date.today()).isoformat()
    return StreamingResponse(
        client_service.export_csv_rows(db, user, date_from, date_to),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": content_disposition(
                f"Клиенты {day}.csv", ascii_name=f"clients-{day}.csv"
            )
        },
    )


@router.get("/{client_id}", response_model=ClientCard, summary="Карточка клиента")
async def get_client(db: Db, user: CurrentUser, client_id: int) -> Any:
    return await client_service.get_client_card(db, user, client_id)


@router.patch("/{client_id}", response_model=ClientCard, summary="Изменить карточку клиента")
async def update_client(db: Db, user: CurrentUser, client_id: int, data: ClientUpdate) -> Any:
    return await client_service.update_client(db, user, client_id, data)


@router.get(
    "/{client_id}/notes", response_model=CursorPage[NoteRow], summary="Заметки о клиенте"
)
async def list_notes(
    db: Db,
    user: CurrentUser,
    client_id: int,
    cursor: Annotated[str | None, Query(description="Курсор следующей страницы")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> Any:
    return await client_service.list_notes(db, user, client_id, cursor=cursor, limit=limit)


@router.post(
    "/{client_id}/notes", response_model=NoteRow, status_code=201, summary="Добавить заметку"
)
async def create_note(db: Db, user: CurrentUser, client_id: int, data: NoteCreate) -> Any:
    return await client_service.create_note(db, user, client_id, data.text)


@router.delete("/{client_id}/notes/{note_id}", response_model=Ok, summary="Удалить заметку")
async def delete_note(db: Db, user: CurrentUser, client_id: int, note_id: int) -> Any:
    await client_service.delete_note(db, user, client_id, note_id)
    return Ok()


@router.get(
    "/{client_id}/materials",
    response_model=CursorPage[MaterialRow],
    summary="Материалы клиента: вложения из переписки",
)
async def list_materials(
    db: Db,
    user: CurrentUser,
    client_id: int,
    cursor: Annotated[str | None, Query(description="Курсор следующей страницы")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> Any:
    return await client_service.list_materials(db, user, client_id, cursor=cursor, limit=limit)
