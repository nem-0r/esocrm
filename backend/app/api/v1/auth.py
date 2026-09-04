"""Роутер аутентификации: вход, выход, профиль, приглашения, присутствие.

Роутер тонкий: разбирает запрос, ставит/снимает cookie сессии, зовёт сервис.
Вся логика — в app.services.auth_service.
"""

from fastapi import APIRouter, Cookie, Request, Response

from app.core.config import settings
from app.core.deps import SESSION_COOKIE, CurrentUser, Db
from app.schemas.auth import AcceptInviteIn, LoginIn, LoginOut, MeUpdateIn
from app.schemas.common import Ok
from app.schemas.user import MeOut
from app.services import auth_service

router = APIRouter()


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        max_age=settings.session_ttl_days * 86400,
        path="/",
    )


def _bearer(request: Request) -> str | None:
    header = request.headers.get("Authorization", "")
    return header[7:] if header.startswith("Bearer ") else None


def _current_token(request: Request, astra_session: str | None) -> str | None:
    return astra_session or _bearer(request)


@router.post("/login", response_model=LoginOut)
async def login(payload: LoginIn, request: Request, response: Response, db: Db) -> LoginOut:
    user, token = await auth_service.login(db, request, payload.email, payload.password)
    _set_session_cookie(response, token)
    me = await auth_service.build_me(db, user)
    return LoginOut(**me.model_dump(), token=token)


@router.post("/logout", response_model=Ok)
async def logout(
    request: Request,
    response: Response,
    db: Db,
    user: CurrentUser,
    astra_session: str | None = Cookie(default=None, alias=SESSION_COOKIE),
) -> Ok:
    token = _current_token(request, astra_session)
    if token:
        await auth_service.logout(db, request, user, token)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return Ok()


@router.get("/me", response_model=MeOut)
async def me(db: Db, user: CurrentUser) -> MeOut:
    return await auth_service.build_me(db, user)


@router.patch("/me", response_model=MeOut)
async def update_me(
    payload: MeUpdateIn,
    request: Request,
    db: Db,
    user: CurrentUser,
    astra_session: str | None = Cookie(default=None, alias=SESSION_COOKIE),
) -> MeOut:
    token = _current_token(request, astra_session) or ""
    updated = await auth_service.update_me(
        db,
        request,
        user,
        token,
        accepting_leads=payload.accepting_leads,
        current_password=payload.current_password,
        new_password=payload.new_password,
    )
    return await auth_service.build_me(db, updated)


@router.post("/accept-invite", response_model=LoginOut)
async def accept_invite(
    payload: AcceptInviteIn, request: Request, response: Response, db: Db
) -> LoginOut:
    user, token = await auth_service.accept_invite(db, request, payload.token, payload.password)
    _set_session_cookie(response, token)
    me_payload = await auth_service.build_me(db, user)
    return LoginOut(**me_payload.model_dump(), token=token)


@router.post("/heartbeat", response_model=Ok)
async def heartbeat(db: Db, user: CurrentUser) -> Ok:
    await auth_service.heartbeat(db, user)
    return Ok()
