from typing import Any, NoReturn

from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError, TimeoutError

from app.core.error_codes import ErrorCode


class ServiceError(Exception):
    def __init__(
        self,
        error: ErrorCode,
        *,
        detail: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(detail or error.message)
        self.error = error
        self.data = data


class AppError(ServiceError):
    pass


def raise_error(error: ErrorCode, *, detail: str | None = None) -> NoReturn:
    raise ServiceError(error, detail=detail)


class DocumentParseError(ServiceError):
    def __init__(self, detail: str) -> None:
        super().__init__(ErrorCode.DOCUMENT_PARSE_FAILED, detail=detail)


class EmbeddingError(ServiceError):
    def __init__(
        self, detail: str | None = None, *, error: ErrorCode = ErrorCode.LLM_ERROR
    ) -> None:
        super().__init__(error, detail=detail)


def database_error_code(exc: SQLAlchemyError) -> ErrorCode:
    if isinstance(exc, IntegrityError):
        if getattr(exc.orig, "sqlstate", None) == "23505":
            return ErrorCode.DATA_DUPLICATE
        return ErrorCode.DATA_CONFLICT
    if isinstance(exc, TimeoutError) or getattr(getattr(exc, "orig", None), "sqlstate", None) == "57014":
        return ErrorCode.DB_TIMEOUT
    if isinstance(exc, OperationalError):
        return ErrorCode.DB_UNAVAILABLE
    return ErrorCode.DB_ERROR
