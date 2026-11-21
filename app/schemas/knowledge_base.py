from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AfterValidator, BaseModel, Field

from app.schemas.common import Identifier, Title, validate_text


class KnowledgeBaseCreate(BaseModel):
    name: Title
    description: Annotated[str, Field(max_length=10_000), AfterValidator(validate_text)] | None = None


class KnowledgeBaseCreateInput(KnowledgeBaseCreate):
    tenant_id: Identifier


class KnowledgeBaseResult(BaseModel):
    kb_id: UUID
    tenant_id: str
    name: str
    created_at: datetime


class TreeDocument(BaseModel):
    document_id: UUID
    title: str
    status: Literal["SUCCESS"] = "SUCCESS"
    chunk_count: int
    created_at: datetime


class TreeKnowledgeBase(BaseModel):
    kb_id: UUID
    name: str
    description: str | None
    created_at: datetime
    documents: list[TreeDocument] = Field(default_factory=list)


class TenantKnowledgeTree(BaseModel):
    tenant_id: str
    knowledge_bases: list[TreeKnowledgeBase] = Field(default_factory=list)
