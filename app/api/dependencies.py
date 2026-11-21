from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db.session import get_session
from app.providers.document_parser import PlainTextParser
from app.providers.embedding.base import EmbeddingProvider
from app.providers.keyword_search.base import KeywordSearchProvider
from app.providers.rerank.base import RerankProvider
from app.providers.vectorstores.base import VectorStore
from app.providers.vectorstores.pgvector import PgVectorStore
from app.services.indexing_service import IndexingService
from app.utils.text_splitter import CharacterTextSplitter


SessionDependency = Annotated[AsyncSession, Depends(get_session)]


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_embedding_provider(request: Request) -> EmbeddingProvider:
    return request.app.state.embedding_provider


def get_rerank_provider(request: Request) -> RerankProvider:
    return request.app.state.rerank_provider


def get_keyword_provider(request: Request) -> KeywordSearchProvider:
    return request.app.state.keyword_provider


def get_vector_store(
    session: SessionDependency, settings: Annotated[Settings, Depends(get_settings)]
) -> VectorStore:
    stores = {"pgvector": PgVectorStore}
    return stores[settings.vector_store](session)


SettingsDependency = Annotated[Settings, Depends(get_settings)]
EmbeddingDependency = Annotated[EmbeddingProvider, Depends(get_embedding_provider)]
RerankDependency = Annotated[RerankProvider, Depends(get_rerank_provider)]
KeywordDependency = Annotated[KeywordSearchProvider, Depends(get_keyword_provider)]
VectorStoreDependency = Annotated[VectorStore, Depends(get_vector_store)]


def get_indexing_service(
    settings: SettingsDependency,
    embedding: EmbeddingDependency,
    store: VectorStoreDependency,
    keyword: KeywordDependency,
) -> IndexingService:
    return IndexingService(
        PlainTextParser(),
        CharacterTextSplitter(settings.chunk_size, settings.chunk_overlap),
        embedding,
        store,
        keyword if settings.retrieval_mode != "vector" else None,
    )
