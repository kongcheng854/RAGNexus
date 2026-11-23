from typing import Annotated
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_serializer, model_validator

from app.providers.query.base import QueryOptions, QueryProcessResult
from app.schemas.common import Identifier, validate_text
from app.schemas.hybrid_search import RetrievalDetails, RetrievalMode, RetrievalOptions
from app.tenant.plan_presets import PlanName, Profile
from app.schemas.rerank import RerankedChunk, RerankMetadata, RerankOptions


KnowledgeBaseIds = Annotated[list[UUID], Field(min_length=1)]


class RetrievalRequest(BaseModel):
    profile: Profile | None = None
    kb_id: UUID | None = Field(default=None, description="Single knowledge base, superseded by kb_ids.")
    kb_ids: KnowledgeBaseIds | None = Field(default=None, description="Ordered knowledge bases for retrieval.")
    user_id: Identifier
    query: Annotated[str, AfterValidator(validate_text)]
    top_k: Annotated[int, Field(strict=True, gt=0, le=1000)] | None = None
    query_options: QueryOptions | None = None
    rerank_options: RerankOptions | None = None
    retrieval_options: RetrievalOptions | None = None

    @model_validator(mode="before")
    @classmethod
    def normalize_knowledge_bases(cls, data):
        if not isinstance(data, dict):
            return data
        if data.get("kb_ids") is not None:
            ids = data["kb_ids"]
            return {**data, "kb_id": ids[0] if isinstance(ids, (list, tuple)) and ids else None}
        if data.get("kb_id") is None:
            raise ValueError("kb_id or a non-empty kb_ids list is required")
        return {**data, "kb_ids": [data["kb_id"]]}


class RetrievalInput(RetrievalRequest):
    tenant_id: Identifier
    kb_id: UUID
    kb_ids: KnowledgeBaseIds


class TenantPolicy(BaseModel):
    plan: PlanName
    retrieve_profile: Profile
    effective_mode: RetrievalMode
    effective_rerank: bool
    effective_query_rewrite: bool


class OfflineDiagnostics(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    execution_limits: dict[str, float] = Field(default_factory=dict)
    langfuse_enabled: bool
    synonym_enabled: bool
    synonym_groups: int
    settings_refs: dict[str, str]
    index: dict[str, str]
    application_model: str | None
    embedding_model: str
    model_call_source: str
    application_model_calls: int | None = None
    stage_timings_ms: dict[str, float] = Field(default_factory=dict)


class RetrievalMetadata(BaseModel):
    offline: OfflineDiagnostics | None = None
    log_id: UUID
    trace_id: str | None = None
    tenant_policy: TenantPolicy
    top_k: int
    vector_store: str
    latency_ms: int
    rerank: RerankMetadata | None = None
    retrieval: RetrievalDetails | None = None
    query_processing: QueryProcessResult | None = None

    @model_serializer(mode="wrap")
    def serialize_metadata(self, handler):
        data = handler(self)
        # The retrieve route omits other null fields for legacy clients.
        data["trace_id"] = self.trace_id
        if self.query_processing is None:
            data["query_processing"] = None
        return data


class RetrievedChunk(RerankedChunk, from_attributes=True):
    kb_id: UUID


class RetrievalResult(BaseModel):
    query: str
    kb_id: UUID = Field(description="First entry in kb_ids, retained for compatibility.")
    kb_ids: KnowledgeBaseIds
    retrieved_chunks: list[RetrievedChunk]
    metadata: RetrievalMetadata
