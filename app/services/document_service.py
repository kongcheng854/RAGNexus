import asyncio
from datetime import datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.error_codes import ErrorCode
from app.core.exceptions import raise_error
from app.models import Chunk, Document, IndexStatus
from app.providers.keyword_search.base import KeywordSearchProvider
from app.repositories import catalog
from app.services.document_ingestion import discard_source, save_source
from app.schemas.document import DocumentFileFields, DocumentDetail, DocumentResult, DocumentUploadInput
from app.services.indexing_service import purge_document_chunks
from app.services.knowledge_base_service import require_knowledge_base
from app.services.index_dispatch import dispatch_document
from app.services.quota_service import check_indexing
from app.tenant.plan_presets import PlanLimits
from app.utils.id_generator import generate_id


async def _create_document(session: AsyncSession, document: Document, limits: PlanLimits,
                           *, upload: bytes | None = None, storage: Path | None = None) -> Document:
    saved = None
    try:
        async with session.begin():
            await require_knowledge_base(session, document.tenant_id, document.kb_id, lock=True)
            await check_indexing(session, document.tenant_id, limits, new_document_kb=document.kb_id)
            if upload is not None:
                saved = await asyncio.to_thread(save_source, storage, document.tenant_id, document.id,
                                                document.source_filename, upload)
                document.source_file_path = saved
            await catalog.add_document(session, document)
    except Exception:
        discard_source(saved)
        raise
    return document


async def create_document_record(session: AsyncSession, request: DocumentUploadInput, limits: PlanLimits) -> Document:
    document = Document(id=generate_id(), **request.model_dump(), source_type="text",
                        status=IndexStatus.PROCESSING, dispatch_pending=True)
    return await _create_document(session, document, limits)


async def create_file_record(session: AsyncSession, fields: DocumentFileFields, tenant: str, limits: PlanLimits,
                             filename: str, data: bytes, storage: Path) -> Document:
    extension = Path(filename).suffix[1:].lower()
    document = Document(id=generate_id(), **fields.model_dump(), tenant_id=tenant, content="",
                        source_filename=filename, source_type={"txt": "text", "md": "markdown"}.get(extension, extension),
                        status=IndexStatus.PROCESSING, dispatch_pending=True)
    return await _create_document(session, document, limits, upload=data, storage=storage)


async def enqueue_document(session: AsyncSession, document: Document) -> DocumentResult:
    await dispatch_document(session, document.id)
    return DocumentResult(document_id=document.id, kb_id=document.kb_id, status=IndexStatus.PROCESSING, chunk_count=0)


async def get_document(session: AsyncSession, tenant: str, document_id: UUID) -> DocumentDetail:
    count = select(func.count(Chunk.id)).where(Chunk.document_id == Document.id).scalar_subquery()
    async with session.begin():
        row = (await session.execute(select(
            Document.id.label("document_id"), Document.kb_id, Document.title, Document.status,
            Document.error_message, Document.created_at, Document.updated_at, count.label("chunk_count"),
        ).where(Document.id == document_id, Document.tenant_id == tenant))).mappings().one_or_none()
        if row is None:
            raise_error(ErrorCode.NOT_FOUND)
        return DocumentDetail.model_validate(row)


async def editable_document(session: AsyncSession, tenant: str, document_id: UUID, *, kb_id: UUID | None = None) -> Document:
    scope = [Document.id == document_id, Document.tenant_id == tenant]
    if kb_id is not None:
        scope.append(Document.kb_id == kb_id)
    # Reject a running worker immediately, then recheck under the lock for concurrent maintenance.
    status = await session.scalar(select(Document.status).where(*scope))
    if status == IndexStatus.PROCESSING:
        raise_error(ErrorCode.DATA_CONFLICT)
    document = await session.scalar(select(Document).where(*scope).with_for_update())
    if document is None:
        raise_error(ErrorCode.NOT_FOUND)
    if document.status == IndexStatus.PROCESSING:
        raise_error(ErrorCode.DATA_CONFLICT)
    return document


async def delete_document(session: AsyncSession, keyword: KeywordSearchProvider, tenant: str, document_id: UUID) -> None:
    async with session.begin():
        document = await editable_document(session, tenant, document_id)
        await purge_document_chunks(session, keyword, document.id)
        path = document.source_file_path
        await session.delete(document)
    discard_source(path)


async def reindex_document(
    session: AsyncSession, keyword: KeywordSearchProvider, tenant: str, document_id: UUID, limits: PlanLimits,
    *, kb_id: UUID | None = None, expected_updated_at: datetime | None = None, reparse: bool = False,
) -> DocumentResult:
    async with session.begin():
        document = await editable_document(session, tenant, document_id, kb_id=kb_id)
        if expected_updated_at is not None and document.updated_at != expected_updated_at:
            raise_error(ErrorCode.DATA_CONFLICT, detail="文档在本次批处理启动后已更新")
        if not document.source_file_path and not document.content.strip():
            raise_error(ErrorCode.DATA_CONFLICT, detail="缺少保存的原文，无法重新索引")
        await check_indexing(session, tenant, limits)
        await purge_document_chunks(session, keyword, document.id)
        document.status = IndexStatus.PROCESSING
        document.reparse_pending = bool(reparse and document.source_file_path)
        document.error_message = None
        document.dispatch_pending = True
    return await enqueue_document(session, document)
