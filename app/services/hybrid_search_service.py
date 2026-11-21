import asyncio
import logging
from time import perf_counter

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.error_codes import ErrorCode
from app.core.exceptions import EmbeddingError
from app.core.logging import log_event
from app.providers.embedding.base import EmbeddingProvider
from app.providers.keyword_search.base import KeywordSearchProvider
from app.providers.vectorstores.base import VectorStore
from app.repositories.chunks import resolve_keyword_matches
from app.schemas.hybrid_search import RetrievalDetails
from app.schemas.rag import RetrievalInput
from app.schemas.rerank import RerankCandidate


logger = logging.getLogger(__name__)


def reciprocal_rank_fusion(
    vector_chunks: list[RerankCandidate],
    bm25_chunks: list[RerankCandidate],
    *, rrf_k: int, top_n: int,
) -> list[RerankCandidate]:
    if rrf_k <= 0 or top_n <= 0:
        raise ValueError("rrf_k and top_n must be positive")
    merged: dict = {}
    for source, chunks in (("vector", vector_chunks), ("bm25", bm25_chunks)):
        seen = set()
        for rank, chunk in enumerate(chunks, start=1):
            if chunk.chunk_id in seen:
                continue
            seen.add(chunk.chunk_id)
            if chunk.chunk_id not in merged:
                merged[chunk.chunk_id] = RerankCandidate(
                    document_id=chunk.document_id, chunk_id=chunk.chunk_id,
                    title=chunk.title, content=chunk.content, score=0,
                )
            result = merged[chunk.chunk_id]
            result.score += 1.0 / (rrf_k + rank)
            if source == "vector":
                result.vector_score = chunk.score
                result.vector_rank = rank
            else:
                result.bm25_score = chunk.score
                result.bm25_rank = rank
            result.retrieval_source = (
                "hybrid" if result.vector_rank and result.bm25_rank else source
            )
    # Equal RRF scores keep insertion order: vector ranking, then BM25-only hits.
    return sorted(merged.values(), key=lambda chunk: chunk.score, reverse=True)[:top_n]


class HybridSearchService:
    def __init__(
        self, session: AsyncSession, embedding: EmbeddingProvider,
        vector_store: VectorStore, keyword: KeywordSearchProvider, settings: Settings,
    ) -> None:
        self.session = session
        self.embedding = embedding
        self.vector_store = vector_store
        self.keyword = keyword
        self.settings = settings

    async def _vector_search(self, request: RetrievalInput, top_k: int) -> list[RerankCandidate]:
        vectors = await self.embedding.embed([request.query])
        if len(vectors) != 1:
            raise EmbeddingError("expected one query vector", error=ErrorCode.LLM_NO_RESPONSE)
        async with self.session.begin():
            matches = await self.vector_store.similarity_search(
                vectors[0], tenant_id=request.tenant_id, kb_id=request.kb_id, top_k=top_k,
            )
        return [RerankCandidate.model_validate(match, from_attributes=True) for match in matches]

    async def search(
        self, request: RetrievalInput,
    ) -> tuple[list[RerankCandidate], RetrievalDetails | None, int]:
        started = perf_counter()
        settings = self.settings
        options = request.retrieval_options
        mode = options.mode if options and options.mode else settings.retrieval_mode
        legacy = mode == "vector" and options is None
        top_n = request.top_k or (settings.top_k if mode == "vector" else settings.hybrid_top_n)
        vector_top_k = (
            options.vector_top_k if options and options.vector_top_k else
            top_n if mode == "vector" else settings.hybrid_vector_top_k
        )
        bm25_top_k = options.bm25_top_k if options and options.bm25_top_k else settings.hybrid_bm25_top_k
        rrf_k = options.rrf_k if options and options.rrf_k else settings.hybrid_rrf_k
        context = {
            "tenant_id": request.tenant_id, "kb_id": request.kb_id, "query": request.query,
            "vector_top_k": vector_top_k, "bm25_top_k": bm25_top_k,
            "vector_count": 0, "bm25_count": 0, "fused_count": 0,
        }

        def event(name: str, level: int = logging.INFO, **fields) -> None:
            log_event(
                logger, level, name, **(context | fields),
                cost_ms=round((perf_counter() - started) * 1000, 2),
            )

        event("HYBRID_SEARCH_START")
        vectors: list[RerankCandidate] = []
        keyword_result = []
        if mode == "hybrid":
            vector_result, keyword_result = await asyncio.gather(
                self._vector_search(request, vector_top_k),
                self.keyword.keyword_search(
                    query=request.query, tenant_id=request.tenant_id,
                    kb_id=request.kb_id, top_k=bm25_top_k,
                ),
                return_exceptions=True,
            )
            if isinstance(vector_result, BaseException):
                raise vector_result
            vectors = vector_result
        elif mode == "vector":
            vectors = await self._vector_search(request, vector_top_k)
        else:
            try:
                keyword_result = await self.keyword.keyword_search(
                    query=request.query, tenant_id=request.tenant_id, kb_id=request.kb_id,
                    top_k=bm25_top_k,
                )
            except Exception as exc:
                event("BM25_SEARCH_FAILED", logging.ERROR, exc_info=exc)
                raise

        context["vector_count"] = len(vectors)
        if mode != "bm25":
            event("VECTOR_SEARCH_SUCCESS")

        degraded = False
        bm25: list[RerankCandidate] = []
        if isinstance(keyword_result, Exception):
            degraded = True
            event("BM25_SEARCH_FAILED", logging.ERROR, exc_info=keyword_result)
            event("HYBRID_SEARCH_DEGRADED", logging.WARNING)
        elif isinstance(keyword_result, BaseException):
            raise keyword_result
        elif mode != "vector":
            # PostgreSQL is authoritative for visibility, tenant scope and current text.
            async with self.session.begin():
                bm25 = await resolve_keyword_matches(
                    self.session, keyword_result, request.tenant_id, request.kb_id
                )
            context["bm25_count"] = len(bm25)
            event("BM25_SEARCH_SUCCESS")

        unique_count = len({chunk.chunk_id for chunk in vectors + bm25})
        context["fused_count"] = unique_count
        if mode == "hybrid" and not degraded:
            candidates = reciprocal_rank_fusion(vectors, bm25, rrf_k=rrf_k, top_n=top_n)
            event("RRF_FUSION_SUCCESS")
        else:
            candidates = (bm25 if mode == "bm25" else vectors)[:top_n]
            if not legacy:
                for rank, candidate in enumerate(candidates, start=1):
                    candidate.retrieval_source = "bm25" if mode == "bm25" else "vector"
                    if mode == "bm25":
                        candidate.bm25_score, candidate.bm25_rank = candidate.score, rank
                    else:
                        candidate.vector_score, candidate.vector_rank = candidate.score, rank
        details = None if legacy else RetrievalDetails(
            mode=mode, fusion="rrf" if mode == "hybrid" else None,
            rrf_k=rrf_k if mode == "hybrid" else None,
            vector_store=self.vector_store.name if mode != "bm25" else None,
            keyword_search=self.keyword.name if mode != "vector" else None,
            vector_top_k=vector_top_k if mode != "bm25" else 0,
            bm25_top_k=bm25_top_k if mode != "vector" else 0,
            vector_count=len(vectors), bm25_count=len(bm25), fused_count=unique_count,
            degraded=degraded, degraded_reason="bm25 search failed" if degraded else None,
        )
        return candidates, details, top_n
