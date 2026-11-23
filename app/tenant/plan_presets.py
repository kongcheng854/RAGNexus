from dataclasses import dataclass
from typing import Literal


MULTI_KB_MAX = 5

PlanName = Literal["free", "standard", "pro"]
Profile = Literal["speed", "balanced", "quality", "custom"]


@dataclass(frozen=True)
class PlanFeatures:
    allowed_profiles: tuple[Profile, ...]
    hybrid_allowed: bool
    rerank_allowed: bool
    query_rewrite_allowed: bool


@dataclass(frozen=True)
class PlanLimits:
    retrieve_qps: int
    retrieve_daily: int
    max_kb: int
    max_documents_per_kb: int
    max_processing_documents: int
    max_kb_per_retrieve: int


@dataclass(frozen=True)
class PlanContext:
    plan: PlanName
    features: PlanFeatures
    limits: PlanLimits


PLAN_PRESETS = {
    "free": PlanContext("free", PlanFeatures(("speed",), False, False, False),
                        PlanLimits(3, 500, 1, 30, 1, max_kb_per_retrieve=1)),
    "standard": PlanContext("standard", PlanFeatures(("speed", "balanced", "custom"), True, False, False),
                            PlanLimits(10, 5_000, 5, 200, 2, max_kb_per_retrieve=3)),
    "pro": PlanContext("pro", PlanFeatures(("speed", "balanced", "quality", "custom"), True, True, True),
                       PlanLimits(50, 100_000, 50, 5_000, 10, max_kb_per_retrieve=MULTI_KB_MAX)),
}
