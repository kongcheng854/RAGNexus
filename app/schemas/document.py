from typing import Annotated
from uuid import UUID

from pydantic import AfterValidator, BaseModel, Field

from app.models import IndexStatus
from app.schemas.common import Identifier, Title, validate_text


class DocumentUpload(BaseModel):
    kb_id: UUID
    title: Title
    content: Annotated[str, Field(max_length=100_000), AfterValidator(validate_text)]


class DocumentUploadInput(DocumentUpload):
    tenant_id: Identifier


class DocumentResult(BaseModel):
    document_id: UUID
    kb_id: UUID
    status: IndexStatus
    chunk_count: int
