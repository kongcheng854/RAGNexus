from typing import Annotated
from uuid import UUID

from pydantic import AfterValidator, BaseModel, Field, ValidationError, field_validator

from app.core.error_codes import ErrorCode
from app.core.exceptions import AppError
from app.schemas.common import validate_text


class FeedbackRequest(BaseModel):
    trace_id: Annotated[str, Field(max_length=64), AfterValidator(validate_text)]
    log_id: UUID | None = None
    score: Annotated[int, Field(strict=True, ge=1, le=5)]
    comment: str | None = None

    @field_validator("score", mode="wrap")
    @classmethod
    def validate_score(cls, value, handler):
        try:
            return handler(value)
        except ValidationError as exc:
            raise AppError(ErrorCode.FEEDBACK_INVALID_SCORE) from exc


class FeedbackData(BaseModel):
    feedback_id: str
    trace_id: str
    log_id: UUID
    score: int
