import logging

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.error_codes import ErrorCode
from app.core.exceptions import ServiceError, database_error_code
from app.core.logging import log_event
from app.models import Document, IndexStatus
from app.repositories import catalog
from app.schemas.document import DocumentResult, DocumentUploadInput
from app.services.indexing_service import IndexingService
from app.services.knowledge_base_service import require_knowledge_base
from app.utils.id_generator import generate_id

logger = logging.getLogger(__name__)


async def upload_document(
    session: AsyncSession, request: DocumentUploadInput, indexing: IndexingService
) -> DocumentResult:
    document_id = generate_id()
    document = Document(
        id=document_id,
        tenant_id=request.tenant_id,
        kb_id=request.kb_id,
        title=request.title,
        source_type="text",
        status=IndexStatus.PROCESSING,
    )
    async with session.begin():
        await require_knowledge_base(session, request.tenant_id, request.kb_id)
        await catalog.add_document(session, document)

    log_event(logger, logging.INFO, "DOCUMENT_INDEXING_STARTED", document_id=document_id, kb_id=request.kb_id)
    try:
        async with session.begin():
            chunk_count = await indexing.index_document(document_id, request)
            await catalog.update_document_status(session, document_id, IndexStatus.SUCCESS)
    except Exception as exc:
        await indexing.cleanup_document(document_id)
        if isinstance(exc, ServiceError):
            error = exc.error
        elif isinstance(exc, SQLAlchemyError):
            error = database_error_code(exc)
        else:
            error = ErrorCode.INDEXING_FAILED

        log_event(
            logger, logging.WARNING, "DOCUMENT_INDEXING_FAILED",
            document_id=document_id, kb_id=request.kb_id, code=int(error),
        )

        async with session.begin():
            await catalog.update_document_status(session, document_id, IndexStatus.FAILED, error.message)

        raise ServiceError(
            error,
            data={"document_id": str(document_id), "status": IndexStatus.FAILED},
        ) from exc

    log_event(
        logger, logging.INFO, "DOCUMENT_INDEXED",
        document_id=document_id, kb_id=request.kb_id, chunk_count=chunk_count,
    )
    return DocumentResult(
        document_id=document_id,
        kb_id=request.kb_id,
        status=IndexStatus.SUCCESS,
        chunk_count=chunk_count,
    )
