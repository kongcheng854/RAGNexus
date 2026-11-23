import asyncio
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
from pydantic import ValidationError

from scripts import run_eval
from scripts.eval_artifacts import raw_rows
from scripts.eval_capture import capture, preflight
from scripts.eval_dataset import ROOT, golden_suite, write_json
from scripts.eval_experiment import Experiment
from scripts.eval_runner import execute
from scripts.eval_score import report_run, score_run
from tests.test_eval_dataset import example_dataset


def config():
    return json.loads((ROOT / "eval/experiments/query_rewrite.json").read_text(encoding="utf-8"))


def tenant():
    return {"plan": "pro", "features": {"allowed_profiles": ["custom", "quality", "balanced", "speed"],
            "hybrid_allowed": True, "query_rewrite_allowed": True, "rerank_allowed": True},
            "limits": {"retrieve_daily": 1000, "retrieve_qps": 1000}, "usage": {"retrieve_daily_count": 0}}


def response(body):
    kb, query = body["kb_id"], body["query"]
    rewrite = body["query_options"]["enabled"]
    return {"query": query, "kb_id": kb, "kb_ids": [kb],
            "retrieved_chunks": [{"chunk_id": str(uuid4()), "document_id": str(uuid4()), "kb_id": kb, "title": "文档",
                                  "score": 0.8, "content": "原文", "metadata": {"heading_path": "规则/售后"}}],
            "metadata": {"log_id": str(uuid4()), "latency_ms": 9217, "top_k": 5, "vector_store": "pgvector",
                "tenant_policy": {"plan": "pro", "retrieve_profile": "custom", "effective_mode": "vector",
                                  "effective_rerank": False, "effective_query_rewrite": rewrite},
                "query_processing": {"raw_query": query, "effective_query": query, "search_query": query,
                                     "strategy": "rewrite" if rewrite else "noop"},
                "retrieval": {"mode": "vector", "vector_top_k": 10, "bm25_top_k": 0, "vector_count": 1,
                              "bm25_count": 0, "fused_count": 1},
                "rerank": {"enabled": False, "provider": "noop", "top_n": 5, "candidate_count": 0},
                "offline": {"langfuse_enabled": False, "synonym_enabled": False, "synonym_groups": 1,
                            "settings_refs": {kb: "hash"}, "application_model": "llm", "embedding_model": "emb",
                            "model_call_source": "test counter", "application_model_calls": int(rewrite),
                            "stage_timings_ms": {"retrieval": 9000}, "index": {"vector_store": "pgvector"}}}}


class ExperimentTests(unittest.TestCase):
    def test_declarations_and_named_presets(self):
        for edit in [lambda d: d.update(concurrency=0), lambda d: d.update(ragas_concurrency=-1),
                     lambda d: d["candidate"]["retrieval_options"].update(rrf_k=99),
                     lambda d: d["baseline"]["query_options"].pop("synonym_enabled"),
                     lambda d: d.update(third_group={}), lambda d: d["candidate"].update(api_key="key"),
                     lambda d: d.update(changed_fields=["rewrite_enabled"])]:
            data = config()
            edit(data)
            with self.subTest(edit=edit), self.assertRaises(ValidationError):
                Experiment.model_validate(data)
        opts = config() | {"target": "profile", "baseline": {"profile": "speed", "label": "A"},
                           "candidate": {"profile": "quality", "label": "B"},
                           "changed_fields": ["profile", "top_k", "retrieval_options.mode",
                                              "query_options.enabled", "rerank_options.enabled"]}
        exp = Experiment.model_validate(opts)
        self.assertEqual(exp.candidate.request_options(), {"profile": "quality"})
        opts["candidate"]["top_k"] = 8
        with self.assertRaises(ValidationError):
            Experiment.model_validate(opts)

    def test_missing_replay_artifacts_report_nonzero(self):
        with tempfile.TemporaryDirectory() as tmp, redirect_stderr(io.StringIO()) as err:
            for command in ("score", "report"):
                self.assertEqual(run_eval.main([command, "--run-dir", tmp]), 1)
            self.assertIn("manifest.json", err.getvalue())


class RunnerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        data = example_dataset()
        data["version"] = "two_kb_test"
        second = dict(data["cases"][0], id="second_case", question="第二个库？", kb_alias="second", suite="sample")
        data["knowledge_bases"]["second"] = {"kb_id": str(uuid4()), "corpus_version": "test_2"}
        data["cases"].append(second)
        data["suites"]["sample"] = {"description": "临时套件", "case_ids": [c["id"] for c in data["cases"][:4]] + [second["id"]]}
        self.cases = golden_suite(data, "sample").cases
        self.dataset_path = self.root / "input.json"
        self.path = self.root / "experiment.json"
        write_json(self.dataset_path, data)
        self.config = config() | {"dataset": str(self.dataset_path), "suite": "sample", "concurrency": 3, "ragas_concurrency": 2}
        self.events = []
        self.active = self.peak = self.judging = self.judge_peak = self.judge_calls = 0
        self.failure = False
        self.first_batch = None
        self.enterContext(patch.dict(os.environ, {"RAGNEXUS_API_KEY": "never-write-this-key"}))
        self.factory = self.enterContext(patch("scripts.eval_score.make_judge", return_value=("test-judge", self.judge)))

    async def judge(self, case, contexts):
        self.judging += 1
        self.judge_peak = max(self.judge_peak, self.judging)
        self.judge_calls += 1
        await asyncio.sleep(.005)
        self.judging -= 1
        self.assertEqual(contexts, ["原文"])
        return (.75, 1.)

    async def handle(self, request):
        if request.url.path.endswith("auth/me"):
            return httpx.Response(200, json={"code": 0, "data": tenant()})
        self.assertEqual(request.url.path, "/api/v1/rag/retrieve")
        self.assertEqual(request.headers["X-RAGNexus-Offline-Eval"], "true")
        body = json.loads(request.content)
        rewrite, question = body["query_options"]["enabled"], body["query"]
        self.events.append((rewrite, question, body["kb_id"]))
        self.active += 1
        self.peak = max(self.active, self.peak)
        if self.first_batch is not None:
            if self.active == self.config["concurrency"]:
                self.first_batch.set()
            await asyncio.wait_for(self.first_batch.wait(), 3)
        await asyncio.sleep(.02 if question == self.cases[0].question else .009)
        self.active -= 1
        if self.failure and rewrite and question == self.cases[2].question:
            return httpx.Response(429, json={"code": 20005})
        data = response(body)
        if self.failure and rewrite and question == self.cases[3].question:
            data["metadata"]["query_processing"].update(degraded=True, degraded_reason="模型超时")
            data["metadata"]["top_k"] = 4
        return httpx.Response(200, json={"code": 0, "data": data})

    async def execute(self):
        write_json(self.path, self.config)
        return await execute(self.path, self.root, "http://test", transport=httpx.MockTransport(self.handle))

    async def test_zero_warmup_bounded_concurrency_order_and_replay(self):
        self.first_batch = asyncio.Event()
        folder, ok = await self.execute()
        self.assertTrue(ok)
        queries = [(c.question, str(c.kb_id)) for c in self.cases]
        self.assertEqual(self.events, [(rewrite, *q) for rewrite in (False, True) for q in queries])
        self.assertEqual((self.peak, self.judge_peak), (3, 2))
        self.assertEqual(self.judge_calls, 10)
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["observability"]["server_langfuse_states"], [False])
        for group in ("baseline", "candidate"):
            rows = raw_rows(folder, group, self.cases, manifest["run_id"])
            self.assertEqual([r["case_id"] for r in rows], [c.id for c in self.cases])
            self.assertEqual(rows[0]["contexts"][0]["heading_path"], "规则/售后")
            self.assertEqual(rows[0]["contexts"][0]["rank"], 1)
        self.dataset_path.unlink()
        self.path.unlink()
        with patch.object(httpx.AsyncClient, "request", side_effect=AssertionError("unexpected HTTP")):
            self.assertTrue(await score_run(folder))
            self.assertEqual(self.judge_calls, 20)
            with patch("scripts.eval_score.make_judge", side_effect=AssertionError("unexpected judge")):
                self.assertTrue(report_run(folder))
        self.assertEqual(len(self.events), 10)
        for file in folder.glob("*"):
            self.assertNotIn(b"never-write-this-key", file.read_bytes())

    async def test_warmup_isolation_single_failure_and_late_config_drift(self):
        self.config["warmup_cases"] = 2
        self.failure = True
        folder, ok = await self.execute()
        self.assertFalse(ok)
        questions = [c.question for c in self.cases]
        self.assertEqual([e[:2] for e in self.events],
                         [(group, q) for group in (False, True) for q in questions[:2] + questions])
        self.assertEqual(self.judge_calls, 9)
        report = json.loads((folder / "comparison.json").read_text(encoding="utf-8"))
        self.assertEqual((report["candidate"]["total"], report["candidate"]["failed"], report["paired_cases"]), (5, 1, 4))
        self.assertEqual(report["status"], "证据不足")
        rows = raw_rows(folder, "candidate", self.cases, report["run_id"])
        self.assertIn("429", rows[2]["error"])
        self.assertEqual(rows[2]["runner_retries"], 0)
        self.assertIn("rewrite", rows[3]["degraded"])
        self.assertTrue(any("top_k" in i for i in rows[3]["config_issues"]))
        self.assertIn(self.cases[3].id, (folder / "report.md").read_text(encoding="utf-8"))
        with (folder / "candidate.raw.jsonl").open("w", encoding="utf-8") as stream:
            stream.write(json.dumps(rows[0]) + "\n")
        with self.assertRaisesRegex(ValueError, "1/5"):
            await score_run(folder)
        self.assertEqual(self.judge_calls, 9)

    async def test_preflight_quota_permission_and_empty_dictionary(self):
        info = tenant()
        async def handle(request):
            if request.url.path.endswith("me"):
                return httpx.Response(200, json={"code": 0, "data": info})
            return httpx.Response(200, json={"code": 0, "data": {"settings": {"synonyms": []}}})
        async with httpx.AsyncClient(base_url="http://test", transport=httpx.MockTransport(handle)) as client:
            exp = Experiment.model_validate(self.config | {"warmup_cases": 999})
            info["limits"]["retrieve_daily"] = 19
            with self.assertRaisesRegex(ValueError, "20"):
                await preflight(client, exp, self.cases)
            info["limits"]["retrieve_daily"] = 20
            info["features"]["query_rewrite_allowed"] = False
            with self.assertRaisesRegex(ValueError, "套餐"):
                await preflight(client, exp, self.cases)
            info["features"]["query_rewrite_allowed"] = True
            synonym = self.config | {"target": "synonym_expansion", "changed_fields": ["query_options.synonym_enabled"]}
            synonym["candidate"]["query_options"].update(enabled=False, synonym_enabled=True)
            with self.assertRaisesRegex(ValueError, "同义词表"):
                await preflight(client, Experiment.model_validate(synonym), self.cases)

    async def test_latency_starts_after_pacing_and_stops_before_parsing(self):
        clock = [0.]
        async def wait():
            clock[0] += 4
        async def post(url, **kwargs):
            clock[0] += .125
            return httpx.Response(200, json={"code": 0, "data": response(kwargs["json"])})
        with patch("scripts.eval_capture.perf_counter", side_effect=lambda: clock[0]):
            row = await capture(AsyncMock(post=post), AsyncMock(wait=wait), self.cases[0], "baseline",
                                Experiment.model_validate(self.config))
        self.assertEqual(row["latency_ms"], 125)
        self.assertEqual(row["server_latency_ms"], 9217)
