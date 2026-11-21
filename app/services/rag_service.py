import logging
from time import perf_counter
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.error_codes import ErrorCode
from app.core.logging import log_event
from app.models import RetrievalLog
from app.providers.embedding.base import EmbeddingProvider
from app.providers.keyword_search.base import KeywordSearchProvider
from app.providers.llm.base import LLMProviderError
from app.providers.rerank.base import RerankProvider
from app.providers.rerank.noop import NoopRerankProvider
from app.providers.vectorstores.base import VectorStore
from app.repositories import catalog
from app.schemas.rag import RetrievalMetadata, RetrievalInput, RetrievalResult
from app.schemas.rerank import RerankCandidate, RerankedChunk, RerankMetadata
from app.services.knowledge_base_service import require_knowledge_base
from app.services.hybrid_search_service import HybridSearchService
from app.utils.id_generator import generate_id


logger = logging.getLogger(__name__)
vector_order = NoopRerankProvider()


async def rank_results(
    request: RetrievalInput,
    candidates: list[RerankCandidate],
    provider: RerankProvider,
    settings: Settings,
    top_k: int,
    retrieval_log_id: UUID,
) -> tuple[list[RerankedChunk], RerankMetadata | None]:
    options = request.rerank_options
    enabled = settings.rerank_enabled
    top_n = settings.rerank_top_n
    if options is not None:
        if options.enabled is not None:
            enabled = options.enabled
        if options.top_n is not None:
            top_n = options.top_n

    if not enabled or provider.name == vector_order.name:
        chunks = await vector_order.rerank(query=request.query, chunks=candidates, top_n=top_k)
        metadata = None
        if options is not None or enabled:
            metadata = RerankMetadata(
                enabled=False, provider=vector_order.name, top_n=top_k, candidate_count=0,
            )
        return chunks, metadata

    shortlist = candidates[:settings.rerank_max_candidates]
    metadata = RerankMetadata(
        enabled=True, provider=provider.name, llm_provider=settings.llm_provider,
        model=settings.llm_model, top_n=top_n, candidate_count=len(shortlist),
    )
    try:
        chunks = await provider.rerank(query=request.query, chunks=shortlist, top_n=top_n)
    except Exception as exc:
        error = exc.error if isinstance(exc, LLMProviderError) else ErrorCode.LLM_ERROR
        log_event(
            logger, logging.WARNING, "RERANK_DEGRADED", exc_info=exc,
            retrieval_log_id=retrieval_log_id, kb_id=request.kb_id, provider=provider.name,
            candidate_count=len(shortlist), code=int(error),
        )
        metadata.degraded = True
        metadata.error = error.message
        chunks = await vector_order.rerank(query=request.query, chunks=candidates, top_n=top_n)

    return chunks, metadata


async def retrieve(
    session: AsyncSession,
    request: RetrievalInput,
    embedding_provider: EmbeddingProvider,
    vector_store: VectorStore,
    rerank_provider: RerankProvider,
    settings: Settings,
    keyword_provider: KeywordSearchProvider,
) -> RetrievalResult:
    started_at = perf_counter()
    async with session.begin():
        await require_knowledge_base(session, request.tenant_id, request.kb_id)

    retrieval_log_id = generate_id()
    candidates, retrieval, top_k = await HybridSearchService(
        session, embedding_provider, vector_store, keyword_provider, settings
    ).search(request)
    # Release the database transaction before waiting on the model.
    chunks, rerank = await rank_results(
        request, candidates, rerank_provider, settings, top_k, retrieval_log_id
    )

    exclude = set() if (retrieval and retrieval.mode != "vector") or (rerank and rerank.enabled) else {"content"}
    latency_ms = int((perf_counter() - started_at) * 1000)
    async with session.begin():
        await catalog.add_retrieval_log(
            session,
            RetrievalLog(
                id=retrieval_log_id,
                tenant_id=request.tenant_id,
                kb_id=request.kb_id,
                user_id=request.user_id,
                query=request.query,
                retrieved_chunks=[
                    chunk.model_dump(mode="json", exclude=exclude, exclude_none=True) for chunk in chunks
                ],
                top_k=top_k,
                vector_store=vector_store.name,
                latency_ms=latency_ms,
            ),
        )
    log_event(
        logger, logging.INFO, "RETRIEVAL_COMPLETED", kb_id=request.kb_id,
        retrieval_log_id=retrieval_log_id, chunk_count=len(chunks), vector_store=vector_store.name,
        rerank_enabled=rerank.enabled if rerank else False,
        rerank_degraded=rerank.degraded if rerank else False,
    )
    return RetrievalResult(
        query=request.query,
        kb_id=request.kb_id,
        retrieved_chunks=chunks,
        metadata=RetrievalMetadata(
            top_k=top_k, vector_store=vector_store.name, latency_ms=latency_ms,
            rerank=rerank, retrieval=retrieval,
        ),
    )
