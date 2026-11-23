from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import and_, func, select, update
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Chunk, Document, IndexStatus, KnowledgeBase, RetrievalLog


async def get_knowledge_base(
    session: AsyncSession, tenant_id: str, kb_id: UUID, *, lock: bool = False
) -> KnowledgeBase | None:
    stmt = select(KnowledgeBase).where(KnowledgeBase.id == kb_id, KnowledgeBase.tenant_id == tenant_id)
    return await session.scalar(stmt.with_for_update() if lock else stmt)


async def get_knowledge_bases(
    session: AsyncSession, tenant_id: str, kb_ids: Sequence[UUID],
) -> dict[UUID, KnowledgeBase]:
    rows = await session.scalars(select(KnowledgeBase).where(
        KnowledgeBase.tenant_id == tenant_id, KnowledgeBase.id.in_(kb_ids),
    ))
    return {kb.id: kb for kb in rows}


async def replace_knowledge_base_settings(session: AsyncSession, kb_id: UUID, settings: dict) -> bool:
    updated = await session.scalar(
        update(KnowledgeBase).where(KnowledgeBase.id == kb_id)
        .values(settings=settings).returning(KnowledgeBase.id)
    )
    return updated is not None


async def add_knowledge_base(session: AsyncSession, knowledge_base: KnowledgeBase) -> None:
    session.add(knowledge_base)
    await session.flush()


async def add_document(session: AsyncSession, document: Document) -> None:
    session.add(document)
    await session.flush()


async def fail_pending_document(session: AsyncSession, document_id: UUID, message: str) -> None:
    await session.execute(
        update(Document).where(Document.id == document_id, Document.status == IndexStatus.PROCESSING)
        .values(status=IndexStatus.FAILED, error_message=message)
    )


async def add_retrieval_log(session: AsyncSession, retrieval_log: RetrievalLog) -> None:
    session.add(retrieval_log)
    await session.flush()


async def fetch_knowledge_tree(
    session: AsyncSession, tenant_id: str, keyword: str
) -> Sequence[RowMapping]:
    counts = select(
        Chunk.document_id, func.count(Chunk.id).label("chunk_count")
    ).group_by(Chunk.document_id).subquery()
    statement = select(
        KnowledgeBase.tenant_id, KnowledgeBase.id.label("kb_id"),
        KnowledgeBase.name, KnowledgeBase.description,
        KnowledgeBase.created_at.label("kb_created_at"),
        Document.id.label("document_id"), Document.title, Document.status, Document.error_message,
        Document.created_at.label("document_created_at"),
        func.coalesce(counts.c.chunk_count, 0).label("chunk_count"),
    ).outerjoin(Document, and_(
        Document.kb_id == KnowledgeBase.id,
        Document.tenant_id == KnowledgeBase.tenant_id,
    )).outerjoin(counts, counts.c.document_id == Document.id).where(
        KnowledgeBase.tenant_id == tenant_id
    )
    if keyword:
        statement = statement.where(KnowledgeBase.name.icontains(keyword, autoescape=True))
    statement = statement.order_by(
        KnowledgeBase.tenant_id, KnowledgeBase.created_at.desc(), KnowledgeBase.id,
        Document.created_at.desc(), Document.id,
    )
    return (await session.execute(statement)).mappings().all()
