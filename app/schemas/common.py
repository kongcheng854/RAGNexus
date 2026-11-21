from typing import Annotated, Generic, TypeVar

from pydantic import AfterValidator, BaseModel, Field

from app.core.error_codes import ErrorCode


def validate_text(value: str) -> str:
    if not value.strip() or "\x00" in value:
        raise ValueError("text must be non-blank and cannot contain null characters")
    return value


Identifier = Annotated[str, Field(max_length=128), AfterValidator(validate_text)]
Title = Annotated[str, Field(max_length=255), AfterValidator(validate_text)]
T = TypeVar("T")


class ResponseEnvelope(BaseModel, Generic[T]):
    code: int = int(ErrorCode.SUCCESS)
    msg: str = ErrorCode.SUCCESS.message
    data: T | None = None
