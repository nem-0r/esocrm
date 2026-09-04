import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import ORJSONResponse

from app.core.config import settings
from app.core.errors import (
    AppError,
    app_error_handler,
    http_error_handler,
    validation_error_handler,
)
from app.core.storage import ensure_bucket

# Время и имя логгера в каждой строке: в продакшене логи читают через
# `docker compose logs`, где без метки времени непонятно, когда это случилось,
# а при двух воркерах uvicorn — ещё и от кого пришла строка.
logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s %(levelname)s [%(process)d] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("astra")


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        ensure_bucket()
    except Exception as exc:  # хранилище может подниматься дольше
        log.warning("Хранилище файлов пока недоступно: %s", exc)

    from app.realtime.hub import hub

    await hub.start()
    log.info("API запущен. Демо-режим: %s", settings.demo_mode)
    yield
    await hub.stop()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Астра CRM",
        version="0.1.0",
        default_response_class=ORJSONResponse,
        lifespan=lifespan,
        docs_url="/docs",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(HTTPException, http_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)

    from app.api.v1 import api_router

    app.include_router(api_router, prefix="/api/v1")

    @app.get("/health", tags=["служебное"])
    async def health() -> dict[str, object]:
        return {"status": "ok", "demo_mode": settings.demo_mode}

    return app


app = create_app()
