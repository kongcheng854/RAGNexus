from fastapi import APIRouter

from app.api.dependencies import (
    EmbeddingDependency,
    KeywordDependency,
    RerankDependency,
    SessionDependency,
    SettingsDependency,
    VectorStoreDependency,
)
from app.schemas.common import ResponseEnvelope
from app.api.v1.deps import TenantDependency
from app.schemas.rag import RetrievalInput, RetrievalRequest, RetrievalResult
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
) -> ResponseEnvelope[RetrievalResult]:
    command = RetrievalInput(**body.model_dump(), tenant_id=tenant.tenant_id)
    result = await retrieve(session, command, embedding, store, reranker, settings, keyword)
    return ResponseEnvelope[RetrievalResult](data=result)
