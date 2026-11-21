import logging
from datetime import datetime, timezone
from uuid import UUID

from app.core.error_codes import ErrorCode
from app.core.exceptions import EmbeddingError
from app.providers.document_parser import DocumentParser
from app.providers.embedding.base import EmbeddingProvider
from app.providers.keyword_search.base import KeywordSearchProvider
from app.providers.vectorstores.base import ChunkToIndex, VectorStore
from app.schemas.document import DocumentUploadInput
from app.schemas.hybrid_search import KeywordChunk
from app.core.logging import log_event
from app.utils.id_generator import generate_id
from app.utils.text_splitter import TextSplitter


logger = logging.getLogger(__name__)


class IndexingService:
    def __init__(
        self,
        parser: DocumentParser,
        splitter: TextSplitter,
        embedding_provider: EmbeddingProvider,
        vector_store: VectorStore,
        keyword: KeywordSearchProvider | None = None,
    ) -> None:
        self.parser = parser
        self.splitter = splitter
        self.embedding_provider = embedding_provider
        self.vector_store = vector_store
        self.keyword = keyword
        self.keyword_write_started = False

    async def index_document(self, document_id: UUID, request: DocumentUploadInput) -> int:
        parts = self.splitter.split(self.parser.parse(request.content))
        vectors = await self.embedding_provider.embed(parts)
        if len(vectors) != len(parts):
            raise EmbeddingError(
                "embedding provider returned an unexpected number of vectors",
                error=ErrorCode.LLM_NO_RESPONSE,
            )

        chunks = [
            ChunkToIndex(
                id=generate_id(),
                tenant_id=request.tenant_id,
                kb_id=request.kb_id,
                document_id=document_id,
                title=request.title,
                content=part,
                chunk_metadata={"position": position},
                embedding=vector,
            )
            for position, (part, vector) in enumerate(zip(parts, vectors, strict=True))
        ]
        await self.vector_store.add_chunks(chunks)
        if self.keyword is not None:
            self.keyword_write_started = True
            await self.keyword.add_chunks([
                KeywordChunk(
                    tenant_id=chunk.tenant_id, kb_id=chunk.kb_id, document_id=chunk.document_id,
                    chunk_id=chunk.id, title=chunk.title, content=chunk.content,
                    metadata=chunk.chunk_metadata | {"source_type": "text"},
                    created_at=datetime.now(timezone.utc),
                )
                for chunk in chunks
            ])
        return len(chunks)

    async def cleanup_document(self, document_id: UUID) -> None:
        if self.keyword is not None and self.keyword_write_started:
            try:
                await self.keyword.delete_by_document_id(document_id)
            except Exception as exc:
                log_event(
                    logger, logging.ERROR, "KEYWORD_INDEX_CLEANUP_FAILED",
                    document_id=document_id, exc_info=exc,
                )
