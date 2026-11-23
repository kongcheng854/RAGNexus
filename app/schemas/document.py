from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import AfterValidator, BaseModel, Field

from app.models import IndexStatus
from app.schemas.common import Identifier, Title, validate_text


class DocumentFileFields(BaseModel):
    kb_id: UUID
    title: Title


class DocumentUpload(DocumentFileFields):
    content: Annotated[str, Field(max_length=100_000), AfterValidator(validate_text)]


class DocumentUploadInput(DocumentUpload):
    tenant_id: Identifier


class DocumentResult(BaseModel):
    document_id: UUID
    kb_id: UUID
    status: IndexStatus
    chunk_count: int


class DocumentDetail(DocumentResult):
    title: str
    error_message: str | None
    created_at: datetime
    updated_at: datetime
