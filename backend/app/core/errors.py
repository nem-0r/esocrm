from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse


class AppError(HTTPException):
    """Ошибка с машинным кодом и человеческим текстом на русском."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(status_code=status_code, detail=message)
        self.code = code
        self.message = message
        self.details = details or {}


class NotFound(AppError):
    def __init__(self, message: str = "Запись не найдена", code: str = "not_found") -> None:
        super().__init__(404, code, message)


class Forbidden(AppError):
    def __init__(self, message: str = "Недостаточно прав", code: str = "forbidden") -> None:
        super().__init__(403, code, message)


class Unauthorized(AppError):
    def __init__(self, message: str = "Требуется вход", code: str = "unauthorized") -> None:
        super().__init__(401, code, message)


class Conflict(AppError):
    def __init__(self, message: str, code: str = "conflict", **details: Any) -> None:
        super().__init__(409, code, message, details)


class Invalid(AppError):
    def __init__(self, message: str, code: str = "invalid", **details: Any) -> None:
        super().__init__(422, code, message, details)


def error_body(code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details or {}}}


async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code, content=error_body(exc.code, exc.message, exc.details)
    )


async def http_error_handler(_: Request, exc: HTTPException) -> JSONResponse:
    generic = {
        401: ("unauthorized", "Требуется вход"),
        403: ("forbidden", "Недостаточно прав"),
        404: ("not_found", "Страница не найдена"),
        429: ("rate_limited", "Слишком много запросов, попробуйте позже"),
    }
    code, message = generic.get(exc.status_code, ("error", str(exc.detail)))
    return JSONResponse(status_code=exc.status_code, content=error_body(code, message))


async def validation_error_handler(_: Request, exc: Exception) -> JSONResponse:
    """FastAPI отдаёт свои ошибки проверки в другом формате, и интерфейс их
    не разбирает — пользователь видит общее «не удалось», хотя сервер точно
    знает, какое поле не так. Приводим к общему виду и переводим."""
    errors = getattr(exc, "errors", lambda: [])()
    first = errors[0] if errors else {}
    location = [str(part) for part in first.get("loc", []) if part not in ("body", "query")]
    field = ".".join(location) if location else None
    kind = first.get("type", "")

    # Свои проверки пишут человеческий текст («Время указывается в формате ЧЧ:ММ»).
    # Pydantic префиксует его «Value error, » — снимаем префикс и показываем как есть,
    # иначе пользователь вместо подсказки видит «неверное значение поля start».
    own = first.get("msg", "")
    if kind in ("value_error", "assertion_error") and own:
        message = own.removeprefix("Value error, ").removeprefix("Assertion failed, ")
    elif kind == "missing":
        message = f"Не заполнено поле «{field}»" if field else "Не хватает данных в запросе"
    elif field:
        message = f"Неверное значение поля «{field}»"
    else:
        message = "Запрос заполнен неверно"

    # В подробностях pydantic держит исходное исключение в ctx — оно не сериализуется
    # в JSON и роняло весь ответ в 500. Оставляем только то, что интерфейс умеет читать.
    details = [
        {
            "field": ".".join(str(p) for p in err.get("loc", []) if p not in ("body", "query")),
            "type": err.get("type", ""),
            "message": err.get("msg", ""),
        }
        for err in errors[:5]
    ]
    return JSONResponse(
        status_code=422,
        content=error_body("validation", message, {"field": field, "errors": details}),
    )
