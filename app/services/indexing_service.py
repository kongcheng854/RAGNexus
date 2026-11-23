import logging
from datetime import datetime, timezone
from uuid import UUID, uuid5

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.error_codes import ErrorCode
from app.core.exceptions import EmbeddingError, ServiceError, database_error_code
from app.models import Document, IndexStatus
from app.repositories import chunks
from app.services.document_ingestion import prepare_document_content
from app.providers.embedding.base import EmbeddingProvider
from app.providers.keyword_search.base import KeywordSearchProvider
from app.providers.vectorstores.base import ChunkToIndex, VectorStore
from app.schemas.hybrid_search import KeywordChunk
from app.core.logging import log_event
from app.utils.markdown_splitter import MarkdownStructuredSplitter


logger = logging.getLogger(__name__)


async def purge_document_chunks(session: AsyncSession, keyword: KeywordSearchProvider, document_id: UUID) -> None:
    await chunks.delete_by_document_id(session, document_id)
    await keyword.delete_by_document_id(document_id)


class IndexingService:
    def __init__(
        self,
        splitter: MarkdownStructuredSplitter,
        embedding_provider: EmbeddingProvider,
        vector_store: VectorStore,
        keyword: KeywordSearchProvider | None = None,
    ) -> None:
        self.splitter = splitter
        self.embedding_provider = embedding_provider
        self.vector_store = vector_store
        self.keyword = keyword

    async def index_existing_document(self, session: AsyncSession, document_id: UUID, *, final_attempt: bool) -> None:
        failure = None
        terminal = False
        # Cache the parse in a separate transaction so embedding retries reuse the same text.
        async with session.begin():
            document = await session.get(Document, document_id, with_for_update=True)
            if document is None or document.status != IndexStatus.PROCESSING:
                return
            try:
                parsed = await prepare_document_content(
                    content=document.content, file_path=document.source_file_path, filename=document.source_filename,
                    source_type=document.source_type, metadata=document.parser_metadata, reparse=document.reparse_pending,
                )
                document.content = parsed.content
                document.source_type = parsed.source_type
                document.parser_metadata = parsed.metadata or None
                document.reparse_pending = False
            except ServiceError as exc:
                document.status = IndexStatus.FAILED
                document.error_message = str(exc)
                failure = exc
        if failure is not None:
            raise DocumentIndexFailed(str(failure)) from failure
        async with session.begin():
            document = await session.get(Document, document_id, with_for_update=True, populate_existing=True)
            if document is None or document.status != IndexStatus.PROCESSING or document.reparse_pending:
                return
            try:
                # Keep the document lock while rolling back partial SQL writes and compensating ES.
                async with session.begin_nested():
                    await self._index_chunks(document)
                    document.status = IndexStatus.SUCCESS
                    document.error_message = None
            except Exception as exc:
                failure = exc
                if self.keyword is not None:
                    try:
                        await self.keyword.delete_by_document_id(document_id)
                    except Exception:
                        logger.exception("KEYWORD_INDEX_CLEANUP_FAILED document_id=%s", document_id)
                error = index_error(exc)
                if final_attempt or not retryable(exc):
                    terminal = True
                    document.status = IndexStatus.FAILED
                    document.error_message = error.message
        if terminal:
            raise DocumentIndexFailed(index_error(failure).message) from failure
        if failure is not None:
            raise failure
        log_event(logger, logging.INFO, "DOCUMENT_INDEXED", document_id=document_id)

    async def _index_chunks(self, document: Document) -> None:
        pieces = self.splitter.split(
            document.content.strip(), plain_text=(document.source_filename or document.title).lower().endswith(".txt"),
        )
        if not pieces:
            raise ServiceError(ErrorCode.INDEXING_FAILED)
        vectors = await self.embedding_provider.embed([piece.text for piece in pieces])
        if len(vectors) != len(pieces):
            raise EmbeddingError(
                "embedding provider returned an unexpected number of vectors",
                error=ErrorCode.LLM_NO_RESPONSE,
            )

        chunks = [
            ChunkToIndex(
                id=uuid5(document.id, str(position)),
                tenant_id=document.tenant_id,
                kb_id=document.kb_id,
                document_id=document.id,
                title=document.title,
                content=piece.text,
                chunk_metadata={
                    "position": position, "chunk_index": position, "source_type": document.source_type,
                    **(document.parser_metadata or {}), **piece.metadata,
                },
                embedding=vector,
            )
            for position, (piece, vector) in enumerate(zip(pieces, vectors, strict=True))
        ]
        await self.vector_store.add_chunks(chunks)
        if self.keyword is not None:
            await self.keyword.add_chunks([
                KeywordChunk(
                    tenant_id=chunk.tenant_id, kb_id=chunk.kb_id, document_id=chunk.document_id,
                    chunk_id=chunk.id, title=chunk.title, content=chunk.content,
                    metadata=chunk.chunk_metadata,
                    created_at=datetime.now(timezone.utc),
                )
                for chunk in chunks
            ])


class DocumentIndexFailed(Exception):
    """The worker has already committed the terminal document state."""


def index_error(exc: Exception) -> ErrorCode:
    if isinstance(exc, ServiceError):
        return exc.error
    if isinstance(exc, SQLAlchemyError):
        return database_error_code(exc)
    return ErrorCode.INDEXING_FAILED


def retryable(exc: Exception) -> bool:
    return index_error(exc) in {
        ErrorCode.LLM_TIMEOUT, ErrorCode.LLM_RATE_LIMIT, ErrorCode.LLM_ERROR,
        ErrorCode.KEYWORD_SEARCH_ERROR, ErrorCode.DB_UNAVAILABLE, ErrorCode.DB_TIMEOUT,
    }
