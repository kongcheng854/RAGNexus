from typing import Annotated
from uuid import UUID

from pydantic import AfterValidator, BaseModel, Field

from app.schemas.common import Identifier, validate_text
from app.schemas.hybrid_search import RetrievalDetails, RetrievalOptions
from app.schemas.rerank import RerankedChunk, RerankMetadata, RerankOptions


class RetrievalRequest(BaseModel):
    kb_id: UUID
    user_id: Identifier
    query: Annotated[str, Field(max_length=2_000), AfterValidator(validate_text)]
    top_k: Annotated[int, Field(strict=True, gt=0, le=1000)] | None = None
    rerank_options: RerankOptions | None = None
    retrieval_options: RetrievalOptions | None = None


class RetrievalInput(RetrievalRequest):
    tenant_id: Identifier


class RetrievalMetadata(BaseModel):
    top_k: int
    vector_store: str
    latency_ms: int
    rerank: RerankMetadata | None = None
    retrieval: RetrievalDetails | None = None


class RetrievalResult(BaseModel):
    query: str
    kb_id: UUID
    retrieved_chunks: list[RerankedChunk]
    metadata: RetrievalMetadata
