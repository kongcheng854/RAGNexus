"""Compare paired case evidence against the experiment's predeclared decision."""

from scripts.eval_experiment import GROUPS
from scripts.eval_metrics import QUALITY, changes, decide, difference, mean, summarize


def case_analysis(dataset, groups, experiment):
    by_id = {g: {r["case_id"]: r for r in groups[g]} for g in GROUPS}
    details, buckets = {}, {}
    for case in dataset.cases:
        a, b = (by_id[g].get(case.id, {}) for g in GROUPS)
        quality = {g: {m: row.get(m) for m in QUALITY} for g, row in zip(GROUPS, (a, b))}
        delta = {m: difference(quality["baseline"][m], quality["candidate"][m]) for m in QUALITY}
        details[case.id] = {"question": case.question, **quality, "delta": delta,
                            "evidence": {g: row.get("evidence") for g, row in zip(GROUPS, (a, b))}}
        labels = [("case_type", case.case_type), ("suite", case.suite), ("kb_alias", case.kb_alias)]
        labels.extend(("tags", tag) for tag in dict.fromkeys(getattr(case, "tags", [])))
        for key in labels:
            buckets.setdefault(key, []).append(case.id)
    slices = []
    for (field, label), ids in buckets.items():
        paired = [i for i in ids if all(details[i]["delta"][m] is not None for m in QUALITY)]
        part = {"field": field, "value": label, "case_ids": ids, "paired_cases": len(paired), "aggregate": None}
        if len(paired) >= 3:
            agg = {g: {m: mean([details[i][g][m] for i in paired]) for m in QUALITY} for g in GROUPS}
            agg["delta"] = {m: difference(agg["baseline"][m], agg["candidate"][m]) for m in QUALITY}
            part.update(aggregate=agg, breached_guardrails=[
                m for m, guard in experiment.decision.quality_guardrails.items() if agg["delta"][m] < guard.min_delta])
        slices.append(part)
    primary = experiment.primary_metric
    ranked = sorted((i for i in details if details[i]["delta"][primary] is not None), key=lambda i: details[i]["delta"][primary])
    return {"cases": details, "slices": slices,
            "most_improved": [i for i in reversed(ranked) if details[i]["delta"][primary] > 0][:3],
            "most_regressed": [i for i in ranked if details[i]["delta"][primary] < 0][:3]}


def compare(manifest, experiment, dataset, groups, input_errors=()):
    issues = list(input_errors)
    expected = len(dataset.cases)
    summary = {g: summarize(groups[g]) for g in GROUPS}
    delta = changes(summary["baseline"], summary["candidate"])
    diagnostics = []
    batches = manifest.get("groups", {})
    runtime = manifest.get("run_config", {})
    if any(runtime.get(k) != getattr(experiment, k) for k in ("concurrency", "warmup_cases", "ragas_concurrency", "timeout_seconds")):
        issues.append("运行条件与实验快照不一致")
    for g, rows in groups.items():
        s = summary[g]
        batch = batches.get(g, {})
        if s["total"] != expected:
            issues.append(f"{g} 正式结果缺失，取得 {s['total']}/{expected} 题")
        if batch.get("concurrency", runtime.get("concurrency")) != experiment.concurrency:
            issues.append(f"{g} 业务并发与声明不一致")
        if not rows and "success" in batch and "failed" in batch:
            s.update(successful_requests=batch["success"], failed_requests=batch["failed"], request_count_source="manifest")
        elif (batch.get("success"), batch.get("failed")) != (s["successful_requests"], s["failed_requests"]):
            issues.append(f"{g} 成功或失败请求数与运行清单不一致")
        if s["failed"] > experiment.decision.max_failed_cases:
            issues.append(f"{g} 有 {s['failed']} 道失败题，超过预设上限 {experiment.decision.max_failed_cases}")
        if s["config_anomalies"]:
            issues.append(f"{g} 有 {s['config_anomalies']} 道题出现配置异常或降级")
        if "synonym" in experiment.target and getattr(experiment, g).declared()["query_options.synonym_enabled"] and not s["synonym_matches"]:
            issues.append(f"{g} 未观测到同义词扩展")
        for r in rows:
            errors = [s for s in (r["retrieval_error"], r["score_error"]) if s is not None]
            errors.extend(r["config_issues"])
            errors.extend(f"{stage} 降级（{why}）" for stage, why in r["degraded"].items())
            if r["latency_ms"] is None:
                errors.append("客户端耗时不可用")
            if r["application_model_calls"] is None:
                errors.append("应用模型调用数不可用")
            if errors:
                diagnostics.append({"group": g, "case_id": r["case_id"], "issues": errors})
    quality_complete = expected > 0 and all(s["scored"] == expected and not s["failed"] for s in summary.values())
    latency_complete = expected > 0 and all(s["latency_samples"] == expected for s in summary.values())
    calls_complete = expected > 0 and all(s["model_call_samples"] == expected for s in summary.values())
    if not quality_complete:
        issues.append("正式题的两项质量评分未全部完成")
    if not latency_complete or not calls_complete:
        issues.append("质量评分已完成，成本信息不完整" if quality_complete else "成本信息不完整")
    elif delta["p95_increase_ratio"] is None:
        issues.append("baseline P95 为零，无法核实延迟增幅")
    verdict, checks = decide(experiment.primary_metric, experiment.decision, delta, quality_complete=quality_complete,
                             latency_complete=latency_complete, calls_complete=calls_complete, issues=issues)
    analysis = case_analysis(dataset, groups, experiment)
    info = {g: getattr(experiment, g).declared() for g in GROUPS}
    return dict(schema_version="2.0", run_id=manifest["run_id"], score_id=manifest.get("score_id"),
                experiment_id=experiment.experiment_id, title=experiment.title, target=experiment.target,
                primary_metric=experiment.primary_metric, dataset_version=dataset.version, suite=experiment.suite,
                expected_cases=expected, paired_cases=sum(all(v is not None for v in r["delta"].values()) for r in analysis["cases"].values()),
                labels={g: getattr(experiment, g).label for g in GROUPS},
                declared_differences={k: {g: info[g][k] for g in GROUPS} for k in experiment.changed_fields},
                conditions={"knowledge_bases": dataset.knowledge_bases, "index_id": manifest.get("index_id"),
                            "server_environments": manifest.get("server_environments", {}), "run_config": runtime,
                            "preflight": manifest.get("preflight", {}), "git": manifest.get("git", {}),
                            "observability": manifest.get("observability", {}), "judge": manifest.get("judge", {})},
                provenance={"snapshot_sha256": manifest.get("snapshot_sha256", {}), "raw_sha256": manifest.get("raw_sha256", {}),
                            "metrics_sha256": manifest.get("metrics_sha256", {})},
                **summary, delta=delta, thresholds=experiment.decision.model_dump(), checks=checks, verdict=verdict,
                failed_cases={g: s["failed"] for g, s in summary.items()}, reasons=issues,
                diagnostics=diagnostics, **analysis)
