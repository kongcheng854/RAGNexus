"""Run artifacts shared by capture, score and report."""

import hashlib
import json
import os
from contextlib import contextmanager
import subprocess
from datetime import datetime, timezone

from scripts.eval_dataset import ROOT, golden_suite, write_json
from scripts.eval_experiment import read_experiment
from scripts.golden_dataset import read_json


def snapshot_digest(value):
    content = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def git_state():
    def git(*args):
        try:
            result = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, timeout=5, check=True)
            return result.stdout.decode("utf-8", errors="replace").strip()
        except (OSError, subprocess.SubprocessError):
            return None

    status = git("status", "--porcelain")
    return {"commit": git("rev-parse", "HEAD"), "dirty": bool(status) if status is not None else None}


@contextmanager
def run_lock(folder):
    # Kernel releases the lock if a scoring process exits unexpectedly.
    if not (folder / "manifest.json").is_file():
        raise ValueError(f"缺少 {folder / 'manifest.json'}")
    with (folder / ".run.lock").open("a+b") as stream:
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise ValueError("此运行目录正在评分或生成报告") from None
        yield


def run_inputs(folder):
    manifest = read_json(folder / "manifest.json")
    if manifest.get("status") == "capturing":
        raise ValueError("正式检索尚未结束，暂不可评分")
    experiment = read_experiment(folder / "experiment.snapshot.json", archived=True)
    data = read_json(folder / "dataset.snapshot.json")
    fingerprints = {"dataset": snapshot_digest(data), "experiment": snapshot_digest(experiment.model_dump(exclude_none=True))}
    if fingerprints != manifest.get("snapshot_sha256"):
        raise ValueError("运行快照的内容摘要与 manifest 不一致")
    dataset = golden_suite(data, experiment.suite)
    if (manifest["experiment_id"], manifest["dataset_version"], manifest["suite"]) != (
            experiment.experiment_id, dataset.version, dataset.selected_suite):
        raise ValueError("运行快照与 manifest 中的数据集版本、套件或实验不一致")
    return manifest, experiment, dataset


def raw_rows(folder, group, cases, run_id, *, digest=None):
    path = folder / f"{group}.raw.jsonl"
    rows = []
    with path.open("rb") as stream:
        for line in stream:
            if digest is not None:
                digest.update(line)
            n = len(rows)
            try:
                row = json.loads(line)
                if row.get("warmup") is True or row.get("phase") == "warmup":
                    continue
                if n >= len(cases) or row["run_id"] != run_id:
                    raise ValueError
                case = cases[n]
                if (row["case_id"], row["group"], row["kb_id"], row["kb_alias"], row["question"], row["ground_truth"]) != (
                        case.id, group, str(case.kb_id), case.kb_alias, case.question, case.ground_truth):
                    raise ValueError
                if not isinstance(row["contexts"], list) or any(
                        not isinstance(c["content"], str) or not c["content"].strip() for c in row["contexts"]):
                    raise ValueError
                if not isinstance(row["config_issues"], list) or not all(isinstance(s, str) for s in row["config_issues"]):
                    raise ValueError
            except (ValueError, KeyError, TypeError, AttributeError):
                raise ValueError(f"{path} 第 {n+1} 行与运行快照不一致或缺少逐题数据") from None
            rows.append(row)
    if len(rows) != len(cases):
        raise ValueError(f"{path} 只有 {len(rows)}/{len(cases)} 道正式题，不能评分")
    return rows


def analysis_folder(folder, manifest):
    # Chapter 02 reports used a different P95 and decision rule.
    return folder if manifest.get("metrics_schema_version") == "2.0" else folder / "analysis-v2"


def failure_report(folder, manifest, error):
    from scripts.eval_comparison import compare
    from scripts.eval_experiment import GROUPS
    from scripts.eval_report import publish

    output = analysis_folder(folder, manifest)
    experiment = read_experiment(folder / "experiment.snapshot.json", archived=True)
    dataset = golden_suite(read_json(folder / "dataset.snapshot.json"), experiment.suite)
    groups = {}
    for group in GROUPS:
        groups[group] = []
        digest = manifest.get("metrics_sha256", {}).get(group)
        if not digest:
            continue
        try:
            metrics = read_json(output / f"{group}.metrics.json")
            if (snapshot_digest(metrics) == digest and metrics["run_id"] == manifest["run_id"] and
                    metrics["score_id"] == manifest.get("score_id") and metrics["group"] == group):
                groups[group] = metrics["cases"]
        except (OSError, ValueError, KeyError, TypeError):
            continue
    manifest = dict(manifest, status="failed", ended_at=now(), error=error)
    write_json(output / "manifest.json", manifest)
    comparison = compare(manifest, experiment, dataset, groups, input_errors=[error])
    publish(output, comparison, folder)
