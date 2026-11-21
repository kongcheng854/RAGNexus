from typing import Annotated

from fastapi import APIRouter, Query

from app.api.dependencies import SessionDependency
from app.api.v1.deps import TenantDependency
from app.schemas.common import ResponseEnvelope
from app.schemas.knowledge_base import KnowledgeBaseCreate, KnowledgeBaseCreateInput, KnowledgeBaseResult, TenantKnowledgeTree
from app.services.knowledge_base_service import create_knowledge_base, get_knowledge_tree


router = APIRouter(prefix="/knowledge-bases", tags=["knowledge bases"])


@router.get("/tree")
async def tree(
    session: SessionDependency,
    tenant: TenantDependency,
    keyword: Annotated[str, Query(max_length=128)] = "",
) -> ResponseEnvelope[list[TenantKnowledgeTree]]:
    return ResponseEnvelope[list[TenantKnowledgeTree]](
        data=await get_knowledge_tree(session, tenant.tenant_id, keyword)
    )


@router.post("/create")
async def create(
    body: KnowledgeBaseCreate, session: SessionDependency, tenant: TenantDependency,
) -> ResponseEnvelope[KnowledgeBaseResult]:
    command = KnowledgeBaseCreateInput(**body.model_dump(), tenant_id=tenant.tenant_id)
    return ResponseEnvelope[KnowledgeBaseResult](data=await create_knowledge_base(session, command))
