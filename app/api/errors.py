import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException

from app.core.error_codes import HTTP_ERROR_CODES, ErrorCode
from app.core.exceptions import ServiceError, database_error_code
from app.core.logging import log_event
from app.schemas.common import ResponseEnvelope


logger = logging.getLogger(__name__)


def error_response(
    request: Request,
    error: ErrorCode,
    data: dict[str, Any] | None = None,
    *,
    status: int | None = None,
    message: str | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    request.state.error_code = int(error)
    body = ResponseEnvelope(code=int(error), msg=message or error.message, data=data)
    return JSONResponse(body.model_dump(mode="json"), status_code=status or error.status, headers=headers)


def log_exception(request: Request, exc: Exception, error: ErrorCode) -> None:
    log_event(
        logger, logging.ERROR, "REQUEST_ERROR", exc_info=exc,
        method=request.method, path=request.url.path,
        exception_type=type(exc).__name__, code=int(error),
    )


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ServiceError)
    async def service_error(request: Request, exc: ServiceError) -> JSONResponse:
        if exc.error.status >= 500 or 30000 <= exc.error < 50000:
            log_exception(request, exc, exc.error)
        headers = {"WWW-Authenticate": "Bearer"} if exc.error == ErrorCode.UNAUTHORIZED else None
        message = str(exc) if exc.error in (ErrorCode.QUOTA_EXCEEDED, ErrorCode.PARAM_ERROR) else None
        return error_response(request, exc.error, exc.data, headers=headers, message=message)

    @app.exception_handler(SQLAlchemyError)
    async def storage_error(request: Request, exc: SQLAlchemyError) -> JSONResponse:
        error = database_error_code(exc)
        log_exception(request, exc, error)
        return error_response(request, error)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        fields = [
            {"field": ".".join(map(str, issue["loc"])), "message": issue["msg"]}
            for issue in exc.errors()
        ]
        return error_response(request, ErrorCode.PARAM_ERROR, {"errors": fields})

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        fallback = ErrorCode.SERVER_ERROR if exc.status_code >= 500 else ErrorCode.API_REQUEST_ERROR
        error = HTTP_ERROR_CODES.get(exc.status_code, fallback)
        if exc.status_code >= 500:
            log_exception(request, exc, error)
        return error_response(request, error, status=exc.status_code, headers=exc.headers)
