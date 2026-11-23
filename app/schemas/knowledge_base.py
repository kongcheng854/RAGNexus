from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.schemas.common import Identifier, Title, validate_text


Word = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1), AfterValidator(validate_text)]


class SynonymGroup(BaseModel):
    terms: list[Word] = Field(min_length=1)
    expand: list[Word] = Field(min_length=1)


class KnowledgeSettings(BaseModel):
    model_config = ConfigDict(extra="allow")
    synonyms: list[SynonymGroup] = Field(default_factory=list)
    rewrite_hint: str = ""


Description = Annotated[str, Field(max_length=10_000), AfterValidator(validate_text)]


class KnowledgeBasePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Title | None = None
    description: Description | None = None
    settings: KnowledgeSettings | None = None

    @model_validator(mode="after")
    def validate_changes(self):
        if not self.model_fields_set:
            raise ValueError("至少填写一个更新字段")
        for field in ("name", "settings"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} 不能为空")
        return self


class KnowledgeBaseCreate(BaseModel):
    name: Title
    description: Description | None = None


class KnowledgeBaseCreateInput(KnowledgeBaseCreate):
    tenant_id: Identifier


class KnowledgeBaseResult(BaseModel):
    kb_id: UUID
    tenant_id: str
    name: str
    created_at: datetime


class KnowledgeBaseDetail(KnowledgeBaseResult):
    description: str | None
    settings: dict
    document_count: int
    updated_at: datetime


class TreeDocument(BaseModel):
    document_id: UUID
    title: str
    status: Literal["SUCCESS", "FAILED", "PROCESSING"]
    error_message: str | None
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
