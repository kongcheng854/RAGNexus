import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace
from uuid import uuid4

import httpx

from scripts import export_eval_cases_from_langfuse as exporter
from scripts import run_retrieval_eval as runner
from scripts.eval_dataset import ROOT, Case, Dataset, merge_cases, read_dataset, write_json


class EvalWorkflowTests(unittest.TestCase):
    def test_seed_references_exist_in_source(self):
        dataset = read_dataset(ROOT / "eval/datasets/ecommerce_retrieval.json")
        self.assertGreaterEqual(len(dataset.cases), 5)
        for case in dataset.cases:
            source = (ROOT / case.source["document"]).read_text(encoding="utf-8")
            self.assertIn(case.ground_truth, source)
            self.assertTrue(case.ground_truth.strip())

    def test_export_paginates_and_deduplicates_per_kb(self):
        ids = [uuid4(), uuid4()]
        pages, traces = [], []
        def score(trace, value, name="user_feedback"):
            return {"traceId": trace, "name": name, "value": value, "comment": "片段无关"}

        def handle(request):
            if request.url.path == "/api/public/scores":
                params = request.url.params
                self.assertEqual(params["name"], "user_feedback")
                self.assertEqual(params["operator"], "<")
                self.assertIn("fromTimestamp", params)
                self.assertIn("toTimestamp", params)
                page = int(params["page"])
                pages.append(page)
                scores = ([score("a", 2), score("limit", 3), score("other", 1, "another_metric")]
                          if page == 1 else [score("a", 1), score("b", 1), score("gone", 2)])
                return httpx.Response(200, json={"data": scores, "meta": {"totalPages": 2}})
            trace = request.url.path.split("/")[-1]
            traces.append(trace)
            if trace == "gone":
                return httpx.Response(404)
            return httpx.Response(200, json={"name": "rag_retrieve",
                "metadata": {"kb_id": str(ids[trace == "b"]), "log_id": "log-" + trace},
                "input": {"query": " 同一问题 "} if trace == "a" else "同一问题"})
        with httpx.Client(base_url="http://test/", transport=httpx.MockTransport(handle)) as client:
            dataset = exporter.export_cases(client, 3.0, 30, None)
        self.assertEqual(pages, [1, 2])
        self.assertEqual(traces, ["a", "b", "gone"])
        self.assertIsNone(dataset.kb_id)
        self.assertEqual([case.kb_ids for case in dataset.cases], [[kb] for kb in ids])
        self.assertTrue(all(case.ground_truth == "" for case in dataset.cases))
        self.assertEqual(dataset.cases[0].source, {"trace_id": "a", "log_id": "log-a",
                         "feedback_score": 2, "feedback_comment": "片段无关"})

    def test_cross_kb_references_require_both_source_documents(self):
        dataset = read_dataset(ROOT / "eval/datasets/multi_kb_cases.json")
        for case in dataset.cases:
            self.assertEqual(len(case.scope()), 2)
            docs = [(ROOT / path).read_text(encoding="utf-8") for path in case.source["documents"]]
            facts = [fact for fact in case.ground_truth.split("。") if fact]
            self.assertEqual(len(facts), 2)
            for i, fact in enumerate(facts):
                self.assertIn(fact, docs[i])
                self.assertNotIn(fact, docs[1 - i])

    def test_joint_trace_export_filter_review_and_merge_preserve_ordered_scope(self):
        a, b = uuid4(), uuid4()
        scopes = {"joint": [a, b], "reverse": [b, a], "single": [b], "other": [a]}
        def handle(request):
            if request.url.path.endswith("/scores"):
                return httpx.Response(200, json={"meta": {"totalPages": 1}, "data": [
                    {"traceId": k, "name": "user_feedback", "value": 1} for k in scopes]})
            ids = scopes[request.url.path.split("/")[-1]]
            return httpx.Response(200, json={"input": "同题", "metadata": {
                "kb_id": str(a), "kb_ids": list(map(str, ids))}})
        with httpx.Client(base_url="http://test/", transport=httpx.MockTransport(handle)) as client:
            imported = exporter.export_cases(client, 3, None, b)
        self.assertEqual([c.kb_ids for c in imported.cases], [[a, b], [b, a], [b]])
        reviewed = Dataset(name="review", cases=[Case(kb_ids=[a, b], question="同题", ground_truth="双库依据")])
        exporter.apply_reviewed_references(imported, reviewed)
        self.assertEqual([c.ground_truth for c in imported.cases], ["双库依据", "", ""])
        merged, added = merge_cases(reviewed, imported)
        self.assertEqual(added, 2)
        self.assertEqual([c.kb_ids for c in merged.cases], [[a, b], [b, a], [b]])
        self.assertEqual(merge_cases(merged, imported)[1], 0)

    def test_live_merge_can_reuse_reviewed_references_for_matching_feedback_only(self):
        first, second = uuid4(), uuid4()
        live = Dataset(name="live", cases=[Case(kb_id=kb, question="Q") for kb in (first, second)])
        reviewed = Dataset(name="review", kb_id=first, cases=[Case(question="Q", ground_truth="已确认"),
                                                           Case(question="历史问题", ground_truth="早期标注")])
        exporter.apply_reviewed_references(live, reviewed)
        self.assertEqual([(c.kb_id, c.ground_truth) for c in live.cases], [(first, "已确认"), (second, "")])

    def test_reviewed_merge_preserves_annotations_across_reruns(self):
        kb = uuid4()
        target = Dataset(name="baseline", cases=[Case(question="甲", ground_truth="原标注"), Case(question="乙")])
        reviewed = Dataset(name="export", cases=[Case(question=q, ground_truth="新标注", kb_id=kb)
                                                  for q in ("甲", "乙", "丙")])
        reviewed.cases.append(Case(question="丁", ground_truth="其他库", kb_id=uuid4()))
        with self.assertRaisesRegex(ValueError, "--kb-id"):
            merge_cases(target, reviewed)
        with tempfile.TemporaryDirectory() as tmp:
            files = [Path(tmp) / name for name in ("main.json", "reviewed.json")]
            for path, dataset in zip(files, [target, reviewed]):
                write_json(path, dataset.model_dump(mode="json"))
            for _ in range(2):
                rc = exporter.main(["--merge", str(files[0]), "--input", str(files[1]), "--kb-id", str(kb)])
                self.assertEqual(rc, 0)
            result = read_dataset(files[0])
        self.assertEqual(result.kb_id, kb)
        self.assertEqual([(c.question, c.ground_truth) for c in result.cases],
                         [("甲", "原标注"), ("乙", "新标注"), ("丙", "新标注")])

    def test_skip_and_cli_error_do_not_call_judge_or_overwrite_a_good_report(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(runner, "make_judge") as judge:
            path, report = Path(tmp) / "cases.json", Path(tmp) / "report.json"
            dataset = Dataset(name="review", cases=[Case(question="退款？", ground_truth=" ")])
            write_json(path, dataset.model_dump(mode="json"))
            argv = ["--dataset", str(path), "--output", str(report), "--api-key", ""]
            with redirect_stdout(io.StringIO()) as out:
                self.assertEqual(runner.main(argv), 0)
            self.assertIn("skipped 1", out.getvalue())
            saved = report.read_bytes()
            self.assertIsNone(json.loads(saved)["summary"]["context_recall"])
            dataset.cases[0].ground_truth = "原文"
            write_json(path, dataset.model_dump(mode="json"))
            with redirect_stderr(io.StringIO()) as err:
                self.assertEqual(runner.main(argv), 1)
                self.assertEqual(runner.main([*argv, "--api-key", "test"]), 1)
            self.assertIn("--api-key", err.getvalue())
            self.assertIn("--kb-id", err.getvalue())
            self.assertEqual(saved, report.read_bytes())
            judge.assert_not_called()


class RetrieveContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_report_averages_annotated_cases_including_empty_retrievals(self):
        dataset = Dataset(name="comparison", kb_id=uuid4(), default_profile="speed", cases=[
            Case(id="match", question="甲", ground_truth="原文"),
            Case(id="miss", question="乙", ground_truth="另一篇原文"),
            Case(id="pending", question="丙"),
        ])
        args = SimpleNamespace(profile=None, kb_id=None, api_key="test", base_url="http://test")
        fetch = AsyncMock(side_effect=[["B", "A"], []])
        score = AsyncMock(return_value=(0.5, 1.0))
        with patch.object(runner, "retrieve", fetch), patch.object(runner, "Settings"), \
                patch.object(runner, "make_judge", return_value=("local", score)), redirect_stdout(io.StringIO()):
            report = await runner.run(dataset, args)
        self.assertEqual(report["summary"], {"total": 3, "evaluated": 2, "skipped": 1,
                                            "context_precision": 0.25, "context_recall": 0.5})
        self.assertEqual(report["low_recall"], ["miss"])
        self.assertTrue(all(row["multi_kb"] is False and row["kb_ids"] == [str(dataset.kb_id)] for row in report["cases"]))
        self.assertEqual([call.args[2:] for call in fetch.await_args_list], [("甲", "speed"), ("乙", "speed")])
        score.assert_awaited_once_with(dataset.cases[0], ["B", "A"])

    async def test_only_qps_is_retried_and_order_is_preserved(self):
        responses = iter([httpx.Response(429, json={"code": 20005}),
            httpx.Response(200, json={"code": 0, "data": {"retrieved_chunks": [{"content": "B"}, {"content": "A"}]}}),
            httpx.Response(429, json={"code": 20014}), httpx.Response(404, json={"code": 20009})])
        kb = uuid4()
        def handle(request):
            self.assertEqual(request.url.path, "/api/v1/rag/retrieve")
            self.assertEqual(json.loads(request.content), {"kb_ids": [str(kb)], "query": "Q", "profile": "speed",
                                                           "user_id": "eval_runner"})
            return next(responses)
        async with httpx.AsyncClient(base_url="http://test", transport=httpx.MockTransport(handle)) as client:
            with patch.object(runner.asyncio, "sleep", new_callable=AsyncMock) as sleep:
                self.assertEqual(await runner.retrieve(client, (kb,), "Q", "speed"), ["B", "A"])
                for message in ("配额", "知识库不存在"):
                    with self.assertRaisesRegex(ValueError, message):
                        await runner.retrieve(client, (kb,), "Q", "speed")
                sleep.assert_awaited_once_with(1)
