from contextlib import nullcontext
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header

from app.api.dependencies import (
    EmbeddingDependency,
    KeywordDependency,
    LangfuseDependency,
    RateLimitsDependency,
    LLMDependency,
    RerankDependency,
    SessionDependency,
    SettingsDependency,
    VectorStoreDependency,
)
from app.providers.llm.openai_compatible import count_completion_attempts
from app.schemas.common import ResponseEnvelope
from app.schemas.feedback import FeedbackData, FeedbackRequest
from app.services import feedback_service
from app.api.v1.deps import TenantDependency
from app.schemas.rag import RetrievalInput, RetrievalRequest, RetrievalResult
from app.tenant.plan_resolver import resolve_plan
from app.services.rag_service import retrieve


router = APIRouter(prefix="/rag", tags=["retrieval"])


@router.post("/retrieve", response_model_exclude_none=True)
async def retrieve_context(
    body: RetrievalRequest,
    tenant: TenantDependency,
    session: SessionDependency,
    embedding: EmbeddingDependency,
    store: VectorStoreDependency,
    reranker: RerankDependency,
    settings: SettingsDependency,
    keyword: KeywordDependency,
    llm: LLMDependency,
    rates: RateLimitsDependency,
    langfuse: LangfuseDependency,
    offline: Annotated[bool, Header(alias="X-RAGNexus-Offline-Eval")] = False,
) -> ResponseEnvelope[RetrievalResult]:
    command = RetrievalInput(**body.model_dump(), tenant_id=tenant.tenant_id)
    with count_completion_attempts() if offline else nullcontext(None) as attempts:
        result = await retrieve(session, command, embedding, store, reranker, settings, keyword, llm,
                                plan=resolve_plan(tenant), rates=rates, langfuse=None if offline else langfuse,
                                completion_attempts=attempts)
    return ResponseEnvelope[RetrievalResult](data=result)


@router.post("/feedback")
async def submit_feedback(
    body: FeedbackRequest, tenant: TenantDependency, session: SessionDependency, langfuse: LangfuseDependency,
    submission_id: Annotated[UUID | None, Header(alias="X-Feedback-Submission-Id")] = None,
) -> ResponseEnvelope[FeedbackData]:
    data = await feedback_service.submit_feedback(session, tenant.tenant_id, body, langfuse, submission_id=submission_id)
    return ResponseEnvelope(data=data)
