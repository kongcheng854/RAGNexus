from dataclasses import dataclass

from app.core.config import Settings
from app.core.error_codes import ErrorCode
from app.core.exceptions import raise_error
from app.providers.query.base import QueryOptions
from app.schemas.hybrid_search import RetrievalMode, RetrievalOptions
from app.schemas.rag import RetrievalInput, TenantPolicy
from app.schemas.rerank import RerankOptions
from app.tenant.plan_presets import PlanContext, Profile


@dataclass(frozen=True)
class RetrievePreset:
    mode: RetrievalMode
    top_k: int
    rerank: bool
    rewrite: bool


RETRIEVE_PROFILE_PRESETS: dict[Profile, RetrievePreset | None] = {
    "speed": RetrievePreset("vector", 3, False, False),
    "balanced": RetrievePreset("hybrid", 5, False, False),
    "quality": RetrievePreset("hybrid", 8, True, True),
    "custom": None,
}


def apply_profile(request: RetrievalInput, plan: PlanContext, settings: Settings) -> tuple[RetrievalInput, TenantPolicy]:
    profile = request.profile or "balanced"
    features = plan.features
    if profile not in features.allowed_profiles:
        raise_error(ErrorCode.FEATURE_NOT_ALLOWED)
    preset = RETRIEVE_PROFILE_PRESETS[profile]
    if preset:
        mode, rerank, rewrite = preset.mode, preset.rerank, preset.rewrite
        changes = dict(top_k=preset.top_k, retrieval_options=RetrievalOptions(mode=mode),
                       rerank_options=RerankOptions(enabled=rerank), query_options=QueryOptions(
                           enabled=rewrite, synonym_enabled=request.query_options.synonym_enabled if request.query_options else None))
    else:
        mode = (request.retrieval_options and request.retrieval_options.mode) or settings.retrieval_mode
        rank = request.rerank_options or RerankOptions()
        query = request.query_options or QueryOptions()
        # A global default can enable only features granted to this tenant.
        rerank = rank.enabled if rank.enabled is not None else settings.rerank_enabled and features.rerank_allowed
        rewrite = query.enabled if query.enabled is not None else settings.query_rewrite_enabled and features.query_rewrite_allowed
        changes = dict(rerank_options=rank.model_copy(update={"enabled": rerank}),
                       query_options=query.model_copy(update={"enabled": rewrite}))
    if ((mode == "hybrid" and not features.hybrid_allowed) or
            (rerank and not features.rerank_allowed) or (rewrite and not features.query_rewrite_allowed)):
        raise_error(ErrorCode.FEATURE_NOT_ALLOWED)
    policy = TenantPolicy(plan=plan.plan, retrieve_profile=profile, effective_mode=mode,
                          effective_rerank=rerank, effective_query_rewrite=rewrite)
    return request.model_copy(update=changes), policy
