from typing import Annotated

from fastapi import Depends, Request
from langfuse.api.client import FernLangfuse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.observability.langfuse_client import get_langfuse_client
from app.services.rate_limit_service import RateLimitService
from app.db.session import get_session
from app.providers.embedding.base import EmbeddingProvider
from app.providers.keyword_search.base import KeywordSearchProvider
from app.providers.llm.base import LLMProvider
from app.providers.rerank.base import RerankProvider
from app.providers.vectorstores.base import VectorStore
from app.providers.vectorstores.pgvector import PgVectorStore


SessionDependency = Annotated[AsyncSession, Depends(get_session)]


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_embedding_provider(request: Request) -> EmbeddingProvider:
    return request.app.state.embedding_provider


def get_llm(request: Request) -> LLMProvider | None:
    return request.app.state.llm


def get_rerank_provider(request: Request) -> RerankProvider:
    return request.app.state.rerank_provider


def get_keyword_provider(request: Request) -> KeywordSearchProvider:
    return request.app.state.keyword_provider


def get_vector_store(
    session: SessionDependency, settings: Annotated[Settings, Depends(get_settings)]
) -> VectorStore:
    stores = {"pgvector": PgVectorStore}
    return stores[settings.vector_store](session)


def get_rate_limits(request: Request) -> RateLimitService:
    return request.app.state.rate_limits


LangfuseDependency = Annotated[FernLangfuse | None, Depends(get_langfuse_client)]
RateLimitsDependency = Annotated[RateLimitService, Depends(get_rate_limits)]
SettingsDependency = Annotated[Settings, Depends(get_settings)]
EmbeddingDependency = Annotated[EmbeddingProvider, Depends(get_embedding_provider)]
LLMDependency = Annotated[LLMProvider | None, Depends(get_llm)]
RerankDependency = Annotated[RerankProvider, Depends(get_rerank_provider)]
KeywordDependency = Annotated[KeywordSearchProvider, Depends(get_keyword_provider)]
VectorStoreDependency = Annotated[VectorStore, Depends(get_vector_store)]
