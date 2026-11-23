from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.error_codes import ErrorCode
from app.core.exceptions import raise_error
from app.models import Document, IndexStatus, KnowledgeBase
from app.tenant.plan_presets import PlanLimits


async def lock_quota(session: AsyncSession, tenant_id: str) -> None:
    # Serialize count-and-insert across API processes, including the virtual demo tenant.
    # The lock lasts for the transaction that changes the count.
    if session.get_bind().dialect.name == "postgresql":
        await session.execute(select(func.pg_advisory_xact_lock(60519, func.hashtext(tenant_id))))


async def kb_count(session: AsyncSession, tenant_id: str) -> int:
    return await session.scalar(select(func.count(KnowledgeBase.id)).where(KnowledgeBase.tenant_id == tenant_id))


async def check_create(session: AsyncSession, tenant_id: str, limits: PlanLimits) -> None:
    await lock_quota(session, tenant_id)
    if await kb_count(session, tenant_id) >= limits.max_kb:
        raise_error(ErrorCode.QUOTA_EXCEEDED, detail=f"知识库数量已达到套餐上限（{limits.max_kb} 个）")


async def check_indexing(session: AsyncSession, tenant_id: str, limits: PlanLimits, *, new_document_kb: UUID | None = None) -> None:
    await lock_quota(session, tenant_id)
    scoped = select(func.count(Document.id)).where(Document.tenant_id == tenant_id)
    if new_document_kb is not None:
        documents = await session.scalar(scoped.where(Document.kb_id == new_document_kb))
        if documents >= limits.max_documents_per_kb:
            raise_error(ErrorCode.QUOTA_EXCEEDED, detail=f"该知识库文档数已达到上限（{limits.max_documents_per_kb} 份）")
    processing = await session.scalar(scoped.where(Document.status == IndexStatus.PROCESSING))
    if processing >= limits.max_processing_documents:
        raise_error(ErrorCode.QUOTA_EXCEEDED, detail=f"正在索引的文档已达到上限（{limits.max_processing_documents} 份），请稍后再试")
