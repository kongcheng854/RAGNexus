from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from app.api.dependencies import KeywordDependency, SessionDependency
from app.api.v1.deps import TenantDependency
from app.schemas.common import ResponseEnvelope
from app.schemas.knowledge_base import KnowledgeBaseCreate, KnowledgeBaseCreateInput, KnowledgeBaseResult, TenantKnowledgeTree
from app.schemas.knowledge_base import KnowledgeBaseDetail, KnowledgeBasePatch
from app.tenant.plan_resolver import resolve_plan
from app.services import knowledge_base_service


router = APIRouter(prefix="/knowledge-bases", tags=["knowledge bases"])


@router.get("/tree")
async def tree(
    session: SessionDependency,
    tenant: TenantDependency,
    keyword: Annotated[str, Query(max_length=128)] = "",
) -> ResponseEnvelope[list[TenantKnowledgeTree]]:
    return ResponseEnvelope[list[TenantKnowledgeTree]](
        data=await knowledge_base_service.get_knowledge_tree(session, tenant.tenant_id, keyword)
    )


@router.post("/create")
async def create(
    body: KnowledgeBaseCreate, session: SessionDependency, tenant: TenantDependency,
) -> ResponseEnvelope[KnowledgeBaseResult]:
    command = KnowledgeBaseCreateInput(**body.model_dump(), tenant_id=tenant.tenant_id)
    return ResponseEnvelope[KnowledgeBaseResult](data=await knowledge_base_service.create_knowledge_base(session, command, resolve_plan(tenant).limits))


@router.get("/{kb_id}")
async def detail(kb_id: UUID, session: SessionDependency, tenant: TenantDependency) -> ResponseEnvelope[KnowledgeBaseDetail]:
    return ResponseEnvelope(data=await knowledge_base_service.get_knowledge_base(session, tenant.tenant_id, kb_id))


@router.patch("/{kb_id}")
async def patch(
    kb_id: UUID, body: KnowledgeBasePatch, session: SessionDependency, tenant: TenantDependency,
) -> ResponseEnvelope[KnowledgeBaseDetail]:
    return ResponseEnvelope(data=await knowledge_base_service.patch_knowledge_base(session, tenant.tenant_id, kb_id, body))


@router.delete("/{kb_id}")
async def remove(
    kb_id: UUID, session: SessionDependency, tenant: TenantDependency, keyword: KeywordDependency,
) -> ResponseEnvelope[None]:
    await knowledge_base_service.delete_knowledge_base(session, keyword, tenant.tenant_id, kb_id)
    return ResponseEnvelope(data=None)
