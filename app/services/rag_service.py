import hashlib
import json
import logging
from collections import Counter
from time import perf_counter
from uuid import UUID

from langfuse.api.client import FernLangfuse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.error_codes import ErrorCode
from app.core.exceptions import ServiceError, raise_error
from app.core.logging import log_event
from app.models import RetrievalLog
from app.observability.langfuse_client import RetrieveObservability
from app.providers.embedding.base import EmbeddingProvider
from app.providers.keyword_search.base import KeywordSearchProvider
from app.providers.llm.base import LLMProvider, LLMProviderError
from app.providers.llm.openai_compatible import OpenAICompatibleLLMProvider
from app.providers.query.llm_rewrite import SYSTEM_PROMPT as REWRITE_PROMPT
from app.providers.query.pipeline import QueryPipeline
from app.providers.rerank.base import RerankProvider
from app.providers.rerank.llm import LLMRerankProvider
from app.providers.rerank.noop import NoopRerankProvider
from app.providers.vectorstores.base import VectorStore
from app.repositories import catalog
from app.schemas.rag import OfflineDiagnostics, RetrievalMetadata, RetrievalInput, RetrievalResult, TenantPolicy
from app.tenant.plan_presets import MULTI_KB_MAX, PlanContext
from app.tenant.retrieve_presets import apply_profile
from app.services.rate_limit_service import RateLimitService
from app.schemas.rerank import RerankCandidate, RerankedChunk, RerankMetadata
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
    multi_kb = len(request.kb_ids) > 1
    top_n = top_k if multi_kb else settings.rerank_top_n
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

    if multi_kb and len(chunks) < len(candidates):
        # A bounded rerank promotes its winners; the remaining fused hits retain RRF order.
        promoted = {chunk.chunk_id for chunk in chunks}
        chunks.extend(RerankedChunk(**hit.model_dump()) for hit in candidates if hit.chunk_id not in promoted)
    return chunks, metadata


async def retrieve(
    session: AsyncSession,
    request: RetrievalInput,
    embedding_provider: EmbeddingProvider,
    vector_store: VectorStore,
    rerank_provider: RerankProvider,
    settings: Settings,
    keyword_provider: KeywordSearchProvider,
    llm: LLMProvider | None = None,
    *, plan: PlanContext, rates: RateLimitService, langfuse: FernLangfuse | None = None,
    completion_attempts: Counter | None = None,
) -> RetrievalResult:
    if len(request.query) > settings.query_max_length:
        raise_error(ErrorCode.PARAM_ERROR, detail=f"query 最多允许 {settings.query_max_length} 个字符")
    if len(request.kb_ids) > min(plan.limits.max_kb_per_retrieve, MULTI_KB_MAX):
        raise_error(ErrorCode.FEATURE_NOT_ALLOWED)
    kb_ids = list(dict.fromkeys(request.kb_ids))
    effective, policy = apply_profile(request.model_copy(update={"kb_ids": kb_ids}), plan, settings)
    async with rates.retrieval(request.tenant_id, plan):
        log_id = generate_id()
        observation = RetrieveObservability(langfuse, query=request.query, metadata={
            "log_id": str(log_id), "tenant_id": request.tenant_id, "kb_id": str(request.kb_id),
            "kb_ids": [str(kb_id) for kb_id in kb_ids],
            "user_id": request.user_id, "profile": policy.retrieve_profile, "plan": plan.plan,
        })
        try:
            return await _retrieve(session, effective, embedding_provider, vector_store, rerank_provider,
                                   settings, keyword_provider, llm, policy, log_id, observation, completion_attempts)
        except Exception as exc:
            if isinstance(exc, ServiceError) and exc.error == ErrorCode.RETRIEVAL_FAILED and exc.data:
                observation.metadata["retrieval"] = exc.data
            await observation.flush(error=type(exc).__name__)
            raise


