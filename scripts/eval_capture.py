"""Capture one authenticated retrieval per case, including server execution evidence."""

import asyncio
import math
from collections import deque
from time import perf_counter

import httpx
from pydantic import ValidationError

from app.core.error_codes import ErrorCode
from app.schemas.rag import RetrievalResult


async def ordered_map(items, limit, function):
    """Bound both scheduled work and the reorder buffer; cancel unfinished work on exit."""
    items = iter(items)
    pending = deque()
    try:
        while True:
            while len(pending) < limit:
                item = next(items, None)
                if item is None:
                    break
                pending.append(asyncio.create_task(function(item)))
            if not pending:
                return
            yield await pending.popleft()
    finally:
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)


class Pacer:
    def __init__(self, qps):
        self.interval = 1 / qps
        self.lock = asyncio.Lock()
        self.next_start = 0

    async def wait(self):
        async with self.lock:
            loop = asyncio.get_running_loop()
            await asyncio.sleep(max(0, self.next_start - loop.time()))
            self.next_start = loop.time() + self.interval


def response_data(response):
    try:
        body = response.json()
    except ValueError:
        body = {}
    code = body.get("code") if isinstance(body, dict) else None
    if not response.is_success or type(code) is not int or code != ErrorCode.SUCCESS:
        try:
            reason = ErrorCode(code).name
        except (TypeError, ValueError):
            reason = "INVALID_RESPONSE"
        raise ValueError(f"HTTP {response.status_code} {reason}")
    if not isinstance(body.get("data"), dict):
        raise ValueError("响应缺少 data 对象")
    return body["data"]


async def preflight(client, experiment, cases):
    count = len(cases)
    data = response_data(await client.get("api/v1/auth/me"))
    try:
        features, limits = data["features"], data["limits"]
        remaining = limits["retrieve_daily"] - data["usage"]["retrieve_daily_count"]
        qps = limits["retrieve_qps"]
        if qps <= 0:
            raise ValueError("租户 QPS 不可用")
        total = 2 * (count + min(count, experiment.warmup_cases))
        if remaining < total:
            raise ValueError(f"剩余每日配额 {remaining}，本次需要 {total} 次检索")
        for arm in (experiment.baseline, experiment.candidate):
            d = arm.declared()
            if (arm.profile not in features["allowed_profiles"] or
                    (d["retrieval_options.mode"] == "hybrid" and not features["hybrid_allowed"]) or
                    (d["query_options.enabled"] and not features["query_rewrite_allowed"]) or
                    (d["rerank_options.enabled"] and not features["rerank_allowed"])):
                raise ValueError(f"套餐不允许组 {arm.label} 的配置")
        if "synonym" in experiment.target:
            from app.schemas.knowledge_base import KnowledgeSettings
            for kb in dict.fromkeys(case.kb_id for case in cases):
                info = response_data(await client.get(f"api/v1/knowledge-bases/{kb}"))
                try:
                    if not KnowledgeSettings.model_validate(info["settings"]).synonyms:
                        raise ValueError(f"知识库 {kb} 缺少有效同义词表")
                except ValidationError:
                    raise ValueError(f"知识库 {kb} 的同义词表格式有误") from None
        return {"plan": data["plan"], "remaining_before_run": remaining, "requests_planned": total,
                "tenant_qps": qps, "effective_qps": min(qps, experiment.max_qps or qps)}
    except (KeyError, TypeError):
        raise ValueError("租户预检查响应缺少有效的套餐、用量或配额") from None


