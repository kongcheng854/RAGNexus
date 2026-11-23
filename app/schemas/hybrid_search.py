from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_serializer


RetrievalMode = Literal["vector", "bm25", "hybrid"]
PositiveCount = Annotated[int, Field(strict=True, gt=0, le=1000)]


class RetrievalOptions(BaseModel):
    mode: RetrievalMode | None = None
    vector_top_k: PositiveCount | None = None
    bm25_top_k: PositiveCount | None = None
    rrf_k: Annotated[int, Field(strict=True, gt=0)] | None = None


class KeywordChunk(BaseModel):
    tenant_id: str
    kb_id: UUID
    document_id: UUID
    chunk_id: UUID
    title: str
    content: str
    metadata: dict
    created_at: datetime


class KeywordMatch(BaseModel):
    chunk_id: UUID
    bm25_score: Annotated[float, Field(allow_inf_nan=False)]


class RetrievedChunkMetadata(BaseModel):
    heading_path: str | None = None
    chunk_type: str | None = None
    table_part: int | None = None


class RetrievalEvidence(BaseModel):
    document_id: UUID
    chunk_id: UUID
    title: str
    content: str
    score: float
    metadata: RetrievedChunkMetadata | None = None
    vector_score: float | None = None
    bm25_score: float | None = None
    vector_rank: int | None = None
    bm25_rank: int | None = None
    retrieval_source: Literal["vector", "bm25", "hybrid"] | None = None

    @model_serializer(mode="wrap")
    def serialize_evidence(self, handler):
        result = handler(self)
        if self.retrieval_source is not None:
            for name in ("vector_score", "bm25_score", "vector_rank", "bm25_rank"):
                result[name] = getattr(self, name)
        return result


class KnowledgeBaseRetrievalStatus(BaseModel):
    status: Literal["ok", "degraded", "failed"]
    error: str | None = None
    degraded_reason: str | None = None


class RetrievalDetails(BaseModel):
    failed_kb_ids: list[UUID] | None = None
    partial_kb_success: bool = False
    per_kb_metadata: dict[UUID, KnowledgeBaseRetrievalStatus] | None = None
    empty_reason: Literal["no_indexed_chunks", "no_chunks_matched"] | None = None
    multi_kb: bool | None = None
    kb_count: int | None = None
    per_kb_top_k: int | None = None
    mode: RetrievalMode
    fusion: Literal["rrf"] | None = None
    rrf_k: int | None = None
    vector_store: str | None = None
    keyword_search: str | None = None
    vector_top_k: int
    bm25_top_k: int
    vector_count: int
    bm25_count: int
    fused_count: int
    degraded: bool = False
    degraded_reason: str | None = None

    @model_serializer(mode="wrap")
    def serialize_details(self, handler):
        data = handler(self)
        if not self.multi_kb:
            data.pop("partial_kb_success", None)
        return data