async def _retrieve(
    session: AsyncSession, request: RetrievalInput, embedding_provider: EmbeddingProvider,
    vector_store: VectorStore, rerank_provider: RerankProvider, settings: Settings,
    keyword_provider: KeywordSearchProvider, llm: LLMProvider | None, policy: TenantPolicy,
    retrieval_log_id: UUID, observation: RetrieveObservability, completion_attempts: Counter | None,
) -> RetrievalResult:
    started_at = perf_counter()
    async with session.begin():
        scoped = await catalog.get_knowledge_bases(session, request.tenant_id, request.kb_ids)
        bases = []
        for kb_id in request.kb_ids:
            if kb_id not in scoped:
                raise ServiceError(ErrorCode.KNOWLEDGE_BASE_NOT_FOUND, data={"missing_kb_id": str(kb_id)})
            bases.append(scoped[kb_id])

    offline = None
    if completion_attempts is not None:
        refs = {}
        group_count = 0
        for kb in bases:
            options = kb.settings or {}
            groups = options.get("synonyms") or []
            group_count += sum(isinstance(g, dict) and bool(g.get("terms")) and bool(g.get("expand")) for g in groups)
            inputs = [kb.name, kb.description, options.get("rewrite_hint"), groups, REWRITE_PROMPT]
            refs[str(kb.id)] = hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()
        offline = OfflineDiagnostics(
            langfuse_enabled=observation.client is not None,
            synonym_enabled=not (request.query_options and request.query_options.synonym_enabled is False),
            synonym_groups=group_count, settings_refs=refs,
            application_model=llm.model if isinstance(llm, OpenAICompatibleLLMProvider) else settings.llm_model,
            embedding_model=settings.embedding_model,
            execution_limits={"rewrite_timeout_ms": settings.query_rewrite_timeout_ms,
                             "llm_timeout_seconds": settings.llm_timeout_seconds,
                             "rerank_max_candidates": settings.rerank_max_candidates,
                             "rerank_chunk_max_chars": settings.rerank_chunk_max_chars,
                             "rerank_temperature": settings.rerank_temperature},
            index={"vector_store": vector_store.name,
                "keyword_index": settings.elasticsearch_index, "environment": settings.app_env},
            model_call_source="openai_compatible completion HTTP POST attempts",
        )
    scope_seconds = perf_counter() - started_at
    with observation.span("query_processing") as stage:
        processed = await QueryPipeline(settings, llm).process(request.query, bases, request.query_options)
        stage.update(processed.model_dump())
    search_request = request.model_copy(update={"query": processed.search_query})
    search_started = perf_counter()
    with observation.span("retrieval") as stage:
        candidates, retrieval, top_k = await HybridSearchService(
            session, embedding_provider, vector_store, keyword_provider, settings
        ).search(search_request, bases, policy.effective_mode)
        retrieval_metadata = retrieval.model_dump(mode="json", exclude_none=True) if retrieval else None
        if retrieval and (retrieval.degraded or retrieval.empty_reason):
            stage.update(retrieval_metadata)
            observation.metadata["retrieval"] = retrieval_metadata
        stage.update(mode=policy.effective_mode, vector_count=retrieval.vector_count if retrieval else len(candidates),
                     bm25_count=retrieval.bm25_count if retrieval else 0, degraded=bool(retrieval and retrieval.degraded))
    rank_started = perf_counter()
    with observation.span("rerank") as stage:
        chunks, rerank = await rank_results(
            request, candidates, rerank_provider, settings, top_k, retrieval_log_id
        )
        stage.update(enabled=bool(rerank and rerank.enabled), candidate_count=rerank.candidate_count if rerank else 0,
                     degraded=bool(rerank and rerank.degraded))

    if offline is not None:
        offline.stage_timings_ms = {"query_processing": (search_started - started_at - scope_seconds) * 1000,
                                    "retrieval": (rank_started - search_started) * 1000,
                                    "rerank": (perf_counter() - rank_started) * 1000}
        known_rewrite = processed.strategy != "rewrite" or llm is None or isinstance(llm, OpenAICompatibleLLMProvider)
        known_rank = not (rerank and rerank.enabled) or (
            isinstance(rerank_provider, LLMRerankProvider)
            and (rerank_provider.llm is None or isinstance(rerank_provider.llm, OpenAICompatibleLLMProvider)))
        if known_rewrite and known_rank:
            offline.application_model_calls = completion_attempts.total()
        else:
            offline.model_call_source = "unmetered provider"
    exclude = set() if (retrieval and retrieval.mode != "vector") or (rerank and rerank.enabled) else {"content"}
    latency_ms = int((scope_seconds + perf_counter() - search_started) * 1000)
    trace_id = await observation.flush(chunks)
    async with session.begin():
        await catalog.add_retrieval_log(
            session,
            RetrievalLog(
                id=retrieval_log_id,
                tenant_id=request.tenant_id,
                kb_id=request.kb_id, kb_ids=[str(kb_id) for kb_id in request.kb_ids],
                user_id=request.user_id,
                query=request.query, trace_id=trace_id, profile=policy.retrieve_profile,
                effective_query=processed.effective_query, search_query=processed.search_query,
                retrieved_chunks=[
                    chunk.model_dump(mode="json", exclude=exclude, exclude_none=True) for chunk in chunks
                ],
                retrieval_metadata=retrieval_metadata,
                top_k=top_k,
                vector_store=vector_store.name,
                latency_ms=latency_ms,
            ),
        )
    log_event(
        logger, logging.INFO, "RETRIEVAL_COMPLETED", kb_id=request.kb_id,
        retrieval_log_id=retrieval_log_id, chunk_count=len(chunks), vector_store=vector_store.name,
        query_strategy=processed.strategy, rewrite_latency_ms=processed.rewrite_latency_ms,
        rewrite_degraded=processed.degraded, synonym_applied=processed.synonym_applied,
        rerank_enabled=rerank.enabled if rerank else False,
        rerank_degraded=rerank.degraded if rerank else False,
    )
    return RetrievalResult(
        query=request.query,
        kb_id=request.kb_id, kb_ids=request.kb_ids,
        retrieved_chunks=chunks,
        metadata=RetrievalMetadata(
            log_id=retrieval_log_id, trace_id=trace_id, offline=offline,
            tenant_policy=policy.model_copy(update={"effective_rerank": bool(rerank and rerank.enabled)}),
            top_k=top_k, vector_store=vector_store.name, latency_ms=latency_ms,
            rerank=rerank, retrieval=retrieval,
            query_processing=processed if offline or processed.strategy == "rewrite" or processed.synonym_applied else None,
        ),
    )
