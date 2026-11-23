from app.core.auth import TenantContext
from app.tenant.plan_presets import PLAN_PRESETS, PlanContext


def resolve_plan(tenant: TenantContext) -> PlanContext:
    return PLAN_PRESETS[tenant.plan]