def analyze(result, case, arm, target):
    meta = result.metadata
    policy, query, retrieval, rank = meta.tenant_policy, meta.query_processing, meta.retrieval, meta.rerank
    offline = meta.offline
    actual = {"profile": policy.retrieve_profile, "plan": policy.plan, "top_k": meta.top_k,
              "rewrite_enabled": policy.effective_query_rewrite, "rerank_enabled": policy.effective_rerank,
              "retrieval_mode": retrieval.mode if retrieval else None,
              "synonym_enabled": offline.synonym_enabled if offline else None,
              "vector_top_k": retrieval.vector_top_k if retrieval else None,
              "bm25_top_k": retrieval.bm25_top_k if retrieval else None,
              "rrf_k": retrieval.rrf_k if retrieval else None, "top_n": rank.top_n if rank else None}
    issues = []
    if result.kb_ids != [case.kb_id] or result.kb_id != case.kb_id or any(
            chunk.kb_id != case.kb_id for chunk in result.retrieved_chunks):
        issues.append("知识库路由与题目不一致")
    if result.query != case.question:
        issues.append("返回的原始 query 与题目不一致")
    if not offline or offline.langfuse_enabled or meta.trace_id is not None:
        issues.append("未确认后端已关闭本次 Langfuse 上报")
    d = arm.declared()
    mode = d["retrieval_options.mode"]
    expected = {"profile": arm.profile, "top_k": d["top_k"], "retrieval_mode": mode,
                "rewrite_enabled": d["query_options.enabled"], "synonym_enabled": d["query_options.synonym_enabled"],
                "rerank_enabled": d["rerank_options.enabled"]}
    for key, active, inactive in (("vector_top_k", mode != "bm25", 0), ("bm25_top_k", mode != "vector", 0),
                                  ("rrf_k", mode == "hybrid", None)):
        path = f"retrieval_options.{key}"
        if not active or path in d:
            expected[key] = d.get(path) if active else inactive
    if not expected["rerank_enabled"]:
        expected["top_n"] = d["top_k"]
    elif "rerank_options.top_n" in d:
        expected["top_n"] = d["rerank_options.top_n"]
    for key, value in expected.items():
        if actual[key] != value:
            issues.append(f"{key}: 声明 {value}，实际 {actual[key]}")
    if policy.effective_mode != mode or not retrieval or (mode == "hybrid" and retrieval.fusion != "rrf"):
        issues.append("租户策略或实际召回通道与声明不一致")
    if not query or query.strategy != ("rewrite" if expected["rewrite_enabled"] else "noop"):
        issues.append("改写执行策略与声明不一致")
    if query and not expected["synonym_enabled"] and query.synonym_applied:
        issues.append("关闭同义词后仍发生了扩展")
    if "synonym" in target and expected["synonym_enabled"] and (not offline or not offline.synonym_groups):
        issues.append("未准备有效同义词表")
    if not rank or rank.enabled != expected["rerank_enabled"]:
        issues.append("重排执行状态与声明不一致")
    if rank and rank.enabled and not rank.candidate_count:
        issues.append("重排没有候选，未执行模型重排")
    if rank and len(result.retrieved_chunks) > rank.top_n:
        issues.append("返回条数超过实际输出上限")
    degraded = {}
    for stage, component, reason in (("rewrite", query, "degraded_reason"), ("retrieval", retrieval, "degraded_reason"),
                                     ("rerank", rank, "error")):
        if component and component.degraded:
            degraded[stage] = getattr(component, reason) or "degraded"
    if degraded:
        issues.append("服务端发生降级：" + ", ".join(degraded))
    executed = int(bool(query and query.strategy == "rewrite" and not query.degraded)) + int(
        bool(rank and rank.enabled and rank.candidate_count and not rank.degraded))
    if offline and offline.application_model_calls is not None and offline.application_model_calls < executed:
        issues.append("服务端记录的应用模型调用次数不足以支持执行状态")
    if offline and str(case.kb_id) not in offline.settings_refs:
        issues.append("缺少当前知识库的设置快照引用")
    return actual, issues, degraded


async def capture(client, pacer, case, group, experiment):
    arm = getattr(experiment, group)
    payload = dict(arm.request_options(), query=case.question, user_id="eval_runner", kb_id=str(case.kb_id))
    row = dict(case_id=case.id, group=group, kb_id=str(case.kb_id), kb_alias=case.kb_alias,
               question=case.question, ground_truth=case.ground_truth, contexts=[], answer=None,
               latency_ms=None, application_model_calls=None, model_call_source="no server evidence",
               effective_config={}, stage_timings_ms={}, degraded={}, runner_retries=0, upstream_retries=None,
               config_issues=[], error=None)
    await pacer.wait()
    started = perf_counter()
    try:
        try:
            async with asyncio.timeout(experiment.timeout_seconds):
                response = await client.post("api/v1/rag/retrieve", json=payload,
                                             headers={"X-RAGNexus-Offline-Eval": "true"})
        finally:
            row["latency_ms"] = (perf_counter() - started) * 1000
        row["http_status"] = response.status_code
        result = RetrievalResult.model_validate(response_data(response))
        meta = result.metadata
        for chunk in result.retrieved_chunks:
            scores = (chunk.score, chunk.rerank_score, chunk.vector_score, chunk.bm25_score)
            if not chunk.content.strip() or any(v is not None and not math.isfinite(v) for v in scores):
                raise ValueError("响应中存在空上下文或非有限分数")
        actual, issues, degraded = analyze(result, case, arm, experiment.target)
        row.update(effective_config=actual, config_issues=issues, degraded=degraded, log_id=str(meta.log_id), trace_id=meta.trace_id,
                   query_processing=meta.query_processing.model_dump() if meta.query_processing else None,
                   retrieval=meta.retrieval.model_dump(mode="json") if meta.retrieval else None,
                   rerank=meta.rerank.model_dump() if meta.rerank else None, server_latency_ms=meta.latency_ms)
        for index, chunk in enumerate(result.retrieved_chunks, 1):
            context = chunk.model_dump(mode="json", exclude_none=True)
            context.update(rank=index, heading_path=chunk.metadata.heading_path if chunk.metadata else None)
            row["contexts"].append(context)
        if offline := meta.offline:
            row.update(offline=offline.model_dump(), stage_timings_ms=offline.stage_timings_ms,
                       application_model_calls=offline.application_model_calls, model_call_source=offline.model_call_source)
    except ValidationError as exc:
        paths = [".".join(map(str, e["loc"])) for e in exc.errors(include_input=False)]
        row["error"] = "响应格式错误：" + ", ".join(paths)
    except (httpx.RequestError, TimeoutError) as exc:
        row["error"] = type(exc).__name__
    except ValueError as exc:
        row["error"] = str(exc)
    return row
