"""Score immutable retrieval results once and render their four-metric comparison offline."""

import hashlib
import os
from uuid import uuid4

from pydantic import ValidationError

from app.core.config import Settings
from scripts.eval_artifacts import analysis_folder, failure_report, now, raw_rows, run_inputs, snapshot_digest
from scripts.eval_capture import ordered_map
from scripts.eval_comparison import compare
from scripts.eval_dataset import ROOT, write_json
from scripts.eval_experiment import GROUPS
from scripts.eval_metrics import CaseResult, METRICS_VERSION, summarize, valid_score
from scripts.eval_report import publish
from scripts.golden_dataset import read_json
from scripts.run_retrieval_eval import make_judge


async def score_run(folder):
    manifest, experiment, dataset = run_inputs(folder)
    output = analysis_folder(folder, manifest)
    raw = {}
    raw_digests = {}
    try:
        for g in GROUPS:
            digest = hashlib.sha256()
            rows = raw_rows(folder, g, dataset.cases, manifest["run_id"], digest=digest)
            raw[g] = [CaseResult.model_validate(row) for row in rows]
            raw_digests[g] = digest.hexdigest()
    except (ValueError, OSError) as exc:
        message = "逐题原始结果字段无效" if isinstance(exc, ValidationError) else str(exc)
        failure_report(folder, manifest, message)
        raise ValueError(message) from None

    os.environ["RAGAS_DO_NOT_TRACK"] = "true"
    timeout = experiment.ragas_timeout_seconds or 120
    model = judge = init_error = None
    if any(r.contexts and r.error is None for rows in raw.values() for r in rows):
        try:
            model, judge = make_judge(Settings(_env_file=ROOT / ".env"), max_workers=experiment.ragas_concurrency, timeout=timeout)
        except ValidationError:
            init_error = "裁判环境配置格式有误"
        except ValueError as exc:
            init_error = str(exc)
    settings = dict(model=model, temperature=0, timeout_seconds=timeout, max_workers=experiment.ragas_concurrency,
                    max_retries=2, seed=42)
    generation = uuid4().hex
    digests = {}
    score_errors = False

    async def evaluate(result):
        record = result.record()
        if result.error is not None:
            return record
        if not result.contexts:
            record.update(context_precision=0.0, context_recall=0.0)
            return record
        if init_error:
            record["score_error"] = init_error
            return record
        try:
            precision, recall = await judge(result, [c.content for c in result.contexts])
            for key, value in (("context_precision", precision), ("context_recall", recall)):
                if valid_score(value):
                    record[key] = value
                else:
                    record["score_error"] = "RAGAS 返回缺失、越界或非有限评分"
        except ValueError as exc:
            record["score_error"] = str(exc) or "裁判评分失败"
        except Exception as exc:
            record["score_error"] = f"裁判调用失败（{type(exc).__name__}）"
        return record

    for group, results in raw.items():
        cases = [r async for r in ordered_map(results, experiment.ragas_concurrency, evaluate)]
        score_errors |= any(r["score_error"] is not None for r in cases)
        metrics = dict(metrics_schema_version=METRICS_VERSION, run_id=manifest["run_id"], score_id=generation,
                       group=group, dataset_version=dataset.version, judge=settings, judge_model=model,
                       summary=summarize(cases), cases=cases)
        write_json(output / f"{group}.metrics.json", metrics)
        digests[group] = snapshot_digest(metrics)
    manifest.update(score_id=generation, scored_at=now(), judge_model=model, judge=settings, status="scored",
                    metrics_sha256=digests, raw_sha256=raw_digests, judge_initialization_error=init_error)
    manifest.pop("error", None)
    manifest["observability"]["ragas_do_not_track"] = os.environ["RAGAS_DO_NOT_TRACK"]
    write_json(output / "manifest.json", manifest)
    return not score_errors


def report_run(folder):
    source, experiment, dataset = run_inputs(folder)
    output = analysis_folder(folder, source)
    manifest = read_json(output / "manifest.json")
    if manifest.get("status") == "failed":
        if (output / "comparison.json").is_file():
            comparison = read_json(output / "comparison.json")
            if comparison.get("verdict") != "execution_failed" or comparison.get("run_id") != source["run_id"]:
                raise ValueError("失败报告与运行清单不一致")
            publish(output, comparison, folder)
            return False
        raise ValueError("缺少已保存的评分")
    groups = {}
    ids = [case.id for case in dataset.cases]
    try:
        for g in GROUPS:
            filename = f"{g}.metrics.json"
            metrics = read_json(output / filename)
            if (metrics.get("metrics_schema_version") != METRICS_VERSION or not manifest.get("score_id") or
                    metrics.get("score_id") != manifest["score_id"] or metrics.get("run_id") != source["run_id"] or
                    metrics.get("judge") != manifest.get("judge") or metrics.get("dataset_version") != dataset.version or
                    metrics.get("group") != g or snapshot_digest(metrics) != manifest.get("metrics_sha256", {}).get(g) or
                    [r["case_id"] for r in metrics["cases"]] != ids):
                raise ValueError(f"{filename} 与本次运行、评分批次或套件不一致")
            groups[g] = metrics["cases"]
    except (ValueError, OSError, TypeError, KeyError) as exc:
        failure_report(folder, manifest, str(exc))
        raise ValueError(str(exc)) from None
    source_issues = []
    for g in GROUPS:
        path = folder / f"{g}.raw.jsonl"
        try:
            with path.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
        except OSError:
            source_issues.append(f"{path.name} 不可读取，已保存的评分缺少可复核的原始证据")
            continue
        if manifest.get("raw_sha256", {}).get(g) != digest:
            source_issues.append(f"{path.name} 与评分时的原始记录不一致")
    comparison = compare(manifest, experiment, dataset, groups, source_issues)
    publish(output, comparison, folder)
    valid = comparison["verdict"] != "execution_failed"
    manifest.update(status="completed" if valid else "inconclusive", ended_at=manifest.get("ended_at") or now(),
                    report_score_id=manifest["score_id"], reported_at=now())
    write_json(output / "manifest.json", manifest)
    return valid
