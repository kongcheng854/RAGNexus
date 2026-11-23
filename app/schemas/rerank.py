from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.hybrid_search import RetrievalEvidence


class RerankOptions(BaseModel):
    enabled: bool | None = None
    top_n: Annotated[int, Field(strict=True, gt=0)] | None = None


class RerankCandidate(RetrievalEvidence):
    kb_id: UUID | None = None
    kb_name: str | None = None

    def to_prompt(self, max_chars: int) -> dict[str, Any]:
        payload = {
            "chunk_id": str(self.chunk_id),
            "document_id": str(self.document_id),
            "title": self.title,
            "content": self.content[:max_chars],
            "vector_score": self.vector_score if self.retrieval_source else self.score,
        }
        if self.retrieval_source is not None:
            payload.update(
                bm25_score=self.bm25_score, retrieval_score=self.score,
                retrieval_source=self.retrieval_source,
            )
        return payload


class RerankedChunk(RerankCandidate):
    rerank_score: float | None = None


class RerankMetadata(BaseModel):
    enabled: bool
    provider: str
    llm_provider: str | None = None
    model: str | None = None
    top_n: int
    candidate_count: int
    degraded: bool = False
    error: str | None = None


class ModelRankings(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True)

    rankings: list[dict[str, Any]]


class ModelRanking(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True)

    chunk_id: str
    rerank_score: Annotated[float, Field(strict=True, ge=0, le=1, allow_inf_nan=False)]
    reason: str | None = None
