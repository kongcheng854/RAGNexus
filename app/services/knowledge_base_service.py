import logging
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.error_codes import ErrorCode
from app.core.exceptions import raise_error
from app.core.logging import log_event
from app.models import KnowledgeBase
from app.repositories import catalog
from app.schemas.knowledge_base import (
    KnowledgeBaseCreateInput, KnowledgeBaseResult, TenantKnowledgeTree, TreeDocument, TreeKnowledgeBase,
)
from app.utils.id_generator import generate_id


logger = logging.getLogger(__name__)


async def create_knowledge_base(
    session: AsyncSession, request: KnowledgeBaseCreateInput
) -> KnowledgeBaseResult:
    knowledge_base = KnowledgeBase(id=generate_id(), **request.model_dump())
    async with session.begin():
        await catalog.add_knowledge_base(session, knowledge_base)
        result = KnowledgeBaseResult(
            kb_id=knowledge_base.id,
            tenant_id=knowledge_base.tenant_id,
            name=knowledge_base.name,
            created_at=knowledge_base.created_at,
        )
    log_event(logger, logging.INFO, "KNOWLEDGE_BASE_CREATED", kb_id=result.kb_id)
    return result


async def require_knowledge_base(session: AsyncSession, tenant_id: str, kb_id: UUID) -> None:
    if await catalog.get_knowledge_base(session, tenant_id, kb_id) is None:
        raise_error(ErrorCode.KNOWLEDGE_BASE_NOT_FOUND)


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
            ))
    return list(tenants.values())
