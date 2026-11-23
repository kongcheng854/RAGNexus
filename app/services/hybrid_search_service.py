import asyncio
import logging
from collections.abc import Callable
from functools import cache
from time import perf_counter
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.error_codes import ErrorCode
from app.core.exceptions import EmbeddingError, ServiceError, database_error_code
from app.core.logging import log_event
from app.models import KnowledgeBase
from app.providers.embedding.base import EmbeddingProvider
from app.providers.keyword_search.base import KeywordSearchProvider
from app.providers.vectorstores.base import VectorStore
from app.repositories.chunks import has_indexed_chunks, resolve_keyword_matches
from app.schemas.hybrid_search import KnowledgeBaseRetrievalStatus, RetrievalDetails, RetrievalMode
from app.schemas.rag import RetrievalInput
from app.schemas.rerank import RerankCandidate
from app.services.multi_kb_fusion_service import fuse_by_rrf


logger = logging.getLogger(__name__)


def _failure_code(error: Exception) -> ErrorCode:
    if isinstance(error, ServiceError):
        return error.error
    if isinstance(error, SQLAlchemyError):
        return database_error_code(error)
    if isinstance(error, TimeoutError):
        return ErrorCode.API_TIMEOUT
    return ErrorCode.RETRIEVAL_FAILED


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
                    title=chunk.title, content=chunk.content, metadata=chunk.metadata, score=0,
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

    async def _query_vector(self, query: str) -> list[float]:
        vectors = await self.embedding.embed([query])
        if len(vectors) != 1:
            raise EmbeddingError("expected one query vector", error=ErrorCode.LLM_NO_RESPONSE)
        return vectors[0]

    async def _vector_search(
        self, request: RetrievalInput, kb_id: UUID, top_k: int, query_vector: Callable[[], asyncio.Task[list[float]]],
    ) -> list[RerankCandidate]:
        vector = await query_vector()
        async with self.session.begin():
            matches = await self.vector_store.similarity_search(
                vector, tenant_id=request.tenant_id, kb_id=kb_id, top_k=top_k,
            )
        return [RerankCandidate.model_validate(match, from_attributes=True) for match in matches]

    async def search(
        self, request: RetrievalInput, bases: list[KnowledgeBase], mode: RetrievalMode,
    ) -> tuple[list[RerankCandidate], RetrievalDetails | None, int]:
        top_k = request.top_k or (self.settings.top_k if mode == "vector" else self.settings.hybrid_top_n)

        @cache
        def query_vector() -> asyncio.Task[list[float]]:
            # All vector branches await one embedding. BM25-only requests never start it.
            return asyncio.create_task(self._query_vector(request.query))

        if len(bases) == 1:
            hits, details = await self._search_kb(request, bases[0], mode, top_k, query_vector=query_vector)
        else:
            hits, details = await self._search_many(request, bases, mode, top_k, query_vector)
        if not hits:
            async with self.session.begin():
                indexed = await has_indexed_chunks(self.session, request.tenant_id, request.kb_ids)
            details.empty_reason = "no_chunks_matched" if indexed else "no_indexed_chunks"
        elif len(bases) == 1 and mode == "vector" and request.retrieval_options is None:
            return hits, None, top_k
        return hits, details, top_k

    async def _search_many(
        self, request: RetrievalInput, bases: list[KnowledgeBase], mode: RetrievalMode,
        top_k: int, query_vector: Callable[[], asyncio.Task[list[float]]],
    ) -> tuple[list[RerankCandidate], RetrievalDetails]:
        options = request.retrieval_options
        per_kb_top_k = max(top_k, (options.vector_top_k if options else None) or 0, 10)

        async def search_one(kb: KnowledgeBase):
            # Each concurrent lookup owns its transaction and session-bound store.
            async with AsyncSession(bind=self.session.bind) as session:
                search = HybridSearchService(
                    session, self.embedding, self.vector_store.for_session(session), self.keyword, self.settings,
                )
                return await search._search_kb(request, kb, mode, per_kb_top_k, multi_kb=True, query_vector=query_vector)

        results = await asyncio.gather(*(search_one(kb) for kb in bases), return_exceptions=True)
        rankings = []
        summaries = []
        failed = []
        statuses = {}
        for kb, result in zip(bases, results, strict=True):
            if isinstance(result, Exception):
                failed.append(kb.id)
                code = _failure_code(result)
                statuses[kb.id] = KnowledgeBaseRetrievalStatus(status="failed", error=code.name)
                log_event(logger, logging.WARNING, "KB_RETRIEVAL_FAILED", tenant_id=request.tenant_id,
                          kb_id=kb.id, code=int(code), exc_info=result)
                continue
            if isinstance(result, BaseException):
                raise result
            hits, summary = result
            rankings.append(hits)
            summaries.append(summary)
            statuses[kb.id] = KnowledgeBaseRetrievalStatus(
                status="degraded" if summary.degraded else "ok", degraded_reason=summary.degraded_reason,
            )
        if not summaries:
            raise ServiceError(ErrorCode.RETRIEVAL_FAILED, data={
                "failed_kb_ids": [str(kb_id) for kb_id in failed],
                "per_kb_metadata": {str(k): v.model_dump(exclude_none=True) for k, v in statuses.items()},
            })

        rrf_k = self.settings.hybrid_rrf_k
        fused = fuse_by_rrf(rankings, rrf_k)
        reasons = list(dict.fromkeys(s.degraded_reason for s in summaries if s.degraded_reason))
        if failed:
            reasons.insert(0, "some knowledge bases failed")
        details = summaries[0].model_copy(update={
            "multi_kb": True, "kb_count": len(bases), "per_kb_top_k": per_kb_top_k,
            "fusion": "rrf", "rrf_k": rrf_k, "fused_count": len(fused),
            "vector_count": sum(s.vector_count for s in summaries),
            "bm25_count": sum(s.bm25_count for s in summaries),
            "partial_kb_success": bool(failed), "failed_kb_ids": failed or None,
            "per_kb_metadata": statuses if reasons else None,
            "degraded": bool(reasons), "degraded_reason": "; ".join(reasons) or None,
        })
        return fused[:top_k], details

    async def _search_kb(
        self, request: RetrievalInput, kb: KnowledgeBase, mode: RetrievalMode, top_n: int,
        *, query_vector: Callable[[], asyncio.Task[list[float]]], multi_kb: bool = False,
    ) -> tuple[list[RerankCandidate], RetrievalDetails]:
        started = perf_counter()
        settings = self.settings
        options = request.retrieval_options
        legacy = mode == "vector" and options is None and not multi_kb
        vector_top_k = (
            options.vector_top_k if options and options.vector_top_k else
            top_n if mode == "vector" else settings.hybrid_vector_top_k
        )
        bm25_top_k = options.bm25_top_k if options and options.bm25_top_k else settings.hybrid_bm25_top_k
        if multi_kb:
            vector_top_k = max(vector_top_k, top_n)
            bm25_top_k = max(bm25_top_k, top_n)
        rrf_k = options.rrf_k if options and options.rrf_k else settings.hybrid_rrf_k
        # A failed SQL transaction can expire ORM attributes on the single-KB request session.
        kb_id, kb_name = kb.id, kb.name
        context = {
            "tenant_id": request.tenant_id, "kb_id": kb_id, "query": request.query,
            "vector_top_k": vector_top_k, "bm25_top_k": bm25_top_k,
            "vector_count": 0, "bm25_count": 0, "fused_count": 0,
        }

        def event(name: str, level: int = logging.INFO, **fields) -> None:
            log_event(
                logger, level, name, **(context | fields),
                cost_ms=round((perf_counter() - started) * 1000, 2),
            )

        event("HYBRID_SEARCH_START")
        calls = {}
        if mode != "bm25":
            calls["vector"] = self._vector_search(request, kb_id, vector_top_k, query_vector)
        if mode != "vector":
            calls["bm25"] = self.keyword.keyword_search(
                query=request.query, tenant_id=request.tenant_id, kb_id=kb_id, top_k=bm25_top_k,
            )
        results = await asyncio.gather(*calls.values(), return_exceptions=True)
        channels: dict[str, list[RerankCandidate]] = {}
        errors: dict[str, Exception] = {}
        for source, result in zip(calls, results, strict=True):
            try:
                if isinstance(result, BaseException):
                    raise result
                if source == "bm25":
                    # Resolve ES IDs after vector search releases this KB's SQL session.
                    async with self.session.begin():
                        result = await resolve_keyword_matches(self.session, result, request.tenant_id, kb_id)
                channels[source] = result
                context[f"{source}_count"] = len(result)
                event(f"{source.upper()}_SEARCH_SUCCESS")
            except Exception as exc:
                errors[source] = exc
                event(f"{source.upper()}_SEARCH_FAILED", logging.ERROR, exc_info=exc)

        if not channels:
            if mode != "hybrid":
                raise errors[mode]
            raise ServiceError(ErrorCode.RETRIEVAL_FAILED) from errors["vector"]

        degraded_reason = None
        if errors:
            degraded_reason = f"{next(iter(errors))} search failed"
            event("HYBRID_SEARCH_DEGRADED", logging.WARNING, degraded_reason=degraded_reason)
        vectors, bm25 = channels.get("vector", []), channels.get("bm25", [])
        unique_count = len({chunk.chunk_id for chunk in vectors + bm25})
        context["fused_count"] = unique_count
        if len(channels) == 2:
            candidates = reciprocal_rank_fusion(vectors, bm25, rrf_k=rrf_k, top_n=top_n)
            event("RRF_FUSION_SUCCESS")
        else:
            source, hits = next(iter(channels.items()))
            candidates = hits[:top_n]
            if not legacy:
                for rank, candidate in enumerate(candidates, start=1):
                    candidate.retrieval_source = source
                    if source == "bm25":
                        candidate.bm25_score, candidate.bm25_rank = candidate.score, rank
                    else:
                        candidate.vector_score, candidate.vector_rank = candidate.score, rank
        for candidate in candidates:
            candidate.kb_id, candidate.kb_name = kb_id, kb_name
        details = RetrievalDetails(
            mode=mode, fusion="rrf" if len(channels) == 2 else None,
            rrf_k=rrf_k if len(channels) == 2 else None,
            vector_store=self.vector_store.name if mode != "bm25" else None,
            keyword_search=self.keyword.name if mode != "vector" else None,
            vector_top_k=vector_top_k if mode != "bm25" else 0,
            bm25_top_k=bm25_top_k if mode != "vector" else 0,
            vector_count=len(vectors), bm25_count=len(bm25), fused_count=unique_count,
            degraded=bool(errors), degraded_reason=degraded_reason,
        )
        return candidates, details
