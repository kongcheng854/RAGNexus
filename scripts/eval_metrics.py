"""Case-level inputs and the four retrieval experiment metrics."""

import math
from statistics import fmean

from pydantic import BaseModel, Field, field_validator

QUALITY = ("context_recall", "context_precision")
P95 = "p95_latency_ms"
CALLS = "mean_application_model_calls"
METRICS_VERSION = "2.0"


class RetrievedContext(BaseModel):
    content: str = Field(min_length=1)
    title: str | None = None
    heading_path: str | None = None
    retrieval_source: str | None = None
    rank: int | None = None


class CaseError(BaseModel):
    message: str = Field(min_length=1)


class CaseResult(BaseModel):
    case_id: str
    question: str
    ground_truth: str
    answer: None = None
    contexts: list[RetrievedContext]
    latency_ms: float | None = None
    application_model_calls: int | None = None
    error: CaseError | None
    config_issues: list[str]
    effective_config: dict = Field(default_factory=dict)
    degraded: dict[str, str] = Field(default_factory=dict)
    query_processing: dict | None = None
    model_call_source: str | None = None

    @field_validator("error", mode="before")
    @classmethod
    def legacy_error(cls, value):
        return {"message": value} if isinstance(value, str) else value

    @field_validator("latency_ms", mode="before")
    @classmethod
    def valid_latency(cls, value):
        return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None

    @field_validator("application_model_calls", mode="before")
    @classmethod
    def valid_count(cls, value):
        return value if type(value) is int and value >= 0 else None

    def record(self):
        context = self.contexts[0] if self.contexts else None
        evidence = None
        if context:
            excerpt = " ".join(context.content.split())
            evidence = {"rank": context.rank or 1, "document": context.title, "heading": context.heading_path,
                        "source": context.retrieval_source, "excerpt": excerpt[:100] + ("…" if len(excerpt) > 100 else "")}
        issues = list(self.config_issues)
        if self.error is None and not self.effective_config:
            issues.append("缺少服务端实际配置")
        return {"case_id": self.case_id, "latency_ms": self.latency_ms, "application_model_calls": self.application_model_calls,
                "model_call_source": self.model_call_source, "retrieval_error": self.error.message if self.error else None,
                "config_issues": issues, "degraded": self.degraded, "evidence": evidence,
                "synonym_applied": bool((self.query_processing or {}).get("synonym_applied")),
                "score_error": None, "context_precision": None, "context_recall": None}


def valid_score(value):
    return type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 1


def mean(values):
    return fmean(values) if values else None


def percentile(ordered, fraction):
    if not ordered:
        return None
    position = (len(ordered) - 1) * fraction
    lo, hi = math.floor(position), math.ceil(position)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (position - lo)


def summarize(rows):
    success = [r for r in rows if r["retrieval_error"] is None]
    latencies = sorted(r["latency_ms"] for r in success if r["latency_ms"] is not None)
    calls = [r["application_model_calls"] for r in success if r["application_model_calls"] is not None]
    result = {m: mean([r[m] for r in success if valid_score(r[m])]) for m in QUALITY}
    quality_samples = {m: sum(valid_score(r[m]) for r in success) for m in QUALITY}
    return dict(result, quality_samples=quality_samples, total=len(rows), successful_requests=len(success), failed_requests=len(rows)-len(success),
                scored=sum(all(valid_score(r[m]) for m in QUALITY) for r in success),
                failed=sum(r["retrieval_error"] is not None or r["score_error"] is not None for r in rows),
                config_anomalies=sum(bool(r["config_issues"] or r["degraded"]) for r in rows),
                synonym_matches=sum(r["synonym_applied"] for r in success),
                latency_samples=len(latencies), mean_latency_ms=mean(latencies), p50_latency_ms=percentile(latencies, .5),
                p95_latency_ms=percentile(latencies, .95), max_latency_ms=max(latencies, default=None),
                model_call_samples=len(calls), missing_model_counts=len(rows)-len(calls),
                mean_application_model_calls=mean(calls))


def difference(before, after):
    if before is None or after is None:
        return None
    # Stable decimal boundaries for inclusive thresholds such as 0.78 - 0.75.
    return round(after - before, 12)


def changes(baseline, candidate):
    delta = {m: difference(baseline[m], candidate[m]) for m in (*QUALITY, P95, CALLS)}
    delta["p95_increase_ratio"] = (round(delta[P95] / baseline[P95], 12)
                                   if baseline[P95] and delta[P95] is not None else None)
    delta["model_calls_increase"] = delta[CALLS]
    return delta


def decide(primary, limits, delta, *, quality_complete, latency_complete, calls_complete, issues):
    improvement = limits.min_primary_improvement
    def at_least(value, minimum):
        return value >= minimum if quality_complete and value is not None else None
    guards = {m: at_least(delta[m], g.min_delta) for m, g in limits.quality_guardrails.items()}
    primary_pass = at_least(delta[primary], improvement)
    positive = any(delta[m] is not None and delta[m] > 0 for m in {primary, *guards})
    ratio, calls = delta["p95_increase_ratio"], delta[CALLS]
    p95_pass = ratio <= limits.cost_limits.max_p95_increase_ratio if latency_complete and ratio is not None else None
    calls_pass = calls <= limits.cost_limits.max_model_calls_increase if calls_complete and calls is not None else None
    complete = not issues and quality_complete and primary_pass is not None and None not in guards.values() and None not in (p95_pass, calls_pass)
    checks = {"integrity": complete, "quality_complete": quality_complete, "primary": primary_pass, "positive_quality": positive if quality_complete else None,
              "guardrails": guards, "p95": p95_pass, "application_model_calls": calls_pass}
    if not complete:
        verdict = "execution_failed"
    elif False in guards.values() or (delta[primary] <= -improvement if improvement else delta[primary] < 0):
        verdict = "regression"
    elif not primary_pass or not positive:
        verdict = "no_clear_benefit"
    elif not (p95_pass and calls_pass):
        verdict = "effective_high_cost"
    else:
        verdict = "effective"
    return verdict, checks
