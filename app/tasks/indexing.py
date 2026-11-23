import asyncio
import sys
from uuid import UUID

from celery.exceptions import Reject

from app.celery_app import celery_app
from app.core.config import Settings
from app.db.session import standalone_session
from app.providers.index_clients import index_clients
from app.providers.vectorstores.pgvector import PgVectorStore
from app.repositories.catalog import fail_pending_document
from app.services.indexing_service import DocumentIndexFailed, IndexingService, index_error, retryable
from app.utils.markdown_splitter import MarkdownStructuredSplitter


async def run_index(document_id: UUID, final_attempt: bool) -> None:
    settings = Settings()
    async with standalone_session() as session, index_clients(settings) as (embedding, keyword):
        service = IndexingService(
            MarkdownStructuredSplitter(settings.chunk_size, settings.chunk_overlap, settings.table_max_rows_per_chunk),
            embedding, PgVectorStore(session), keyword,
        )
        await service.index_existing_document(session, document_id, final_attempt=final_attempt)


async def record_failure(document_id: UUID, message: str) -> None:
    async with standalone_session() as session, session.begin():
        await fail_pending_document(session, document_id, message)


@celery_app.task(bind=True, max_retries=2, default_retry_delay=10)
def index_document_task(self, document_id: str):
    # Celery tasks are synchronous. Each delivery owns its event loop, DB session and HTTP clients.
    factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    identifier = UUID(document_id)
    final_attempt = self.request.retries >= self.max_retries
    with asyncio.Runner(loop_factory=factory) as runner:
        try:
            runner.run(run_index(identifier, final_attempt))
        except DocumentIndexFailed:
            raise
        except Exception as exc:
            if not final_attempt and retryable(exc):
                try:
                    raise self.retry(exc=exc)
                except Reject:
                    runner.run(record_failure(identifier, "重试任务投递失败"))
                    raise
            runner.run(record_failure(identifier, index_error(exc).message))
            raise
