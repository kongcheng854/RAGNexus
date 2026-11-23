from pydantic import BaseModel

from fastapi import APIRouter

from app.api.dependencies import RateLimitsDependency, SessionDependency
from app.services.quota_service import kb_count
from app.tenant.plan_presets import PlanFeatures, PlanLimits, PlanName
from app.tenant.plan_resolver import resolve_plan
from app.api.v1.deps import TenantDependency
from app.schemas.common import ResponseEnvelope


class TenantUsage(BaseModel):
    kb_count: int
    retrieve_daily_count: int


class TenantInfo(BaseModel):
    plan: PlanName
    features: PlanFeatures
    limits: PlanLimits
    usage: TenantUsage
    tenant_id: str
    tenant_name: str
    key_prefix: str | None
    key_name: str | None


router = APIRouter(prefix="/auth", tags=["authentication"])


@router.get("/me")
async def me(tenant: TenantDependency, session: SessionDependency, rates: RateLimitsDependency) -> ResponseEnvelope[TenantInfo]:
    plan = resolve_plan(tenant)
    async with session.begin():
        count = await kb_count(session, tenant.tenant_id)
    usage = TenantUsage(kb_count=count, retrieve_daily_count=await rates.daily_usage(tenant.tenant_id))
    return ResponseEnvelope[TenantInfo](data=TenantInfo(
        plan=plan.plan, features=plan.features, limits=plan.limits, usage=usage,
        tenant_id=tenant.tenant_id, tenant_name=tenant.tenant_name,
        key_prefix=tenant.key_prefix, key_name=tenant.key_name,
    ))
