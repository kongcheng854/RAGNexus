"""Two-arm retrieval experiment declarations and first-stage validation."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError, model_validator

from app.providers.query.base import QueryOptions
from app.schemas.hybrid_search import RetrievalOptions
from app.schemas.rerank import RerankOptions
from app.tenant.plan_presets import Profile
from app.tenant.retrieve_presets import RETRIEVE_PROFILE_PRESETS
from scripts.golden_dataset import read_json

GROUPS = ("baseline", "candidate")
Metric = Literal["context_precision", "context_recall"]
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Slug = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")]
STRICT = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False, hide_input_in_errors=True)


class Options(BaseModel):
    model_config = STRICT


class Recall(RetrievalOptions):
    model_config = STRICT


class Rewrite(QueryOptions):
    model_config = STRICT


class Rerank(RerankOptions):
    model_config = STRICT


class Arm(Options):
    label: Text
    profile: Profile
    top_k: int | None = Field(default=None, gt=0, le=1000)
    retrieval_options: Recall | None = None
    rerank_options: Rerank | None = None
    query_options: Rewrite | None = None

    @model_validator(mode="after")
    def check_options(self):
        if self.profile != "custom":
            if self.model_fields_set - {"label", "profile"}:
                raise ValueError("命名 Profile 只接受 label 和 profile")
            return self
        if self.top_k is None:
            raise ValueError("custom 需要显式 top_k")
        for name in ("retrieval_options", "query_options", "rerank_options"):
            options = getattr(self, name)
            if options is None or any(value is None for value in options.model_dump().values()):
                raise ValueError(f"custom 需要完整的 {name}")
            missing = type(options).model_fields.keys() - options.model_fields_set
            if missing:
                raise ValueError(f"{name} 需要显式指定 {', '.join(sorted(missing))}")
        return self

    def request_options(self):
        return self.model_dump(exclude={"label"}, exclude_none=True)

    def declared(self):
        options = self.request_options()
        if preset := RETRIEVE_PROFILE_PRESETS[self.profile]:
            options.update(top_k=preset.top_k, retrieval_options={"mode": preset.mode},
                           rerank_options={"enabled": preset.rerank},
                           query_options={"enabled": preset.rewrite, "strategy": "rewrite", "synonym_enabled": True})
        fields = {}
        for name, value in options.items():
            if isinstance(value, dict):
                fields.update({f"{name}.{k}": v for k, v in value.items()})
            else:
                fields[name] = value
        return fields


class Guardrail(Options):
    min_delta: float = Field(ge=-1, le=1)


class CostLimits(Options):
    max_p95_increase_ratio: float = Field(ge=0)
    max_model_calls_increase: float = Field(ge=0)


class Decision(Options):
    rationale: Text | None = None  # Older run snapshots predate this field.
    min_primary_improvement: float = Field(ge=0, le=1)
    quality_guardrails: dict[Metric, Guardrail]
    cost_limits: CostLimits
    max_failed_cases: int = Field(ge=0)


class Experiment(Options):
    schema_version: Literal["1.0"]
    experiment_id: Slug
    title: Text
    target: Slug
    dataset: Text
    suite: Text
    primary_metric: Metric
    changed_fields: list[Text] = Field(min_length=1)
    concurrency: int = Field(default=4, gt=0)
    ragas_concurrency: int = Field(default=4, gt=0)
    ragas_timeout_seconds: float | None = Field(default=None, gt=0)
    warmup_cases: int = Field(default=0, ge=0)
    timeout_seconds: float = Field(default=120, gt=0)
    max_qps: float | None = Field(default=None, gt=0)
    index_id: Text | None = None
    baseline: Arm
    candidate: Arm
    decision: Decision

    @model_validator(mode="after")
    def compare_declarations(self):
        a, b = self.baseline, self.candidate
        if (a.profile == "custom") != (b.profile == "custom"):
            raise ValueError("两个组必须同为 custom 或同为命名 Profile")
        if a.profile != "custom" and "profile" not in self.target:
            raise ValueError("单功能实验必须使用 custom")
        left, right = a.declared(), b.declared()
        diff = {k for k in left.keys() | right.keys() if left.get(k) != right.get(k)}
        if len(set(self.changed_fields)) != len(self.changed_fields) or set(self.changed_fields) != diff:
            raise ValueError(f"changed_fields 必须逐项声明实际差异：{', '.join(sorted(diff)) or '没有差异'}")
        switches = {"query_rewrite": "query_options.enabled", "synonym_expansion": "query_options.synonym_enabled",
                    "rerank": "rerank_options.enabled", "hybrid_search": "retrieval_options.mode"}
        if self.target in switches and diff != {switches[self.target]}:
            raise ValueError(f"{self.target} 单功能对照只应改变 {switches[self.target]}")
        if "profile" in self.target and a.profile == "custom":
            raise ValueError("Profile 实验需要两组不同的命名预设")
        return self


def read_experiment(path, *, archived=False):
    try:
        experiment = Experiment.model_validate(read_json(path))
        if not archived and not experiment.decision.rationale:
            raise ValueError("decision.rationale: 请在运行前记录验收门槛的理由")
        if not archived and not experiment.decision.quality_guardrails:
            raise ValueError("decision.quality_guardrails: 请至少声明一项质量护栏")
        return experiment
    except ValidationError as exc:
        issues = exc.errors(include_input=False, include_url=False)
        raise ValueError("\n".join(f"{'.'.join(map(str, i['loc'])) or '$'}: {i['msg']}" for i in issues)) from None
