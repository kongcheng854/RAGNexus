import logging
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.error_codes import ErrorCode
from app.core.exceptions import raise_error
from app.core.logging import log_event
from app.models import Document, IndexStatus, KnowledgeBase, RetrievalLog
from app.providers.keyword_search.base import KeywordSearchProvider
from app.repositories import catalog
from app.schemas.knowledge_base import (
    KnowledgeBaseDetail, KnowledgeBasePatch, KnowledgeBaseCreateInput, KnowledgeBaseResult, TenantKnowledgeTree, TreeDocument, TreeKnowledgeBase,
)
from app.services.indexing_service import purge_document_chunks
from app.services.document_ingestion import discard_source
from app.services.quota_service import check_create
from app.tenant.plan_presets import PlanLimits
from app.utils.id_generator import generate_id


logger = logging.getLogger(__name__)


async def create_knowledge_base(
    session: AsyncSession, request: KnowledgeBaseCreateInput, limits: PlanLimits
) -> KnowledgeBaseResult:
    knowledge_base = KnowledgeBase(id=generate_id(), **request.model_dump())
    async with session.begin():
        await check_create(session, request.tenant_id, limits)
        await catalog.add_knowledge_base(session, knowledge_base)
        result = KnowledgeBaseResult(
            kb_id=knowledge_base.id,
            tenant_id=knowledge_base.tenant_id,
            name=knowledge_base.name,
            created_at=knowledge_base.created_at,
        )
    log_event(logger, logging.INFO, "KNOWLEDGE_BASE_CREATED", kb_id=result.kb_id)
    return result


async def require_knowledge_base(session: AsyncSession, tenant_id: str, kb_id: UUID, *, lock: bool = False) -> KnowledgeBase:
    kb = await catalog.get_knowledge_base(session, tenant_id, kb_id, lock=lock)
    if kb is None:
        raise_error(ErrorCode.KNOWLEDGE_BASE_NOT_FOUND)
    return kb


async def _detail(session: AsyncSession, kb: KnowledgeBase) -> KnowledgeBaseDetail:
    document_count = await session.scalar(select(func.count(Document.id)).where(Document.kb_id == kb.id))
    return KnowledgeBaseDetail(
        kb_id=kb.id, tenant_id=kb.tenant_id, name=kb.name, description=kb.description,
        settings=kb.settings, created_at=kb.created_at, updated_at=kb.updated_at, document_count=document_count,
    )


async def get_knowledge_base(session: AsyncSession, tenant: str, kb_id: UUID) -> KnowledgeBaseDetail:
    async with session.begin():
        return await _detail(session, await require_knowledge_base(session, tenant, kb_id))


async def patch_knowledge_base(
    session: AsyncSession, tenant: str, kb_id: UUID, changes: KnowledgeBasePatch,
) -> KnowledgeBaseDetail:
    async with session.begin():
        kb = await require_knowledge_base(session, tenant, kb_id, lock=True)
        for name, value in changes.model_dump(exclude_unset=True).items():
            setattr(kb, name, value)
        await session.flush()
        await session.refresh(kb, ["updated_at"])
        return await _detail(session, kb)


async def delete_knowledge_base(
    session: AsyncSession, keyword: KeywordSearchProvider, tenant: str, kb_id: UUID,
) -> None:
    async with session.begin():
        kb = await require_knowledge_base(session, tenant, kb_id, lock=True)
        scope = (Document.tenant_id == tenant, Document.kb_id == kb_id)
        pending = await session.scalar(select(Document.id).where(*scope, Document.status == IndexStatus.PROCESSING).limit(1))
        if pending is not None:
            raise_error(ErrorCode.DATA_CONFLICT)
        documents = (await session.execute(
            select(Document.id, Document.status, Document.source_file_path).where(*scope).order_by(Document.id).with_for_update()
        )).all()
        if any(doc.status == IndexStatus.PROCESSING for doc in documents):
            raise_error(ErrorCode.DATA_CONFLICT)
        for doc in documents:
            await purge_document_chunks(session, keyword, doc.id)
        await session.execute(delete(Document).where(*scope))
        await session.execute(delete(RetrievalLog).where(RetrievalLog.kb_id == kb_id, RetrievalLog.tenant_id == tenant))
        await session.delete(kb)
    for doc in documents:
        discard_source(doc.source_file_path)


async def get_knowledge_tree(
    session: AsyncSession, tenant_id: str, keyword: str
) -> list[TenantKnowledgeTree]:
    async with session.begin():
        rows = await catalog.fetch_knowledge_tree(session, tenant_id, keyword.strip())
    tenants: dict[str, TenantKnowledgeTree] = {}
    bases: dict[UUID, TreeKnowledgeBase] = {}
    for row in rows:
        tenant_id = row["tenant_id"]
        if tenant_id not in tenants:
            tenants[tenant_id] = TenantKnowledgeTree(tenant_id=tenant_id)
        kb_id = row["kb_id"]
        if kb_id not in bases:
            bases[kb_id] = TreeKnowledgeBase(
                kb_id=kb_id, name=row["name"], description=row["description"],
                created_at=row["kb_created_at"],
            )
            tenants[tenant_id].knowledge_bases.append(bases[kb_id])
        if row["document_id"] is not None:
            bases[kb_id].documents.append(TreeDocument(
                document_id=row["document_id"], title=row["title"],
                chunk_count=row["chunk_count"], created_at=row["document_created_at"],
                status=IndexStatus(row["status"]).name, error_message=row["error_message"],
            ))
    return list(tenants.values())
