import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.eval_artifacts import snapshot_digest
from scripts.eval_dataset import write_json
from scripts.eval_experiment import Experiment
from scripts.eval_metrics import CALLS, P95, CaseResult, changes, decide, percentile, summarize
from scripts.eval_report import VERDICTS, render
from scripts.eval_score import report_run, score_run
from tests.test_eval_dataset import example_dataset
from tests.test_eval_experiment import config


class MetricTests(unittest.TestCase):
    def test_five_verdicts_and_inclusive_boundaries(self):
        limits = Experiment.model_validate(config()).decision
        for minimum, recall, precision, ratio, calls, complete, expected in (
            (.03, .03, -.02, .40, 1, True, "effective"),
            (.03, .03, 0, .401, 1, True, "effective_high_cost"),
            (.03, .03, 0, 0, 1.01, True, "effective_high_cost"),
            (.03, .029, .10, 0, 0, True, "no_clear_benefit"),
            (.03, -.029, .10, 0, 0, True, "no_clear_benefit"),
            (.03, -.03, .10, 0, 0, True, "regression"),
            (.03, .09, -.021, 1, 3, True, "regression"),
            (0, 0, 0, .50, 0, True, "no_clear_benefit"),
            (0, 0, .01, 0, 0, True, "effective"),
            (0, -.0001, .10, 0, 0, True, "regression"),
            (.03, .20, -.30, 0, None, False, "execution_failed"),
        ):
            with self.subTest(expected=expected, minimum=minimum, recall=recall):
                decision = limits.model_copy(update={"min_primary_improvement": minimum})
                delta = {"context_recall": recall, "context_precision": precision,
                         "p95_increase_ratio": ratio, CALLS: calls}
                verdict, checks = decide("context_recall", decision, delta, quality_complete=True,
                                         latency_complete=True, calls_complete=complete, issues=[])
                self.assertEqual(verdict, expected)
                self.assertEqual(checks["integrity"], complete)
        self.assertEqual(decide("context_recall", limits, delta, quality_complete=True, latency_complete=True,
                                calls_complete=False, issues=["配置漂移"])[0], "execution_failed")

    def test_precision_can_be_primary_and_recall_its_guardrail(self):
        definition = config()
        definition["primary_metric"] = "context_precision"
        definition["decision"]["quality_guardrails"] = {"context_recall": {"min_delta": -.02}}
        experiment = Experiment.model_validate(definition)
        delta = {"context_precision": .04, "context_recall": -.02, "p95_increase_ratio": .40, CALLS: 1.0}
        verdict, checks = decide(experiment.primary_metric, experiment.decision, delta, quality_complete=True,
                                 latency_complete=True, calls_complete=True, issues=[])
        self.assertEqual(verdict, "effective")
        self.assertEqual(checks["guardrails"], {"context_recall": True})

    def test_runtime_sample_counts_and_interpolation(self):
        base = {"case_id": "id", "question": "问", "ground_truth": "事实", "contexts": [], "effective_config": {"top_k": 5},
                "config_issues": [], "error": None}
        rows = []
        for ms, calls, error in [(100, 1, None), (200, None, None), (400, 3, None), (9999, 30, "HTTP 500")]:
            r = CaseResult(**(base | dict(latency_ms=ms, application_model_calls=calls, error=error))).record()
            rows.append(r)
        s = summarize(rows)
        self.assertEqual((s["successful_requests"], s["failed_requests"], s["model_call_samples"], s[CALLS]), (3, 1, 2, 2.0))
        self.assertAlmostEqual(s["mean_latency_ms"], 700 / 3)
        self.assertEqual((s["p50_latency_ms"], s[P95], s["max_latency_ms"]), (200, 380, 400))
        self.assertIsNone(percentile([], .95))
        self.assertEqual(percentile([17], .95), 17)
        self.assertIsNone(CaseResult(**(base | dict(latency_ms=float("inf")))).latency_ms)
        self.assertIsNone(CaseResult(**(base | dict(application_model_calls=-1))).application_model_calls)

    def test_delta_units_and_zero_denominator(self):
        a = {"context_recall": .72, "context_precision": .81, P95: 100, CALLS: 0}
        b = {"context_recall": .78, "context_precision": .79, P95: 125, CALLS: 1}
        self.assertEqual(changes(a, b), dict(context_recall=.06, context_precision=-.02, p95_latency_ms=25,
                         p95_increase_ratio=.25, mean_application_model_calls=1, model_calls_increase=1))
        self.assertIsNone(changes(a | {P95: 0}, b)["p95_increase_ratio"])


class SavedReportTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        data = example_dataset()
        selected = [data["cases"][i] for i in (0, 1, 2, 3, 10, 15)]
        for i, case in enumerate(selected):
            case["tags"] = ["small"] if i < 2 else ["large"]
        data["suites"]["sample"] = {"case_ids": [c["id"] for c in selected], "description": "临时输入"}
        conf = Experiment.model_validate(config() | {"suite": "sample", "ragas_concurrency": 2,
                                                       "ragas_timeout_seconds": 37}).model_dump(exclude_none=True)
        write_json(self.root / "experiment.snapshot.json", conf)
        write_json(self.root / "dataset.snapshot.json", data)
        kb = data["knowledge_bases"]["ecommerce"]["kb_id"]
        self.manifest = dict(metrics_schema_version="2.0", run_id="test", experiment_id=conf["experiment_id"],
                             dataset_version=data["version"], suite="sample", status="captured", observability={},
                             groups={g: {"success": 6, "failed": 0, "concurrency": 4} for g in ("baseline", "candidate")},
                             run_config={k: conf[k] for k in ("concurrency", "ragas_concurrency", "warmup_cases", "timeout_seconds")},
                             snapshot_sha256={"experiment": snapshot_digest(conf), "dataset": snapshot_digest(data)})
        self.rows = {}
        for g in ("baseline", "candidate"):
            self.rows[g] = [dict(case_id=c["id"], group=g, kb_id=kb, kb_alias="ecommerce", question=c["question"],
                                 ground_truth=c["ground_truth"], answer=None, run_id="test", latency_ms=(i+1)*10,
                                 application_model_calls=int(g == "candidate"), error=None, config_issues=[],
                                 effective_config={"profile": "custom"}, contexts=[
                                     {"content": f"{g}-{i} " + "文"*130, "title": "证据文档", "heading_path": "政策", "rank": 1}
                                 ]) for i, c in enumerate(selected)]
        self.seen = []
        self.bad_score = False
        self.factory = self.enterContext(patch("scripts.eval_score.make_judge", return_value=("fixture-judge", self.judge)))

    async def judge(self, case, contexts):
        self.seen.append((case.case_id, case.question, case.ground_truth, contexts))
        self.assertIsInstance(case, CaseResult)
        self.assertIsNone(case.answer)
        arm, i = contexts[0].split(" ")[0].split("-")
        if self.bad_score and arm == "candidate" and i == "0":
            return float("nan"), None
        return (.8, .5 if arm == "baseline" else [.85, .8, .7, .45, .4, .35][int(i)])

    def save(self):
        write_json(self.root / "manifest.json", self.manifest)
        for g, rows in self.rows.items():
            warmup = rows[0] | {"warmup": True, "latency_ms": 10_000, "application_model_calls": 900}
            with (self.root / f"{g}.raw.jsonl").open("w", encoding="utf-8") as stream:
                for row in [warmup, *rows]:
                    stream.write(json.dumps(row) + "\n")

    def read(self, name):
        return json.loads((self.root / name).read_text(encoding="utf-8"))

    async def test_same_inputs_report_sections_ranking_and_judge(self):
        self.save()
        self.enterContext(patch.dict(os.environ, {"RAGAS_DO_NOT_TRACK": "false"}))
        self.assertTrue(await score_run(self.root))
        self.assertEqual(len(self.seen), 12)
        self.assertEqual(os.environ["RAGAS_DO_NOT_TRACK"], "true")
        self.assertEqual(self.factory.call_args.kwargs, {"max_workers": 2, "timeout": 37})
        self.assertEqual(self.read("manifest.json")["judge"],
                         dict(model="fixture-judge", temperature=0, max_workers=2, timeout_seconds=37, seed=42, max_retries=2))
        for actual, row in zip(self.seen, self.rows["baseline"] + self.rows["candidate"], strict=True):
            self.assertEqual(actual, (row["case_id"], row["question"], row["ground_truth"], [row["contexts"][0]["content"]]))
        self.assertTrue(report_run(self.root))
        c = self.read("comparison.json")
        self.assertEqual(c["verdict"], "effective")
        self.assertEqual(c["candidate"][P95], 57.5)
        self.assertEqual(c["candidate"][CALLS], 1)
        self.assertEqual(c["most_improved"], [r["case_id"] for r in self.rows["candidate"][:3]])
        self.assertEqual(c["most_regressed"], [r["case_id"] for r in reversed(self.rows["candidate"][3:])])
        slices = {(s["field"], s["value"]): s for s in c["slices"]}
        self.assertIsNone(slices["tags", "small"]["aggregate"])
        self.assertIsNotNone(slices["tags", "large"]["aggregate"])
        md = (self.root / "report.md").read_text(encoding="utf-8")
        self.assertEqual(md, render(c))
        self.assertEqual(sum(line.startswith("## ") for line in md.splitlines()), 11)
        top = md.split("## 实验目标")[0]
        for word in (VERDICTS["effective"][0], "百分点", "P95 延迟", "平均应用模型调用数", c["recommendation"]):
            self.assertIn(word, top)
        self.assertIn("证据文档", md)
        self.assertNotIn("文"*110, md)
        self.assertEqual(len(self.seen), 12)

    async def test_missing_costs_preserve_quality_but_block_cost_acceptance(self):
        self.rows["candidate"][0]["latency_ms"] = None
        self.rows["candidate"][1]["application_model_calls"] = None
        self.save()
        self.assertTrue(await score_run(self.root))
        self.assertFalse(report_run(self.root))
        c = self.read("comparison.json")
        self.assertEqual(c["verdict"], "execution_failed")
        self.assertIn("质量评分已完成，成本信息不完整", c["reasons"])
        self.assertEqual(c["candidate"]["scored"], 6)
        self.assertEqual(c["candidate"]["model_call_samples"], 5)
        self.assertEqual(c["candidate"][CALLS], 1.)
        self.assertIsNone(c["checks"]["p95"])
        self.assertIsNone(c["checks"]["application_model_calls"])

    async def test_failed_score_and_empty_retrieval_are_distinct(self):
        self.bad_score = True
        self.rows["candidate"][1]["contexts"] = []
        self.save()
        self.assertFalse(await score_run(self.root))
        self.assertFalse(report_run(self.root))
        rows = self.read("candidate.metrics.json")["cases"]
        self.assertIsNone(rows[0]["context_precision"])
        self.assertIsNotNone(rows[0]["score_error"])
        self.assertEqual((rows[1]["context_precision"], rows[1]["context_recall"], rows[1]["score_error"]), (0., 0., None))
        self.assertEqual(self.read("comparison.json")["failed_cases"]["candidate"], 1)

    async def test_group_conditions_and_corrupt_artifacts_fail_closed(self):
        self.manifest["groups"]["candidate"]["concurrency"] = 1
        self.save()
        await score_run(self.root)
        self.assertFalse(report_run(self.root))
        self.assertTrue(any("并发" in s for s in self.read("comparison.json")["reasons"]))
        values = self.read("candidate.metrics.json")
        values["cases"][0]["context_recall"] = 1
        write_json(self.root / "candidate.metrics.json", values)
        with self.assertRaisesRegex(ValueError, "candidate.metrics"):
            report_run(self.root)
        failure = self.read("comparison.json")
        self.assertEqual(failure["verdict"], "execution_failed")
        self.assertEqual(failure["baseline"]["scored"], 6)
        self.assertEqual(failure["candidate"]["scored"], 0)

    async def test_missing_raw_after_scoring_retains_scores_and_withholds_verdict(self):
        self.save()
        await score_run(self.root)
        (self.root / "baseline.raw.jsonl").unlink()
        self.assertFalse(report_run(self.root))
        c = self.read("comparison.json")
        self.assertEqual(c["paired_cases"], 6)
        self.assertEqual(c["verdict"], "execution_failed")
        self.assertFalse(c["artifact_available"]["baseline.raw.jsonl"])
        self.assertTrue(any("原始证据" in reason for reason in c["reasons"]))

    async def test_archived_reports_preserved(self):
        del self.manifest["metrics_schema_version"]
        self.save()
        original = self.read("manifest.json")
        (self.root / "report.md").write_text("old report", encoding="utf-8")
        write_json(self.root / "comparison.json", {"status": "old"})
        await score_run(self.root)
        self.assertTrue(report_run(self.root))
        self.assertEqual(self.read("manifest.json"), original)
        self.assertEqual(self.read("comparison.json"), {"status": "old"})
        self.assertEqual((self.root / "report.md").read_text(encoding="utf-8"), "old report")
        self.assertEqual(self.read("analysis-v2/comparison.json")["artifacts"]["baseline.raw.jsonl"], "../baseline.raw.jsonl")
