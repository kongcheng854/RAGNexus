"""Run the baseline and candidate separately using immutable input snapshots."""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import httpx

from scripts.eval_artifacts import failure_report, git_state, now, run_lock, snapshot_digest
from scripts.eval_capture import Pacer, capture, ordered_map, preflight
from scripts.eval_dataset import ROOT, golden_suite, write_json
from scripts.eval_experiment import GROUPS, read_experiment
from scripts.golden_dataset import read_json


class EvidenceSummary:
    def __init__(self, experiment, plan):
        self.plan = plan
        self.experiment = experiment
        self.environments = {}
        self.arm_configs = {}
        self.baseline = {}

    def check(self, row):
        if row["error"]:
            return
        group, kb = row["group"], row["kb_id"]
        actual, diag, issues = row["effective_config"], row.get("offline", {}), row["config_issues"]
        if actual["plan"] != self.plan:
            issues.append("租户套餐与预检查不一致")
        runtime = {key: diag.get(key) for key in ("index", "embedding_model", "application_model", "settings_refs", "execution_limits")}
        if runtime != self.environments.setdefault(kb, runtime):
            issues.append("知识库设置、索引或模型与本次首次响应不一致")
        if not issues and actual != self.arm_configs.setdefault((group, kb), actual):
            issues.append("组内实际配置发生变化")
        if group == "baseline":
            self.baseline[row["case_id"]] = actual
            return
        left = self.baseline.get(row["case_id"])
        if not left:
            return
        # Named presets leave several counts to server defaults. Compare their observed values too.
        for field in ("vector_top_k", "bm25_top_k", "rrf_k", "top_n"):
            if left[field] == actual[field]:
                continue
            path = ("rerank_options." if field == "top_n" else "retrieval_options.") + field
            if path in self.experiment.changed_fields:
                continue
            if field == "top_n" and (left["rerank_enabled"] != actual["rerank_enabled"] or
                                     (not actual["rerank_enabled"] and left["top_k"] != actual["top_k"])):
                continue
            if field != "top_n" and left["retrieval_mode"] != actual["retrieval_mode"]:
                continue
            issues.append(f"两组共同配置 {field} 不一致")


async def collect(folder, client, experiment, cases, manifest):
    tenant = await preflight(client, experiment, cases)
    manifest["preflight"] = tenant
    pacer = Pacer(tenant["effective_qps"])
    evidence = EvidenceSummary(experiment, tenant["plan"])
    manifest["server_environments"] = evidence.environments
    observed = set()
    for group in GROUPS:
        async def fetch(case):
            return await capture(client, pacer, case, group, experiment)

        warmups = []
        async for row in ordered_map(cases[:experiment.warmup_cases], experiment.concurrency, fetch):
            warmups.append({"case_id": row["case_id"], "error": row["error"], "config_issues": row["config_issues"]})
        summary = {"concurrency": experiment.concurrency, "warmup": warmups, "success": 0, "failed": 0, "invalid_cases": [], "errors": {}, "effective_configs": []}
        manifest["groups"][group] = summary
        write_json(folder / "manifest.json", manifest)
        with (folder / f"{group}.raw.jsonl").open("x", encoding="utf-8") as stream:
            async for row in ordered_map(cases, experiment.concurrency, fetch):
                row["run_id"] = manifest["run_id"]
                evidence.check(row)
                stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
                stream.flush()
                summary["failed" if row["error"] else "success"] += 1
                if row["error"]:
                    summary["errors"][row["case_id"]] = row["error"]
                if row["config_issues"]:
                    summary["invalid_cases"].append(row["case_id"])
                config = row["effective_config"]
                if config and config not in summary["effective_configs"]:
                    summary["effective_configs"].append(config)
                offline = row.get("offline", {})
                observed.add(True if row.get("trace_id") else offline.get("langfuse_enabled"))
        manifest["observability"]["server_langfuse_states"] = sorted(observed, key=str)
        write_json(folder / "manifest.json", manifest)


async def execute(path, results, base_url, *, transport=None):
    from scripts.eval_score import score_run, report_run

    experiment = read_experiment(path)
    dataset_path = ROOT / experiment.dataset
    if not dataset_path.is_file():
        raise ValueError(f"找不到实验数据集 {dataset_path}，请提供已通过校验的正式 Golden Set")
    data = read_json(dataset_path)
    dataset = golden_suite(data, experiment.suite)
    if not dataset.cases:
        raise ValueError("所选套件没有题目")
    try:
        url = httpx.URL(base_url)
    except httpx.InvalidURL:
        raise ValueError("无效的检索服务基础地址") from None
    if url.scheme not in ("http", "https") or not url.host or url.userinfo or url.query or url.fragment:
        raise ValueError("服务基础地址必须是无凭据、无查询参数的 HTTP(S) URL")
    os.environ["RAGAS_DO_NOT_TRACK"] = "true"
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + experiment.experiment_id + "-" + uuid4().hex[:8]
    folder = Path(results) / run_id
    folder.mkdir(parents=True, exist_ok=False)
    print(f"运行目录 {folder}", flush=True)
    config = experiment.model_dump(exclude_none=True)
    manifest = dict(metrics_schema_version="2.0", run_id=run_id, experiment_id=experiment.experiment_id, status="capturing", started_at=now(),
                    ended_at=None, git=git_state(), dataset_name=dataset.name, dataset_version=dataset.version,
                    suite=experiment.suite, knowledge_bases=dataset.knowledge_bases, primary_metric=experiment.primary_metric,
                    run_config={k: config[k] for k in ("concurrency", "ragas_concurrency", "warmup_cases", "timeout_seconds")},
                    index_id=experiment.index_id, service_url=str(url), groups={},
                    observability={"requested_server_langfuse": False, "server_langfuse_states": [],
                                   "ragas_do_not_track": os.environ["RAGAS_DO_NOT_TRACK"]})
    manifest["snapshot_sha256"] = {"dataset": snapshot_digest(data), "experiment": snapshot_digest(config)}
    write_json(folder / "experiment.snapshot.json", config)
    write_json(folder / "dataset.snapshot.json", data)
    write_json(folder / "manifest.json", manifest)
    try:
        key = os.getenv("RAGNEXUS_API_KEY")
        if not key:
            raise ValueError("缺少租户环境变量 RAGNEXUS_API_KEY")
        async with httpx.AsyncClient(base_url=str(url).rstrip("/") + "/", headers={"Authorization": f"Bearer {key}"},
                                     timeout=experiment.timeout_seconds, trust_env=False, transport=transport) as client:
            await collect(folder, client, experiment, dataset.cases, manifest)
    except (ValueError, OSError, httpx.HTTPError) as exc:
        message = type(exc).__name__ if isinstance(exc, httpx.HTTPError) else str(exc)
        failure_report(folder, manifest, message)
        raise ValueError(message) from None
    manifest.update(status="captured", capture_ended_at=now())
    write_json(folder / "manifest.json", manifest)
    with run_lock(folder):
        try:
            await score_run(folder)
            return folder, report_run(folder)
        except (OSError, ValueError) as exc:
            failure_report(folder, read_json(folder / "manifest.json"), str(exc))
            raise
