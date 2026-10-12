"""Выгрузка клиентов проверяет период так же, как выгрузка оплат.

Раньше `/clients/export` не вызывал `check_period`: «с» позже «по» молча давало
пустой файл, а год 0001 или 9999 падал с `OverflowError` уже ВНУТРИ потока ответа,
когда заголовки были отправлены, — клиент получал оборванный файл, а не 422.
"""

from collections.abc import AsyncIterator
from datetime import UTC
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.v1 import clients as clients_api
from app.core.db import get_db
from app.core.deps import get_current_user
from app.core.errors import AppError, app_error_handler
from app.services import client_service


@pytest.fixture
def export_app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    app = FastAPI()
    app.include_router(clients_api.router, prefix="/clients")
    app.add_exception_handler(AppError, app_error_handler)  # type: ignore[arg-type]
    calls: list[tuple] = []

    async def fake_rows(_db: object, _user: object, date_from: object, date_to: object):
        calls.append((date_from, date_to))
        yield "id\n"

    async def fake_zone(_db: object) -> object:
        return UTC

    async def fake_user() -> SimpleNamespace:
        return SimpleNamespace(id=1)

    async def fake_db() -> AsyncIterator[None]:
        yield None

    monkeypatch.setattr(client_service, "export_csv_rows", fake_rows)
    monkeypatch.setattr(clients_api, "local_zone", fake_zone)
    app.dependency_overrides[get_current_user] = fake_user
    app.dependency_overrides[get_db] = fake_db
    app.state.calls = calls
    return app


async def _get(app: FastAPI, **params: str):  # noqa: ANN202
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.get("/clients/export", params=params)


@pytest.mark.parametrize(
    "params",
    [
        {"date_from": "2026-10-08", "date_to": "2026-10-07"},
        {"date_from": "0001-01-01"},
        {"date_to": "9999-12-31"},
        {"date_from": "1899-12-31", "date_to": "2026-10-07"},
        {"date_from": "2026-10-07", "date_to": "2101-01-01"},
    ],
)
async def test_bad_period_is_422_invalid_and_nothing_is_streamed(
    export_app: FastAPI, params: dict[str, str]
) -> None:
    response = await _get(export_app, **params)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid"
    assert export_app.state.calls == []


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"date_from": "2026-10-07", "date_to": "2026-10-07"},
        {"date_from": "1900-01-01", "date_to": "2100-12-31"},
        {"date_from": "2026-01-01"},
    ],
)
async def test_good_period_is_streamed(export_app: FastAPI, params: dict[str, str]) -> None:
    response = await _get(export_app, **params)
    assert response.status_code == 200
    assert response.text == "id\n"
    assert len(export_app.state.calls) == 1
