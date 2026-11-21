from pydantic import BaseModel

from fastapi import APIRouter

from app.api.v1.deps import TenantDependency
from app.schemas.common import ResponseEnvelope


class TenantInfo(BaseModel):
    tenant_id: str
    tenant_name: str
    key_prefix: str | None
    key_name: str | None


router = APIRouter(prefix="/auth", tags=["authentication"])


@router.get("/me")
async def me(tenant: TenantDependency) -> ResponseEnvelope[TenantInfo]:
    return ResponseEnvelope[TenantInfo](data=TenantInfo(
        tenant_id=tenant.tenant_id, tenant_name=tenant.tenant_name,
        key_prefix=tenant.key_prefix, key_name=tenant.key_name,
    ))
